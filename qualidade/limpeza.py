"""Decisões de limpeza: corrigir, descartar, quarentena ou sinalizar.

A origem nunca é alterada. Cada função recebe os dados brutos e devolve:
  - o DataFrame limpo (vai para o schema stg),
  - os registros retirados (vão para qualidade.quarentena, com o motivo),
  - a lista de decisões tomadas, com quantidade e justificativa (vai para qualidade.decisao).

Regra geral adotada: só corrigimos quando existe evidência independente do valor certo
(outra coluna, outra tabela, a física da usina ou a unidade). Sem evidência, sinalizamos ou descartamos.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

import regras as R

CORRIGIR, DESCARTAR, QUARENTENA, SINALIZAR = "Corrigir", "Descartar", "Quarentena", "Sinalizar"

# Correções de cadastro decididas pela equipe após conferir o município da usina.
# Cada uma só é aplicada se a regra de consistência continuar falhando para a usina.
CORRECOES_USINA = {
    "CRE-UFV-004": ({"latitude": -15.803},
                    "Latitude com sinal trocado: Janaúba/MG fica no hemisfério sul (-15,80); município, UF e longitude confirmam."),
    "CRE-BIO-005": ({"uf": "SP"},
                    "UF digitada errada: Sertãozinho fica em SP; coordenadas (-21,14; -47,99) e submercado SE confirmam."),
}

PADRAO_COD_USINA = r"CRE-[A-Z]{3}-\d{3}"


class Registro:
    """Acumula quarentena e decisões de uma tabela."""

    def __init__(self, tabela: str):
        self.tabela = tabela
        self.quarentena: list[pd.DataFrame] = []
        self.decisoes: list[dict] = []

    def decidir(self, problema: str, dimensao: str, registros: int, decisao: str, justificativa: str):
        if registros:
            self.decisoes.append(dict(tabela=self.tabela, problema=problema, dimensao=dimensao,
                                      registros=int(registros), decisao=decisao, justificativa=justificativa))

    def isolar(self, linhas: pd.DataFrame, chave_cols: list[str], regra: str, motivo: str):
        if linhas.empty:
            return
        q = pd.DataFrame({
            "tabela": self.tabela,
            "chave": linhas[chave_cols].astype(str).agg(" | ".join, axis=1),
            "regra": regra,
            "motivo": motivo,
            "registro": linhas.apply(lambda r: json.dumps({k: (None if pd.isna(v) else str(v)) for k, v in r.items()},
                                                          ensure_ascii=False), axis=1),
        })
        self.quarentena.append(q)

    def quarentena_df(self) -> pd.DataFrame:
        return pd.concat(self.quarentena, ignore_index=True) if self.quarentena else pd.DataFrame(
            columns=["tabela", "chave", "regra", "motivo", "registro"])


# --------------------------------------------------------------------------- #
# usina
# --------------------------------------------------------------------------- #
def limpar_usina(usina: pd.DataFrame, caixas_uf: dict, hoje: date) -> tuple[pd.DataFrame, Registro]:
    reg = Registro("usina")
    u = usina.copy()
    u["flag_qualidade"] = ""

    def flag(mask, texto):
        u.loc[mask, "flag_qualidade"] = (u.loc[mask, "flag_qualidade"] + "; " + texto).str.lstrip("; ")

    # 1) código fora do padrão: duplicata de uma usina válida (mesmo nome normalizado e mesmas coordenadas)?
    validas = u[u["cod_usina"].str.fullmatch(PADRAO_COD_USINA)]
    chave_dup = lambda df: df["nome"].str.strip().str.upper() + "|" + df["latitude"].round(4).astype(str) + "|" + df["longitude"].round(4).astype(str)
    invalidas = u[~u["cod_usina"].str.fullmatch(PADRAO_COD_USINA)]
    dup = invalidas[chave_dup(invalidas).isin(set(chave_dup(validas)))]
    resto = invalidas.drop(dup.index)
    reg.isolar(dup, ["id_usina", "cod_usina"], "usina_cod_usina_formato",
               "Duplicata de usina já cadastrada (mesmo nome normalizado e mesmas coordenadas)")
    reg.decidir("Usina cadastrada duas vezes (código fora do padrão, nome com espaço, município vazio)",
                R.UNICIDADE, len(dup), DESCARTAR,
                "Mesmo nome e coordenadas de uma usina válida; nenhuma medição aponta para o id duplicado.")
    reg.isolar(resto, ["id_usina", "cod_usina"], "usina_cod_usina_formato", "Código fora do padrão sem usina correspondente")
    reg.decidir("Código de usina fora do padrão sem correspondente", R.VALIDADE, len(resto), QUARENTENA,
                "Não há como saber a qual usina pertence.")
    u = u.drop(dup.index).drop(resto.index)

    # 2) coordenadas inconsistentes com a UF: aplica correção documentada, se houver
    fora = R.coordenada_na_uf(u, caixas_uf).falhas
    for idx, linha in fora.iterrows():
        correcao = CORRECOES_USINA.get(linha["cod_usina"])
        if correcao:
            campos, justificativa = correcao
            for campo, valor in campos.items():
                u.loc[idx, campo] = valor
            flag(u.index == idx, f"corrigido: {', '.join(campos)}")
            problema = "Latitude com sinal trocado" if "latitude" in campos else "UF diferente da localização"
            reg.decidir(f"{problema} ({linha['cod_usina']})", R.CONSISTENCIA if "uf" in campos else R.VALIDADE,
                        1, CORRIGIR, justificativa)
        else:
            flag(u.index == idx, "coordenada fora da UF")
            reg.decidir(f"Coordenada fora da UF ({linha['cod_usina']})", R.CONSISTENCIA, 1, SINALIZAR,
                        "Sem evidência do valor correto.")

    # 3) início de operação no futuro: valor desconhecido (não inventamos uma data)
    futuro = pd.to_datetime(u["data_inicio_operacao"]) > pd.Timestamp(hoje)
    reg.decidir("Data de início de operação no futuro", R.VALIDADE, int(futuro.sum()), SINALIZAR,
                "A usina tem medições em 2025, então a data está errada, mas não há fonte para a correta: "
                "o campo fica nulo (desconhecido) e a usina é marcada.")
    flag(futuro, "data_inicio_operacao inválida (anulada)")
    u.loc[futuro, "data_inicio_operacao"] = None
    return u.reset_index(drop=True), reg


# --------------------------------------------------------------------------- #
# parada_usina
# --------------------------------------------------------------------------- #
def limpar_parada(parada: pd.DataFrame, med: pd.DataFrame) -> tuple[pd.DataFrame, Registro]:
    reg = Registro("parada_usina")
    p = parada.copy()
    p["flag_qualidade"] = ""
    invertidas = p[p["fim"] < p["inicio"]]
    for idx, linha in invertidas.iterrows():
        ini, fim = linha["fim"], linha["inicio"]  # período com as pontas trocadas
        janela = med[(med["id_usina"] == linha["id_usina"]) & med["data_hora"].between(ini, fim)]
        if len(janela) and (janela["energia_mwh"] > 0).mean() > 0.5:
            reg.isolar(p.loc[[idx]], ["id_parada", "id_usina"], "parada_usina_fim_antes_de_inicio",
                       "fim < inicio e a medição mostra geração normal no período: parada não aconteceu como lançada")
            reg.decidir("Parada com fim antes do início, sem efeito na medição", R.CONSISTENCIA, 1, QUARENTENA,
                        "Trocar início e fim daria uma parada de 10 h, mas o SCADA registra geração normal nessas "
                        "horas; o lançamento manual não é confiável para estimar perdas.")
            p = p.drop(idx)
        else:
            p.loc[idx, ["inicio", "fim"]] = [ini, fim]
            p.loc[idx, "flag_qualidade"] = "corrigido: início e fim trocados"
            reg.decidir("Parada com início e fim trocados", R.CONSISTENCIA, 1, CORRIGIR,
                        "A medição está zerada no período invertido, confirmando a parada.")
    return p.reset_index(drop=True), reg


# --------------------------------------------------------------------------- #
# medicao_geracao
# --------------------------------------------------------------------------- #
def _preencher_lacunas_curtas(d: pd.DataFrame, max_horas: int) -> pd.Series:
    """Interpola linearmente blocos de energia nula com até max_horas, cercados por valores válidos
    em horas contíguas. Devolve a máscara do que foi preenchido."""
    preenchido = pd.Series(False, index=d.index)
    for _, g in d.groupby("id_usina"):
        g = g.sort_values("data_hora")
        s = g.set_index("data_hora")["energia_mwh"]
        cheio = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="h"))
        nulo = cheio.isna()
        bloco = (nulo != nulo.shift()).cumsum()
        tamanho = nulo.groupby(bloco).transform("size")
        interp = cheio.interpolate(method="time", limit_area="inside")
        aceitar = nulo & (tamanho <= max_horas) & interp.notna()
        alvo = aceitar[aceitar].index.intersection(s.index)
        if len(alvo):
            idx = g.index[g["data_hora"].isin(alvo)]
            d.loc[idx, "energia_mwh"] = interp.loc[g.loc[idx, "data_hora"]].round(3).values
            preenchido.loc[idx] = True
    return preenchido


def limpar_medicao(med: pd.DataFrame, usinas: pd.DataFrame, inicio, fim, p: dict) -> tuple[pd.DataFrame, Registro]:
    reg = Registro("medicao_geracao")
    chave = ["id_medicao", "id_usina", "data_hora"]
    d = med.copy()
    for c in ("corrigido", "imputado", "sinalizado"):
        d[c] = False
    d["motivo"] = ""

    def marcar(mask, coluna, texto):
        d.loc[mask, coluna] = True
        d.loc[mask, "motivo"] = (d.loc[mask, "motivo"] + "; " + texto).str.lstrip("; ")

    # 1) sem usina no cadastro (nulo ou id inexistente)
    sem = ~d["id_usina"].isin(set(usinas["id_usina"]))
    nulo = d["id_usina"].isna()
    reg.isolar(d[sem & nulo], chave, "medicao_id_usina_sem_cadastro", "id_usina nulo")
    reg.isolar(d[sem & ~nulo], chave, "medicao_id_usina_sem_cadastro", "id_usina inexistente no cadastro")
    reg.decidir("Medição sem usina (id_usina nulo)", R.INTEGRIDADE, int((sem & nulo).sum()), QUARENTENA,
                "Não é possível atribuir a geração a nenhuma usina nem a um submercado.")
    reg.decidir("Medições de equipamento fora do cadastro (id_usina inexistente)", R.INTEGRIDADE,
                int((sem & ~nulo).sum()), QUARENTENA,
                "Valores idênticos aos de outra usina no mesmo dia: provável cópia; aguardam o cadastro ser revisto.")
    d = d[~sem]

    # 2) fora do período medido (ex.: ano digitado errado)
    fora = ~d["data_hora"].between(pd.Timestamp(inicio), pd.Timestamp(fim))
    reg.isolar(d[fora], chave, "medicao_data_fora_do_periodo", "data_hora fora do período de medição")
    reg.decidir("Data fora do período (ano digitado errado)", R.VALIDADE, int(fora.sum()), QUARENTENA,
                "A hora provável (pelo recebido_em) já tem leitura do SCADA; corrigir o ano criaria uma duplicata.")
    d = d[~fora]

    # 3) duplicidade: prioridade SCADA, depois o primeiro que chegou
    d = d.assign(_prio=(d["origem"] != "SCADA").astype(int)).sort_values(["id_usina", "data_hora", "_prio", "recebido_em"])
    dup = d.duplicated(["id_usina", "data_hora"], keep="first")
    primeiro = d[~dup].set_index(["id_usina", "data_hora"])["energia_mwh"]
    extra = d[dup]
    igual = extra.set_index(["id_usina", "data_hora"])["energia_mwh"].values == primeiro.loc[
        list(zip(extra["id_usina"], extra["data_hora"]))].values if len(extra) else np.array([], bool)
    reg.isolar(extra[igual], chave, "medicao_duplicidade", "cópia idêntica de reprocessamento")
    reg.isolar(extra[~igual], chave, "medicao_duplicidade", "lançamento manual conflitante com leitura SCADA")
    reg.decidir("Reprocessamento duplicou um dia inteiro", R.UNICIDADE, int(igual.sum()), DESCARTAR,
                "Cópias idênticas chegaram 1 dia depois; mantida a primeira recepção.")
    reg.decidir("Lançamento manual por cima da leitura automática (+10%)", R.UNICIDADE, int((~igual).sum()), QUARENTENA,
                "O SCADA é a fonte primária; o manual chegou 2 dias depois sem justificativa. Fica isolado para revisão.")
    d = d[~dup].drop(columns="_prio").copy()

    pot = d["id_usina"].map(usinas.set_index("id_usina")["potencia_mw"])
    fonte = d["id_usina"].map(usinas.set_index("id_usina")["cod_fonte"])

    # 4) planilha em kWh: valor impossível que vira plausível dividido por 1000
    kwh = (d["origem"] == "MANUAL") & (d["energia_mwh"] > pot) & (d["energia_mwh"] / 1000 <= pot)
    reg.decidir("Planilha digitada em kWh", R.ACURACIA, int(kwh.sum()), CORRIGIR,
                "Origem MANUAL e valores ~1000x acima da potência; divididos por 1000 ficam no padrão da usina.")
    d.loc[kwh, "energia_mwh"] = (d.loc[kwh, "energia_mwh"] / 1000).round(3)
    marcar(kwh, "corrigido", "kWh convertido para MWh")

    # 5) negativa = consumo dos serviços auxiliares, não geração
    neg = d["energia_mwh"] < 0
    reg.decidir("Energia negativa (consumo auxiliar registrado como geração)", R.VALIDADE, int(neg.sum()), CORRIGIR,
                "Geração não pode ser negativa: a usina estava consumindo, ou seja, gerou 0 nessas horas.")
    d.loc[neg, "energia_mwh"] = 0.0
    marcar(neg, "corrigido", "negativo -> 0")

    # 6) solar à noite
    h = d["data_hora"].dt.hour
    noite = (fonte == "UFV") & ((h > p["solar_fim_dia"]) | (h < p["solar_inicio_dia"])) & (d["energia_mwh"] > 0)
    reg.decidir("Solar gerando à noite (relógio do medidor)", R.CONSISTENCIA, int(noite.sum()), CORRIGIR,
                "Não há irradiação depois das 19 h; a geração física é 0.")
    d.loc[noite, "energia_mwh"] = 0.0
    marcar(noite, "corrigido", "solar noturna -> 0")

    # 7) geração com disponibilidade zero (usina em parada)
    indisp = (d["disponibilidade_mw"] == 0) & (d["energia_mwh"] > 0)
    reg.decidir("Geração lançada durante manutenção (disponibilidade 0)", R.CONSISTENCIA, int(indisp.sum()), CORRIGIR,
                "A própria linha informa disponibilidade 0 e a parada programada está em parada_usina.")
    d.loc[indisp, "energia_mwh"] = 0.0
    marcar(indisp, "corrigido", "gerando sem disponibilidade -> 0")

    # 8) acima da potência: fisicamente impossível -> descarta o valor (vai para imputação)
    acima = d["energia_mwh"] > pot
    reg.decidir("Energia acima da potência instalada (vírgula deslocada, pico de leitura)", R.ACURACIA,
                int(acima.sum()), CORRIGIR,
                "Valor impossível descartado e substituído por interpolação das horas vizinhas (lacuna de 1 h).")
    d.loc[acima, "energia_mwh"] = np.nan
    marcar(acima, "corrigido", "acima da potência descartado")

    # 9) lacunas curtas de energia nula -> interpolação
    nulos_antes = d["energia_mwh"].isna() & ~acima
    preench = _preencher_lacunas_curtas(d, p["max_horas_interpolacao"])
    reg.decidir("Energia nula (falha de comunicação)", R.COMPLETUDE, int((preench & nulos_antes).sum()), CORRIGIR,
                f"Lacuna de até {p['max_horas_interpolacao']} h interpolada linearmente; marcada como IMPUTADO.")
    marcar(preench, "imputado", "interpolado")
    ainda_nulo = d["energia_mwh"].isna()
    reg.decidir("Energia nula sem vizinhos para interpolar", R.COMPLETUDE, int(ainda_nulo.sum()), SINALIZAR,
                "Lacuna longa: mantida nula para não inventar geração.")
    marcar(ainda_nulo, "sinalizado", "energia nula")

    # 10) medidor travado: valor plausível, mas suspeito -> sinaliza
    trav = R.valor_travado(d, usinas.set_index("id_usina")["potencia_mw"], p["travado_min_horas"]).falhas.index
    reg.decidir("Medidor travado (mesmo valor por horas)", R.ACURACIA, len(trav), SINALIZAR,
                "O valor é plausível para a usina e não há leitura alternativa; mantido e marcado.")
    marcar(d.index.isin(trav), "sinalizado", "valor travado")

    # 11) atualidade e relógio
    atraso = (d["recebido_em"] - d["data_hora"]).dt.total_seconds() / 3600 > p["limite_atraso_h"]
    reg.decidir("Integração atrasada (dado chegou semanas depois)", R.ATUALIDADE, int(atraso.sum()), SINALIZAR,
                "O valor está correto; o atraso importa para a operação, não para o faturamento.")
    marcar(atraso, "sinalizado", "recebido com atraso")
    antes = d["recebido_em"] < d["data_hora"]
    reg.decidir("Recebido antes de ter sido medido (relógio do servidor)", R.CONSISTENCIA, int(antes.sum()), SINALIZAR,
                "Erro no carimbo de recepção, não na energia; o valor é mantido.")
    marcar(antes, "sinalizado", "recebido_em anterior à medição")

    d["status_qualidade"] = np.select([d["imputado"], d["corrigido"], d["sinalizado"]],
                                      ["IMPUTADO", "CORRIGIDO", "SINALIZADO"], "ORIGINAL")
    return d.reset_index(drop=True), reg


# --------------------------------------------------------------------------- #
# feriados: completa os anos que a origem não cobre
# --------------------------------------------------------------------------- #
def _pascoa(ano: int) -> date:
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def feriados_nacionais(ano: int) -> list[tuple[date, str, str]]:
    p = _pascoa(ano)
    return [
        (date(ano, 1, 1), "Confraternização Universal", "Feriado"),
        (p - timedelta(days=48), "Carnaval", "Ponto facultativo"),
        (p - timedelta(days=47), "Carnaval", "Ponto facultativo"),
        (p - timedelta(days=2), "Sexta-feira Santa", "Feriado"),
        (date(ano, 4, 21), "Tiradentes", "Feriado"),
        (date(ano, 5, 1), "Dia do Trabalho", "Feriado"),
        (p + timedelta(days=60), "Corpus Christi", "Ponto facultativo"),
        (date(ano, 9, 7), "Independência do Brasil", "Feriado"),
        (date(ano, 10, 12), "Nossa Senhora Aparecida", "Feriado"),
        (date(ano, 11, 2), "Finados", "Feriado"),
        (date(ano, 11, 15), "Proclamação da República", "Feriado"),
        (date(ano, 11, 20), "Dia da Consciência Negra", "Feriado"),
        (date(ano, 12, 25), "Natal", "Feriado"),
    ]


def completar_feriados(feriado: pd.DataFrame, anos: list[int]) -> tuple[pd.DataFrame, Registro]:
    reg = Registro("feriado_nacional")
    f = feriado.assign(origem="campo_real_energia.feriado_nacional")
    presentes = set(pd.to_datetime(f["data"]).dt.year)
    novos = [(d, desc, tipo, "calculado (regras do calendário nacional)")
             for ano in anos if ano not in presentes for d, desc, tipo in feriados_nacionais(ano)]
    if novos:
        f = pd.concat([f, pd.DataFrame(novos, columns=["data", "descricao", "tipo", "origem"])], ignore_index=True)
        reg.decidir(f"Feriados ausentes para {sorted({d.year for d, *_ in novos})}", R.COMPLETUDE, len(novos), CORRIGIR,
                    "O PLD cobre 2026, mas a origem só tem 2025. Feriados nacionais são calculados por regra "
                    "(datas fixas + móveis a partir da Páscoa); a coluna origem identifica os calculados.")
    return f, reg
