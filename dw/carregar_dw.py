"""Etapa 3 - cria e carrega o star schema (schema dw).

Executa, numa única transação, 01_ddl.sql, 02_carga.sql e 03_views.sql e confere os grãos.
Uso:  python carregar_dw.py [--recriar]   (--recriar apaga o schema dw antes, perdendo o histórico SCD2)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA.parent / "extração"))

import common  # noqa: E402

ARQUIVOS = ["01_ddl.sql", "02_carga.sql", "03_views.sql"]

CHECAGENS = {
    "fato_geracao: grão usina x hora sem duplicata":
        "SELECT COUNT(*) - COUNT(DISTINCT (sk_tempo, sk_usina)) FROM dw.fato_geracao",
    "fato_pld: grão submercado x hora sem duplicata":
        "SELECT COUNT(*) - COUNT(DISTINCT (sk_tempo, sk_submercado)) FROM dw.fato_pld",
    "fatos apontando para membro desconhecido":
        """SELECT (SELECT COUNT(*) FROM dw.fato_geracao WHERE -1 IN (sk_tempo, sk_usina, sk_fonte, sk_submercado))
                + (SELECT COUNT(*) FROM dw.fato_pld WHERE -1 IN (sk_tempo, sk_submercado))
                + (SELECT COUNT(*) FROM dw.fato_parada_hora WHERE -1 IN (sk_tempo, sk_usina, sk_fonte, sk_submercado))""",
    "geração sem PLD da hora":
        "SELECT COUNT(*) FROM dw.fato_geracao WHERE pld IS NULL",
    "usinas com mais de uma versão atual":
        "SELECT COUNT(*) - COUNT(DISTINCT cod_usina) FROM dw.dim_usina WHERE versao_atual",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recriar", action="store_true", help="DROP SCHEMA dw CASCADE antes de criar")
    args = ap.parse_args()

    common.configurar_log()
    common.carregar_env()
    cfg = common.carregar_config()
    log = common.log

    with common.conectar() as conn, common.execucao(cfg, "dw", "ddl + carga + views") as ex:
        if args.recriar:
            conn.execute("DROP SCHEMA IF EXISTS dw CASCADE")
        for nome in ARQUIVOS:
            log.info("executando %s", nome)
            conn.execute((PASTA / nome).read_text(encoding="utf-8"))
        conn.commit()

        for tabela in ("dim_tempo", "dim_submercado", "dim_fonte", "dim_usina",
                       "fato_geracao", "fato_pld", "fato_parada_hora", "fato_qualidade", "fato_impacto"):
            n = conn.execute(f"SELECT COUNT(*) FROM dw.{tabela}").fetchone()[0]
            ex.gravadas += n
            log.info("  dw.%-18s %8d linhas", tabela, n)
        for descricao, consulta in CHECAGENS.items():
            n = conn.execute(consulta).fetchone()[0]
            if n:
                ex.avisos.append(f"{descricao}: {n}")
            log.info("  [%s] %s: %s", "OK" if n == 0 else "!!", descricao, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
