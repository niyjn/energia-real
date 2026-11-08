"""Utilitários compartilhados pelos extratores.

- leitura do .env (credenciais) e do config.ini (parâmetros)
- conexão com o PostgreSQL
- GET com timeout, nova tentativa com espera crescente, tratamento de 429/5xx/resposta vazia
  e integração com o motor de navegador (sabidez.py) quando a fonte bloqueia clientes HTTP puros (HTTP 403)
- registro de cada execução na tabela raw.log_extracao
"""
from __future__ import annotations

import atexit
import configparser
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import psycopg
import requests
from psycopg import sql
from psycopg.conninfo import make_conninfo

import sabidez
from sabidez import HttpStatusError

PASTA = Path(__file__).resolve().parent
log = logging.getLogger("extracao")
_IDENTIFICADOR = re.compile(r"^[a-z_][a-z0-9_]*$")


class ErroConfig(Exception):
    """Configuração ausente ou inválida."""


class ErroFonte(Exception):
    """Falha definitiva ao falar com a fonte (tentar de novo não adianta)."""


class _ErroTemporario(Exception):
    """Falha que vale a pena repetir (timeout, 429, 5xx, resposta vazia)."""


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
def configurar_log(nivel: int = logging.INFO) -> None:
    logging.basicConfig(level=nivel, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")


def carregar_env() -> Path | None:
    """Procura um .env na pasta do código e nas pastas acima; não sobrescreve variáveis já definidas."""
    for pasta in (PASTA, *PASTA.parents):
        arquivo = pasta / ".env"
        if not arquivo.is_file():
            continue
        for linha in arquivo.read_text(encoding="utf-8-sig").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))
        return arquivo
    return None


def carregar_config(caminho: str | Path | None = None) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
    cfg.optionxform = str  # mantém maiúsculas/minúsculas das chaves (ex.: códigos de usina)
    caminho = Path(caminho) if caminho else PASTA / "config.ini"
    if not cfg.read(caminho, encoding="utf-8"):
        raise ErroConfig(f"Arquivo de configuração não encontrado: {caminho}")
    return cfg


def lista(cfg: configparser.ConfigParser, secao: str, chave: str) -> list[str]:
    """Lê 'a, b, c' do config como lista de strings sem vazios."""
    return [x.strip() for x in cfg.get(secao, chave, fallback="").split(",") if x.strip()]


def schema(cfg: configparser.ConfigParser, chave: str) -> str:
    """Nome do schema ('raw', 'tratada' ou 'origem') validado contra injeção de SQL."""
    nome = cfg.get("banco", f"schema_{chave}")
    if not _IDENTIFICADOR.match(nome):
        raise ErroConfig(f"Nome de schema inválido em [banco] schema_{chave}: {nome!r}")
    return nome


def tabela(cfg: configparser.ConfigParser, chave: str, nome: str) -> sql.Identifier:
    return sql.Identifier(schema(cfg, chave), nome)


# --------------------------------------------------------------------------- #
# Banco
# --------------------------------------------------------------------------- #
def _conninfo() -> str:
    """Monta a conexão a partir de DB_URL (aceita prefixo jdbc: e postgresql://).

    Usuário e senha podem vir na URL, em ?user=...&password=... ou nas variáveis
    DB_USER / DB_PASSWORD do .env.
    """
    url = os.environ.get("DB_URL", "").strip()
    if not url:
        raise ErroConfig("Variável DB_URL não encontrada (defina no arquivo .env).")
    url = re.sub(r"^jdbc:", "", url)
    p = urlparse(url)
    if not p.hostname:
        raise ErroConfig("DB_URL inválida: esperado jdbc:postgresql://host:porta/banco")
    consulta = parse_qs(p.query)

    def pegar(nome_url: str, nome_env: str, da_url: str | None) -> str | None:
        return da_url or (consulta.get(nome_url) or [None])[0] or os.environ.get(nome_env)

    usuario = pegar("user", "DB_USER", unquote(p.username) if p.username else None)
    senha = pegar("password", "DB_PASSWORD", unquote(p.password) if p.password else None)
    return make_conninfo(
        host=p.hostname,
        port=p.port or 5432,
        dbname=p.path.lstrip("/") or None,
        user=usuario,
        password=senha,
        sslmode=(consulta.get("sslmode") or ["require"])[0],
        connect_timeout=15,
    )


