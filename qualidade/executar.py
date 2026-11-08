"""Etapa 2 - Qualidade de dados.

1. Profiling de todas as tabelas de origem e das fontes externas -> qualidade.profiling
2. Regras por dimensão (regras.py) sobre a origem             -> qualidade.metrica (fase = 'origem')
3. Limpeza com decisão justificada (limpeza.py)               -> schema stg + qualidade.quarentena + qualidade.decisao
4. Mesmas regras sobre a camada limpa                         -> qualidade.metrica (fase = 'stg')
5. Impacto nos indicadores de negócio (antes x depois)        -> qualidade.impacto

A origem (campo_real_energia) só é lida. Uso: python executar.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

import pandas as pd
from psycopg import sql

import banco
import limpeza as L
import regras as R
from banco import common, copiar, garantir, ler_df, recriar

# --------------------------------------------------------------------------- #
# Parâmetros das regras (limites de negócio, não credenciais)
# --------------------------------------------------------------------------- #
P = {
    "inicio_medicao": "2025-01-01 00:00",
    "fim_medicao": "2025-12-31 23:00",
    "limite_atraso_h": 48,          # SCADA normalmente entrega em ~1-2 h
    "travado_min_horas": 6,
    "max_horas_interpolacao": 6,
    "solar_inicio_dia": 5,
    "solar_fim_dia": 19,
    # Piso e teto do PLD horário (R$/MWh) homologados pela ANEEL para cada ano.
    # Conferir na resolução homologatória do ano antes de usar em produção.
    "pld_limites": {2025: (58.60, 1542.23), 2026: (57.31, 1611.04)},
    "clima_faixas": {"temperatura_c": (-10, 50), "radiacao_wm2": (0, 1400), "vento_10m_ms": (0, 60),
                     "vento_100m_ms": (0, 75), "precipitacao_mm": (0, 300), "nuvens_pct": (0, 100)},
    "clima_atraso_dias": 5,
}

# Caixas geográficas aproximadas (lat_min, lat_max, lon_min, lon_max) das UFs com usinas da empresa
CAIXAS_UF = {
    "PR": (-26.8, -22.5, -54.7, -48.0), "SC": (-29.4, -25.9, -53.9, -48.3), "RS": (-33.8, -27.0, -57.7, -49.6),
    "MG": (-22.95, -14.2, -51.1, -39.8), "SP": (-25.4, -19.7, -53.2, -44.1), "BA": (-18.4, -8.5, -46.7, -37.3),
    "RN": (-7.0, -4.8, -38.6, -34.9), "TO": (-13.5, -5.1, -50.8, -45.6), "MA": (-10.3, -1.0, -48.8, -41.8),
    "PE": (-9.5, -7.1, -41.4, -34.8),
}

ORIGEM = "campo_real_energia"


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
def carregar(conn) -> dict[str, pd.DataFrame]:
    d = {
        "submercado": ler_df(conn, f"SELECT * FROM {ORIGEM}.submercado"),
        "fonte_energia": ler_df(conn, f"SELECT * FROM {ORIGEM}.fonte_energia"),
        "usina": ler_df(conn, f"""SELECT u.*, f.cod_fonte FROM {ORIGEM}.usina u
                                  LEFT JOIN {ORIGEM}.fonte_energia f USING (id_fonte) ORDER BY u.id_usina"""),
        "feriado_nacional": ler_df(conn, f"SELECT * FROM {ORIGEM}.feriado_nacional"),
        "parada_usina": ler_df(conn, f"SELECT * FROM {ORIGEM}.parada_usina ORDER BY id_parada"),
        "medicao_geracao": ler_df(conn, f"SELECT * FROM {ORIGEM}.medicao_geracao"),
        "pld_horario": ler_df(conn, "SELECT data_hora, trim(cod_submercado) AS cod_submercado, pld FROM tratada.pld_horario"),
        "clima_horario": ler_df(conn, """SELECT cod_usina, data_hora, temperatura_c, precipitacao_mm, nuvens_pct,
                                                radiacao_wm2, vento_10m_ms, vento_100m_ms FROM tratada.clima_horario"""),
    }
    d["submercado"]["cod_submercado"] = d["submercado"]["cod_submercado"].str.strip()
    d["usina"]["cod_submercado"] = d["usina"]["cod_submercado"].str.strip()
    return d


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #
def perfil(df: pd.DataFrame, tabela: str) -> pd.DataFrame:
    linhas = []
    for c in df.columns:
        s = df[c]
        num = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
        linha = dict(tabela=tabela, coluna=c, tipo=str(s.dtype), linhas=len(s), nulos=int(s.isna().sum()),
                     pct_nulos=round(100 * s.isna().mean(), 4) if len(s) else 0.0, distintos=int(s.nunique(dropna=True)),
                     minimo=None, maximo=None, media=None, desvio=None, p25=None, p50=None, p75=None)
        try:
            if s.notna().any():
                linha["minimo"], linha["maximo"] = str(s.dropna().min()), str(s.dropna().max())
        except TypeError:
            pass
        if num and s.notna().any():
            q = s.quantile([0.25, 0.5, 0.75])
            linha.update(media=float(s.mean()), desvio=float(s.std()) if s.notna().sum() > 1 else None,
                         p25=float(q[0.25]), p50=float(q[0.5]), p75=float(q[0.75]))
        linha["mais_frequentes"] = "; ".join(f"{k} ({v})" for k, v in s.value_counts().head(3).items())
        linhas.append(linha)
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------- #
# Regras
# --------------------------------------------------------------------------- #
def regras_medicao(med: pd.DataFrame, usina: pd.DataFrame, parada: pd.DataFrame) -> list[R.Resultado]:
    validas = usina[usina["cod_usina"].str.fullmatch(L.PADRAO_COD_USINA)]
    pot = validas.set_index("id_usina")["potencia_mw"]
    ini, fim = pd.Timestamp(P["inicio_medicao"]), pd.Timestamp(P["fim_medicao"])
    no_periodo = med[med["data_hora"].between(ini, fim) & med["id_usina"].isin(set(validas["id_usina"]))]
    return [
        R.nulos(med, "medicao_geracao", ["id_usina", "data_hora", "energia_mwh"]),
        R.serie_horaria_completa(no_periodo, "medicao_geracao", "id_usina", "data_hora", ini, fim, validas["id_usina"]),
        R.duplicidade(med, "medicao_geracao", ["id_usina", "data_hora"]),
        R.faixa(med, "medicao_geracao", "energia_mwh", minimo=0, descricao="Energia negativa"),
        R.condicao(med, "medicao_geracao", "medicao_data_fora_do_periodo", R.VALIDADE,
                   f"data_hora fora de {ini:%Y-%m-%d} a {fim:%Y-%m-%d}", lambda d: ~d["data_hora"].between(ini, fim)),
        R.referencial(med, "medicao_geracao", "id_usina", validas["id_usina"], "usina"),
        R.acima_da_capacidade(med, pot),
        R.valor_travado(med, pot, P["travado_min_horas"]),
        R.geracao_sem_disponibilidade(med),
        R.geracao_noturna_solar(med, validas.loc[validas["cod_fonte"] == "UFV", "id_usina"], P["solar_fim_dia"], P["solar_inicio_dia"]),
        R.geracao_durante_parada(med, parada),
        R.ordem_temporal(med, "medicao_geracao", "data_hora", "recebido_em",
                         descricao="recebido_em anterior à hora medida"),
        R.atraso_recebimento(med, P["limite_atraso_h"]),
        R.ultimo_dado(no_periodo, "medicao_geracao", "id_usina", "data_hora", validas["id_usina"], fim),
    ]


def regras_cadastro(d: dict[str, pd.DataFrame]) -> list[R.Resultado]:
    u, p = d["usina"], d["parada_usina"]
    hoje = pd.Timestamp(date.today())
    chave_nome = lambda df: df["nome"].str.strip().str.upper() + "|" + df["latitude"].round(4).astype(str) + "|" + df["longitude"].round(4).astype(str)
    validas = u[u["cod_usina"].str.fullmatch(L.PADRAO_COD_USINA)]
    return [
        R.nulos(d["submercado"], "submercado", ["cod_submercado", "nome"]),
        R.duplicidade(d["submercado"], "submercado", ["cod_submercado"]),
        R.nulos(d["fonte_energia"], "fonte_energia", ["cod_fonte", "descricao"]),
        R.duplicidade(d["fonte_energia"], "fonte_energia", ["cod_fonte"]),
        R.nulos(u, "usina", ["cod_usina", "nome", "uf", "municipio", "latitude", "longitude", "potencia_mw", "data_inicio_operacao"]),
        R.padrao_texto(u, "usina", "cod_usina", L.PADRAO_COD_USINA),
        R.condicao(u, "usina", "usina_cadastrada_duas_vezes", R.UNICIDADE, "Mesmo nome normalizado e mesmas coordenadas",
                   lambda df: chave_nome(df).duplicated(keep="first")),
        R.faixa(u, "usina", "latitude", -34, 6, descricao="Latitude fora do território brasileiro"),
        R.faixa(u, "usina", "longitude", -74, -34, descricao="Longitude fora do território brasileiro"),
        R.coordenada_na_uf(u, CAIXAS_UF),
        R.condicao(u, "usina", "usina_inicio_operacao_futuro", R.VALIDADE, "data_inicio_operacao depois de hoje",
                   lambda df: pd.to_datetime(df["data_inicio_operacao"]) > hoje),
        R.faixa(u, "usina", "potencia_mw", minimo=0.01, descricao="Potência instalada não positiva"),
        R.referencial(u, "usina", "cod_submercado", d["submercado"]["cod_submercado"], "submercado"),
        R.referencial(u, "usina", "id_fonte", d["fonte_energia"]["id_fonte"], "fonte_energia"),
        R.nulos(p, "parada_usina", ["id_usina", "inicio", "fim", "tipo", "motivo"]),
        R.ordem_temporal(p, "parada_usina", "inicio", "fim", descricao="fim anterior ao início"),
        R.referencial(p, "parada_usina", "id_usina", validas["id_usina"], "usina"),
        R.condicao(p, "parada_usina", "parada_tipo_invalido", R.VALIDADE, "tipo diferente de Programada/Forçada",
                   lambda df: ~df["tipo"].isin(["Programada", "Forçada"])),
    ]


def regras_externas(d: dict[str, pd.DataFrame]) -> list[R.Resultado]:
    pld, clima = d["pld_horario"], d["clima_horario"]
    subs = d["submercado"]["cod_submercado"]
    validas = d["usina"][d["usina"]["cod_usina"].str.fullmatch(L.PADRAO_COD_USINA)]["cod_usina"]
    limites = P["pld_limites"]

    def fora_limite(df):
        ano = df["data_hora"].dt.year
        piso = ano.map(lambda a: limites.get(a, (None, None))[0])
        teto = ano.map(lambda a: limites.get(a, (None, None))[1])
        return piso.isna() | (df["pld"] < piso - 1e-6) | (df["pld"] > teto + 1e-6)

    anos_pld = sorted(pld["data_hora"].dt.year.unique())
    anos_feriado = set(pd.to_datetime(d["feriado_nacional"]["data"]).dt.year)
    faltam = pd.DataFrame({"ano_sem_feriados": [a for a in anos_pld if a not in anos_feriado]})
    fim_clima = pd.Timestamp(date.today() - timedelta(days=P["clima_atraso_dias"]))

    resultados = [
        R.serie_horaria_completa(pld, "pld_horario", "cod_submercado", "data_hora",
                                 pld["data_hora"].min(), pld["data_hora"].max(), subs),
        R.duplicidade(pld, "pld_horario", ["data_hora", "cod_submercado"]),
        R.condicao(pld, "pld_horario", "pld_fora_piso_teto", R.VALIDADE, "PLD fora do piso/teto regulatório do ano", fora_limite),
        R.referencial(pld, "pld_horario", "cod_submercado", subs, "submercado"),
        R.ultimo_dado(pld, "pld_horario", "cod_submercado", "data_hora", subs, pd.Timestamp(date.today())),
        R.serie_horaria_completa(clima, "clima_horario", "cod_usina", "data_hora",
                                 P["inicio_medicao"], clima["data_hora"].max(), validas),
        R.nulos(clima, "clima_horario", list(P["clima_faixas"])),
        R.referencial(clima, "clima_horario", "cod_usina", validas, "usina"),
        R.ultimo_dado(clima, "clima_horario", "cod_usina", "data_hora", validas, fim_clima, tolerancia_horas=48),
        R.Resultado("feriado_anos_sem_cobertura", R.COMPLETUDE, "feriado_nacional",
                    "Anos com PLD/medição sem feriados cadastrados (dimensão de tempo incompleta)", len(anos_pld), faltam),
    ]
    for col, (mn, mx) in P["clima_faixas"].items():
        resultados.append(R.faixa(clima, "clima_horario", col, mn, mx, dimensao=R.ACURACIA,
                                  descricao=f"{col} fisicamente implausível (fora de [{mn}, {mx}])"))
    return resultados


def metricas(resultados: list[R.Resultado], fase: str, id_execucao: int) -> pd.DataFrame:
    agora = datetime.now()
    return pd.DataFrame([dict(id_execucao=id_execucao, executado_em=agora, fase=fase, regra=r.regra, dimensao=r.dimensao,
                              tabela=r.tabela, descricao=r.descricao, avaliados=r.avaliados, falhas=r.n_falhas,
                              pct_falhas=r.pct, pct_conformidade=round(100 - r.pct, 4)) for r in resultados])


# --------------------------------------------------------------------------- #
# Impacto antes x depois
# --------------------------------------------------------------------------- #
def receita(med: pd.DataFrame, usina: pd.DataFrame, pld: pd.DataFrame) -> pd.DataFrame:
    m = med.merge(usina[["id_usina", "cod_submercado", "cod_fonte"]], on="id_usina", how="inner")
    m = m.merge(pld, on=["data_hora", "cod_submercado"], how="inner")
    m["receita"] = m["energia_mwh"] * m["pld"]
    return m


def impacto(antes: pd.DataFrame, depois: pd.DataFrame) -> pd.DataFrame:
    linhas = []

    def add(indicador, recorte, a, b):
        linhas.append(dict(indicador=indicador, recorte=recorte, antes=round(a, 2), depois=round(b, 2),
                           diferenca=round(b - a, 2), variacao_pct=round(100 * (b - a) / a, 4) if a else None))

    for recorte, fa, fb in [("Total", antes, depois)] + [
            (f, antes[antes["cod_fonte"] == f], depois[depois["cod_fonte"] == f]) for f in sorted(depois["cod_fonte"].unique())]:
        ga, gb = fa["energia_mwh"].sum(), fb["energia_mwh"].sum()
        ra, rb = fa["receita"].sum(), fb["receita"].sum()
        add("Geração (MWh)", recorte, ga, gb)
        add("Receita valorada ao PLD (R$)", recorte, ra, rb)
        add("Preço capturado (R$/MWh)", recorte, ra / ga if ga else 0, rb / gb if gb else 0)
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------- #
DDL = {
    "profiling": """id_execucao BIGINT, tabela TEXT, coluna TEXT, tipo TEXT, linhas INT, nulos INT, pct_nulos NUMERIC(9,4),
                    distintos INT, minimo TEXT, maximo TEXT, media DOUBLE PRECISION, desvio DOUBLE PRECISION,
                    p25 DOUBLE PRECISION, p50 DOUBLE PRECISION, p75 DOUBLE PRECISION, mais_frequentes TEXT""",
    "metrica": """id_execucao BIGINT NOT NULL, executado_em TIMESTAMP NOT NULL, fase VARCHAR(10) NOT NULL,
                  regra VARCHAR(80) NOT NULL, dimensao VARCHAR(30) NOT NULL, tabela VARCHAR(40) NOT NULL, descricao TEXT,
                  avaliados INT NOT NULL, falhas INT NOT NULL, pct_falhas NUMERIC(9,4), pct_conformidade NUMERIC(9,4),
                  PRIMARY KEY (id_execucao, fase, regra)""",
    "quarentena": "id_execucao BIGINT, tabela TEXT, chave TEXT, regra TEXT, motivo TEXT, registro JSONB",
    "decisao": "id_execucao BIGINT, tabela TEXT, problema TEXT, dimensao TEXT, registros INT, decisao TEXT, justificativa TEXT",
    "impacto": """id_execucao BIGINT, indicador TEXT, recorte TEXT, antes NUMERIC(18,2), depois NUMERIC(18,2),
                  diferenca NUMERIC(18,2), variacao_pct NUMERIC(12,4)""",
}

DDL_STG = {
    "usina": """id_usina INT PRIMARY KEY, cod_usina VARCHAR(12) NOT NULL UNIQUE, nome VARCHAR(80) NOT NULL, id_fonte SMALLINT,
                cod_fonte VARCHAR(10), cod_submercado VARCHAR(2), uf CHAR(2), municipio VARCHAR(60), latitude NUMERIC(9,6),
                longitude NUMERIC(9,6), potencia_mw NUMERIC(8,2), data_inicio_operacao DATE, situacao VARCHAR(20),
                flag_qualidade TEXT""",
    "parada_usina": """id_parada INT PRIMARY KEY, id_usina INT NOT NULL, inicio TIMESTAMP NOT NULL, fim TIMESTAMP NOT NULL,
                       tipo VARCHAR(20), motivo VARCHAR(120), flag_qualidade TEXT""",
    "medicao_geracao": """id_medicao BIGINT, id_usina INT NOT NULL, data_hora TIMESTAMP NOT NULL, energia_mwh NUMERIC(12,3),
                          disponibilidade_mw NUMERIC(8,2), origem VARCHAR(20), recebido_em TIMESTAMP,
                          status_qualidade VARCHAR(12) NOT NULL, motivo TEXT, PRIMARY KEY (id_usina, data_hora)""",
    "feriado": "data DATE PRIMARY KEY, descricao VARCHAR(60), tipo VARCHAR(20), origem TEXT",
}


def main() -> int:
    common.configurar_log()
    common.carregar_env()
    cfg = common.carregar_config()
    log = common.log

    with common.conectar() as conn, common.execucao(cfg, "qualidade", "profiling, regras, limpeza e impacto") as ex:
        d = carregar(conn)
        ex.lidas = sum(len(v) for v in d.values())
        log.info("dados carregados: %s", {k: len(v) for k, v in d.items()})

        tab = {k: garantir(conn, "qualidade", k, v) for k, v in DDL.items() if k in ("profiling", "metrica")}
        tab.update({k: recriar(conn, "qualidade", k, v) for k, v in DDL.items() if k not in ("profiling", "metrica")})

        # 1) profiling
        perf = pd.concat([perfil(df, nome).astype(object) for nome, df in d.items()], ignore_index=True)
        copiar(conn, tab["profiling"], perf.assign(id_execucao=ex.id)[["id_execucao", *perf.columns]])

        # 2) regras na origem
        res_origem = regras_cadastro(d) + regras_medicao(d["medicao_geracao"], d["usina"], d["parada_usina"]) + regras_externas(d)
        copiar(conn, tab["metrica"], metricas(res_origem, "origem", ex.id))
        for r in res_origem:
            if r.n_falhas:
                log.info("  %-45s %-24s %6d / %-7d (%.3f%%)", r.regra, r.dimensao, r.n_falhas, r.avaliados, r.pct)

        # 3) limpeza
        usina_l, reg_u = L.limpar_usina(d["usina"], CAIXAS_UF, date.today())
        med_l, reg_m = L.limpar_medicao(d["medicao_geracao"], usina_l, P["inicio_medicao"], P["fim_medicao"], P)
        parada_l, reg_p = L.limpar_parada(d["parada_usina"], med_l)
        anos = sorted(set(d["pld_horario"]["data_hora"].dt.year) | {2025})
        feriado_l, reg_f = L.completar_feriados(d["feriado_nacional"], anos)
        registros = [reg_u, reg_p, reg_m, reg_f]

        usina_l["nome"] = usina_l["nome"].str.strip()
        med_l["id_usina"] = med_l["id_usina"].astype(int)
        stg = {
            "usina": usina_l[["id_usina", "cod_usina", "nome", "id_fonte", "cod_fonte", "cod_submercado", "uf", "municipio",
                              "latitude", "longitude", "potencia_mw", "data_inicio_operacao", "situacao", "flag_qualidade"]],
            "parada_usina": parada_l[["id_parada", "id_usina", "inicio", "fim", "tipo", "motivo", "flag_qualidade"]],
            "medicao_geracao": med_l[["id_medicao", "id_usina", "data_hora", "energia_mwh", "disponibilidade_mw", "origem",
                                      "recebido_em", "status_qualidade", "motivo"]],
            "feriado": feriado_l[["data", "descricao", "tipo", "origem"]],
        }
        for nome, df in stg.items():
            ex.gravadas += copiar(conn, recriar(conn, "stg", nome, DDL_STG[nome]), df)

        quar = pd.concat([r.quarentena_df() for r in registros], ignore_index=True)
        copiar(conn, tab["quarentena"], quar.assign(id_execucao=ex.id)[["id_execucao", *quar.columns]])
        dec = pd.DataFrame([x for r in registros for x in r.decisoes])
        copiar(conn, tab["decisao"], dec.assign(id_execucao=ex.id)[["id_execucao", *dec.columns]])

        # 4) regras na camada limpa
        res_stg = regras_medicao(med_l, usina_l, parada_l)
        copiar(conn, tab["metrica"], metricas(res_stg, "stg", ex.id))

        # 5) impacto
        antes = receita(d["medicao_geracao"], d["usina"], d["pld_horario"])
        depois = receita(med_l, usina_l, d["pld_horario"])
        imp = impacto(antes, depois)
        copiar(conn, tab["impacto"], imp.assign(id_execucao=ex.id)[["id_execucao", *imp.columns]])
        conn.commit()

        log.info("decisões: %s | quarentena: %s registros", len(dec), len(quar))
        total = imp[imp["recorte"] == "Total"].set_index("indicador")
        for ind, r in total.iterrows():
            log.info("  %-30s antes=%16.2f depois=%16.2f (%+.4f%%)", ind, r["antes"], r["depois"], r["variacao_pct"] or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
