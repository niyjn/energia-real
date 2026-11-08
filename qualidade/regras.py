"""Regras de qualidade reutilizáveis.

Cada regra recebe um DataFrame (e parâmetros) e devolve um `Resultado` com a dimensão de qualidade,
quantos registros foram avaliados e quais falharam. Nenhuma regra altera dados: elas só medem.
As decisões (corrigir, descartar, quarentena, sinalizar) ficam em limpeza.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
import pandas as pd

COMPLETUDE = "Completude"
UNICIDADE = "Unicidade"
VALIDADE = "Validade"
CONSISTENCIA = "Consistência"
ACURACIA = "Acurácia"
INTEGRIDADE = "Integridade referencial"
ATUALIDADE = "Atualidade"
DIMENSOES = (COMPLETUDE, UNICIDADE, VALIDADE, CONSISTENCIA, ACURACIA, INTEGRIDADE, ATUALIDADE)


@dataclass
class Resultado:
    regra: str
    dimensao: str
    tabela: str
    descricao: str
    avaliados: int
    falhas: pd.DataFrame

    @property
    def n_falhas(self) -> int:
        return len(self.falhas)

    @property
    def pct(self) -> float:
        return round(100 * self.n_falhas / self.avaliados, 4) if self.avaliados else 0.0


def _vazio(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT or (isinstance(v, str) and not v.strip())


# --------------------------------------------------------------------------- #
# Genéricas
# --------------------------------------------------------------------------- #
def condicao(df: pd.DataFrame, tabela: str, regra: str, dimensao: str, descricao: str,
             mascara: Callable[[pd.DataFrame], pd.Series]) -> Resultado:
    """Regra genérica: falha onde `mascara(df)` for verdadeira."""
    m = mascara(df).fillna(False).astype(bool)
    return Resultado(regra, dimensao, tabela, descricao, len(df), df[m])


def nulos(df: pd.DataFrame, tabela: str, colunas: list[str], regra: str | None = None) -> Resultado:
    """Completude: campos obrigatórios nulos ou com texto vazio."""
    m = df[colunas].map(_vazio).any(axis=1)
    return Resultado(regra or f"{tabela}_campos_obrigatorios", COMPLETUDE, tabela,
                     f"Nulo ou vazio em: {', '.join(colunas)}", len(df), df[m])


def serie_horaria_completa(df: pd.DataFrame, tabela: str, chave: str, col_tempo: str, inicio, fim,
                           entidades: Iterable | None = None, regra: str | None = None) -> Resultado:
    """Completude: toda entidade deve ter uma linha por hora entre inicio e fim."""
    horas = pd.date_range(inicio, fim, freq="h")
    ents = list(entidades) if entidades is not None else sorted(df[chave].dropna().unique())
    esperado = pd.MultiIndex.from_product([ents, horas], names=[chave, col_tempo])
    presente = pd.MultiIndex.from_frame(df[[chave, col_tempo]].dropna().drop_duplicates())
    faltando = esperado.difference(presente).to_frame(index=False)
    return Resultado(regra or f"{tabela}_horas_faltantes", COMPLETUDE, tabela,
                     f"Horas sem registro entre {inicio} e {fim} por {chave}", len(esperado), faltando)


def duplicidade(df: pd.DataFrame, tabela: str, chave: list[str], regra: str | None = None,
                descricao: str | None = None) -> Resultado:
    """Unicidade: registros excedentes com a mesma chave (o primeiro não conta como falha)."""
    m = df.duplicated(subset=chave, keep="first")
    return Resultado(regra or f"{tabela}_duplicidade", UNICIDADE, tabela,
                     descricao or f"Mais de um registro por ({', '.join(chave)})", len(df), df[m])


def faixa(df: pd.DataFrame, tabela: str, coluna: str, minimo=None, maximo=None, regra: str | None = None,
          dimensao: str = VALIDADE, descricao: str | None = None) -> Resultado:
    """Validade: valor fora de [minimo, maximo]. Nulos não são avaliados aqui (ver `nulos`)."""
    s = df[coluna]
    m = pd.Series(False, index=df.index)
    if minimo is not None:
        m |= s < minimo
    if maximo is not None:
        m |= s > maximo
    return Resultado(regra or f"{tabela}_{coluna}_faixa", dimensao, tabela,
                     descricao or f"{coluna} fora de [{minimo}, {maximo}]", int(s.notna().sum()), df[m])


def padrao_texto(df: pd.DataFrame, tabela: str, coluna: str, padrao: str, regra: str | None = None) -> Resultado:
    """Validade: texto fora do formato esperado (regex completa)."""
    m = ~df[coluna].astype(str).str.fullmatch(padrao)
    return Resultado(regra or f"{tabela}_{coluna}_formato", VALIDADE, tabela,
                     f"{coluna} fora do padrão {padrao}", len(df), df[m])


def ordem_temporal(df: pd.DataFrame, tabela: str, col_antes: str, col_depois: str, regra: str | None = None,
                   descricao: str | None = None) -> Resultado:
    """Consistência entre colunas: col_depois não pode ser anterior a col_antes."""
    m = df[col_depois] < df[col_antes]
    return Resultado(regra or f"{tabela}_{col_depois}_antes_de_{col_antes}", CONSISTENCIA, tabela,
                     descricao or f"{col_depois} anterior a {col_antes}", len(df), df[m])


def referencial(df: pd.DataFrame, tabela: str, coluna: str, validos: Iterable, tabela_ref: str,
                regra: str | None = None) -> Resultado:
    """Integridade referencial: valor (inclusive nulo) que não existe no cadastro de referência."""
    m = ~df[coluna].isin(set(validos))
    return Resultado(regra or f"{tabela}_{coluna}_sem_cadastro", INTEGRIDADE, tabela,
                     f"{coluna} sem correspondência em {tabela_ref}", len(df), df[m])


# --------------------------------------------------------------------------- #
# Específicas do domínio (geração de energia)
# --------------------------------------------------------------------------- #
def coordenada_na_uf(usinas: pd.DataFrame, caixas: dict[str, tuple[float, float, float, float]],
                     tabela: str = "usina") -> Resultado:
    """Consistência com o mundo físico: lat/lon precisam cair dentro da caixa geográfica da UF declarada."""
    def fora(r) -> bool:
        cx = caixas.get(r["uf"])
        if cx is None:
            return True
        lat_min, lat_max, lon_min, lon_max = cx
        return not (lat_min <= r["latitude"] <= lat_max and lon_min <= r["longitude"] <= lon_max)
    m = usinas.apply(fora, axis=1)
    return Resultado("usina_coordenada_fora_da_uf", CONSISTENCIA, tabela,
                     "Coordenadas fora da caixa geográfica da UF declarada", len(usinas), usinas[m])


def acima_da_capacidade(med: pd.DataFrame, potencia: pd.Series, tolerancia: float = 1.0) -> Resultado:
    """Acurácia (física): energia de 1 hora não pode passar de potência instalada x 1 h."""
    limite = med["id_usina"].map(potencia) * tolerancia
    m = med["energia_mwh"] > limite
    return Resultado("medicao_acima_da_potencia", ACURACIA, "medicao_geracao",
                     "Energia horária maior que a potência instalada (MW x 1 h)", int(med["energia_mwh"].notna().sum()), med[m])


def geracao_sem_disponibilidade(med: pd.DataFrame) -> Resultado:
    """Consistência entre colunas: energia gerada maior que a potência disponível na hora."""
    m = med["energia_mwh"] > med["disponibilidade_mw"] + 1e-9
    return Resultado("medicao_energia_maior_que_disponibilidade", CONSISTENCIA, "medicao_geracao",
                     "energia_mwh > disponibilidade_mw na mesma hora", int(med["energia_mwh"].notna().sum()), med[m])


def geracao_noturna_solar(med: pd.DataFrame, ids_solar: Iterable[int], hora_fim_dia: int = 19,
                          hora_inicio_dia: int = 5) -> Resultado:
    """Consistência física: usina solar não gera à noite."""
    solar = med[med["id_usina"].isin(set(ids_solar))]
    h = solar["data_hora"].dt.hour
    m = ((h > hora_fim_dia) | (h < hora_inicio_dia)) & (solar["energia_mwh"] > 0)
    return Resultado("medicao_solar_gerando_a_noite", CONSISTENCIA, "medicao_geracao",
                     f"Solar com energia > 0 antes das {hora_inicio_dia}h ou depois das {hora_fim_dia}h",
                     len(solar), solar[m])


def geracao_durante_parada(med: pd.DataFrame, paradas: pd.DataFrame) -> Resultado:
    """Consistência entre tabelas: usina gerando durante parada registrada em parada_usina."""
    m = pd.Series(False, index=med.index)
    validas = paradas[paradas["fim"] >= paradas["inicio"]]
    for p in validas.itertuples():
        m |= (med["id_usina"] == p.id_usina) & med["data_hora"].between(p.inicio, p.fim) & (med["energia_mwh"] > 0)
    return Resultado("medicao_gerando_durante_parada", CONSISTENCIA, "medicao_geracao",
                     "Energia > 0 em hora coberta por parada (parada_usina)", len(med), med[m])


def valor_travado(med: pd.DataFrame, potencia: pd.Series, min_horas: int = 6, limite_clipping: float = 0.97) -> Resultado:
    """Acurácia: mesmo valor não nulo repetido por >= min_horas horas seguidas (medidor travado).

    Valores perto da potência máxima (>= limite_clipping x potência) são ignorados: operar no limite por
    várias horas é fisicamente plausível (clipping de inversor, turbina a plena carga)."""
    d = med.dropna(subset=["energia_mwh"]).sort_values(["id_usina", "data_hora"])
    novo_bloco = (
        d["id_usina"].ne(d["id_usina"].shift())
        | d["energia_mwh"].ne(d["energia_mwh"].shift())
        | d["data_hora"].diff().ne(pd.Timedelta(hours=1))
    )
    bloco = novo_bloco.cumsum()
    tamanho = d.groupby(bloco)["energia_mwh"].transform("size")
    perto_do_maximo = d["energia_mwh"] >= d["id_usina"].map(potencia) * limite_clipping
    m = (tamanho >= min_horas) & (d["energia_mwh"] > 0) & ~perto_do_maximo
    return Resultado("medicao_valor_travado", ACURACIA, "medicao_geracao",
                     f"Mesmo valor > 0 por {min_horas}+ horas seguidas", len(d), d[m])


def atraso_recebimento(med: pd.DataFrame, limite_horas: float) -> Resultado:
    """Atualidade: dado chegou ao banco muito depois da hora medida."""
    atraso = (med["recebido_em"] - med["data_hora"]).dt.total_seconds() / 3600
    m = atraso > limite_horas
    return Resultado("medicao_recebida_com_atraso", ATUALIDADE, "medicao_geracao",
                     f"recebido_em mais de {limite_horas} h depois de data_hora", int(med["recebido_em"].notna().sum()), med[m])


def ultimo_dado(df: pd.DataFrame, tabela: str, chave: str, col_tempo: str, entidades: Iterable, fim_periodo,
                tolerancia_horas: float = 24) -> Resultado:
    """Atualidade: entidade que parou de enviar dados antes do fim do período."""
    ultimo = df.groupby(chave)[col_tempo].max()
    ents = list(entidades)
    ultimos = pd.DataFrame({chave: ents, "ultimo_registro": [ultimo.get(e, pd.NaT) for e in ents]})
    corte = pd.Timestamp(fim_periodo) - pd.Timedelta(hours=tolerancia_horas)
    m = ultimos["ultimo_registro"].isna() | (ultimos["ultimo_registro"] < corte)
    return Resultado(f"{tabela}_parou_de_enviar", ATUALIDADE, tabela,
                     f"Último registro mais de {tolerancia_horas} h antes do fim do período", len(ultimos), ultimos[m])