def conectar(autocommit: bool = False) -> psycopg.Connection:
    # prepare_threshold=None: compatível com pooler (pgbouncer) do Neon
    return psycopg.connect(_conninfo(), autocommit=autocommit, prepare_threshold=None)


_DDL_LOG = """
CREATE TABLE IF NOT EXISTS {log} (
    id_execucao     BIGINT       GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    fonte           VARCHAR(30)  NOT NULL,
    recurso         VARCHAR(120),
    inicio          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    fim             TIMESTAMPTZ,
    linhas_lidas    INTEGER      NOT NULL DEFAULT 0,
    linhas_gravadas INTEGER      NOT NULL DEFAULT 0,
    status          VARCHAR(20)  NOT NULL,   -- EXECUTANDO | OK | PARCIAL | ERRO
    erro            TEXT
)
"""


def garantir_estrutura(conn: psycopg.Connection, cfg: configparser.ConfigParser) -> None:
    """Cria os schemas raw/tratada e a tabela de log, se ainda não existirem."""
    for chave in ("raw", "tratada"):
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema(cfg, chave))))
    conn.execute(sql.SQL(_DDL_LOG).format(log=tabela(cfg, "raw", "log_extracao")))
    conn.commit()


# --------------------------------------------------------------------------- #
# Log de execuções (conexão própria e autocommit: o log sobrevive a rollback)
# --------------------------------------------------------------------------- #
class Execucao:
    def __init__(self, id_execucao: int):
        self.id = id_execucao
        self.lidas = 0
        self.gravadas = 0
        self.avisos: list[str] = []


@contextmanager
def execucao(cfg: configparser.ConfigParser, fonte: str, recurso: str):
    """Registra início/fim, linhas e erro. Status: OK, PARCIAL (com avisos) ou ERRO."""
    log_tab = tabela(cfg, "raw", "log_extracao")
    with conectar(autocommit=True) as c:
        id_exec = c.execute(
            sql.SQL("INSERT INTO {} (fonte, recurso, status) VALUES (%s, %s, 'EXECUTANDO') RETURNING id_execucao").format(log_tab),
            (fonte, recurso),
        ).fetchone()[0]
    ex = Execucao(id_exec)
    log.info("[%s] %s: iniciada (execução %s)", fonte, recurso, id_exec)
    status, erro = "ERRO", None
    try:
        yield ex
        status = "PARCIAL" if ex.avisos else "OK"
        erro = "; ".join(ex.avisos)[:2000] or None
    except BaseException as e:  # inclui Ctrl+C: o log precisa registrar a interrupção
        erro = f"{type(e).__name__}: {e}"[:2000]
        raise
    finally:
        with conectar(autocommit=True) as c:
            c.execute(
                sql.SQL(
                    "UPDATE {} SET fim = now(), linhas_lidas = %s, linhas_gravadas = %s, status = %s, erro = %s "
                    "WHERE id_execucao = %s"
                ).format(log_tab),
                (ex.lidas, ex.gravadas, status, erro, id_exec),
            )
        log.info("[%s] %s: %s (lidas=%s, gravadas=%s)%s", fonte, recurso, status, ex.lidas, ex.gravadas,
                 f" - {erro}" if erro else "")


# --------------------------------------------------------------------------- #
# HTTP + integração com o motor de navegador (sabidez.py)
# --------------------------------------------------------------------------- #
# função refatorada para guardar atributos do driver de navegador na sessão
def nova_sessao(cfg: configparser.ConfigParser) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = cfg.get("http", "user_agent", fallback="CampoRealEnergia-DW/1.0 (trabalho academico)")
    s._sabidez_driver = None  # type: ignore[attr-defined]
    s._sabidez_dominios: set[str] = set()  # type: ignore[attr-defined]
    return s


# função adicionada para inicializar sob demanda o driver do sabidez e fechá-lo ao sair
def _obter_driver_navegador(sessao: requests.Session, cfg: configparser.ConfigParser) -> sabidez.Driver:
    drv = getattr(sessao, "_sabidez_driver", None)
    if drv is None:
        h = cfg["http"]
        cliente = h.get("cliente", "auto").strip().lower()
        motor = cliente if cliente in ("selenium", "playwright") else h.get("motor_navegador", "auto").strip().lower()
        browser = h.get("browser", "auto").strip().lower()
        headless = h.getboolean("headless", True)
        log.info("iniciando navegador via sabidez (motor=%s, browser=%s, headless=%s)", motor, browser, headless)
        drv = sabidez.build_driver(motor, headless=headless, browser=browser)
        sessao._sabidez_driver = drv  # type: ignore[attr-defined]
        atexit.register(lambda d=drv: _fechar_driver_silencioso(d))
    return drv


# função adicionada para encerrar o driver do navegador sem levantar erro no encerramento
def _fechar_driver_silencioso(drv: sabidez.Driver) -> None:
    try:
        drv.close()
    except Exception:
        pass


# função adicionada para executar get_json pelo navegador traduzindo erros para o fluxo de retry
def _get_json_navegador(sessao: requests.Session, url: str, params: dict, timeout: float,
                        espera: float, cfg: configparser.ConfigParser) -> tuple[dict, float]:
    drv = _obter_driver_navegador(sessao, cfg)
    try:
        dados = drv.get_json(url, params=params, timeout=timeout)
        return dados, espera
    except HttpStatusError as e:
        if e.status == 429 or e.status >= 500:
            if e.retry_after.isdigit():
                espera = max(espera, int(e.retry_after))
            raise _ErroTemporario(f"HTTP {e.status}") from None
        raise ErroFonte(str(e)) from None
    except ValueError as e:
        raise _ErroTemporario(f"resposta inválida no navegador: {e}") from None
    except Exception as e:
        raise _ErroTemporario(f"erro no navegador: {e}") from None


# função refatorada para usar o navegador do sabidez quando o requests recebe http 403
def get_json(sessao: requests.Session, url: str, params: dict, cfg: configparser.ConfigParser) -> dict:
    """GET que devolve JSON. Repete com espera crescente em timeout, erro de rede, 429, 5xx,
    resposta vazia ou JSON inválido. Se a fonte devolver 403 no cliente HTTP puro e [http] cliente
    for 'auto' (ou 'selenium'/'playwright'), delega a requisição ao motor de navegador (sabidez.py)."""
    h = cfg["http"]
    timeout = h.getfloat("timeout", 60)
    tentativas = h.getint("tentativas", 5)
    espera_base = h.getfloat("espera_base", 2.0)
    pausa = h.getfloat("pausa_entre_requisicoes", 0.5)
    cliente = h.get("cliente", "auto").strip().lower()
    dominio = urlparse(url).netloc
    dominios_nav: set[str] = getattr(sessao, "_sabidez_dominios", set())

    motivo = ""
    for n in range(1, tentativas + 1):
        espera = espera_base * 2 ** (n - 1)
        try:
            usar_nav = cliente in ("selenium", "playwright") or dominio in dominios_nav
            if usar_nav:
                dados, espera = _get_json_navegador(sessao, url, params, timeout, espera, cfg)
                time.sleep(pausa)
                return dados

            r = sessao.get(url, params=params, timeout=timeout)
            if r.status_code == 403 and cliente == "auto":
                log.warning("HTTP 403 em %s via requests; alternando domínio %s para o motor sabidez", url, dominio)
                dominios_nav.add(dominio)
                sessao._sabidez_dominios = dominios_nav  # type: ignore[attr-defined]
                dados, espera = _get_json_navegador(sessao, url, params, timeout, espera, cfg)
                time.sleep(pausa)
                return dados

            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    espera = max(espera, int(retry_after))
                raise _ErroTemporario(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise ErroFonte(f"HTTP {r.status_code} em {url}: {r.text[:200]!r}")
            if not r.content.strip():
                raise _ErroTemporario("resposta vazia")
            try:
                dados = r.json()
            except ValueError:
                raise _ErroTemporario("resposta não é JSON válido") from None
            time.sleep(pausa)  # respeita o limite de requisições da fonte
            return dados
        except (requests.Timeout, requests.ConnectionError, _ErroTemporario) as e:
            motivo = f"{type(e).__name__}: {e}"
            if n == tentativas:
                break
            log.warning("tentativa %s/%s falhou (%s); nova tentativa em %.0fs", n, tentativas, motivo, espera)
            time.sleep(espera)
    raise ErroFonte(f"falhou após {tentativas} tentativas ({motivo}) em {url}")
