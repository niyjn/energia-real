"""Gera o projeto Power BI (.pbip) com o modelo semântico completo: tabelas do schema dw, relacionamentos e medidas DAX.

O host do banco NÃO fica no arquivo: ele é um parâmetro (ServidorPG) a preencher no Power BI.
Uso: python gerar_pbip.py   ->  painel/energia_real.pbip (abrir no Power BI Desktop)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
NOME = "energia_real"
MODELO = RAIZ / f"{NOME}.SemanticModel"
RELATORIO = RAIZ / f"{NOME}.Report"

# (coluna, tipo, opções)  tipos: i=int64, s=string, d=decimal, t=dateTime, dt=date, b=boolean
T = {
    "dim_tempo": [
        ("sk_tempo", "i", "hidden"), ("data_hora", "t", ""), ("data", "dt", ""), ("ano", "i", ""), ("trimestre", "i", ""),
        ("mes", "i", ""), ("nome_mes", "s", "sort:mes"), ("ano_mes", "s", ""), ("dia", "i", ""), ("dia_semana", "i", ""),
        ("nome_dia_semana", "s", "sort:dia_semana"), ("hora", "i", ""), ("fim_de_semana", "b", ""), ("feriado", "b", ""),
        ("ponto_facultativo", "b", ""), ("nome_feriado", "s", ""), ("dia_util", "b", ""), ("estacao", "s", ""),
        ("periodo_hidrologico", "s", ""), ("patamar_carga", "s", ""), ("horario_ponta", "b", ""),
    ],
    "dim_submercado": [("sk_submercado", "i", "hidden"), ("cod_submercado", "s", ""), ("nome", "s", ""), ("regioes", "s", "")],
    "dim_fonte": [("sk_fonte", "i", "hidden"), ("cod_fonte", "s", ""), ("descricao", "s", ""), ("renovavel", "b", ""), ("intermitente", "b", "")],
    "dim_usina": [
        ("sk_usina", "i", "hidden"), ("cod_usina", "s", ""), ("nome", "s", ""), ("cod_fonte", "s", ""), ("cod_submercado", "s", ""),
        ("uf", "s", ""), ("municipio", "s", ""), ("latitude", "d", ""), ("longitude", "d", ""), ("potencia_mw", "d", ""),
        ("data_inicio_operacao", "dt", ""), ("situacao", "s", ""), ("flag_qualidade", "s", ""), ("hash_scd2", "s", "hidden"),
        ("valido_de", "t", ""), ("valido_ate", "t", ""), ("versao_atual", "b", ""),
    ],
    "fato_pld": [("sk_tempo", "i", "hidden"), ("sk_submercado", "i", "hidden"), ("pld", "d", "")],
    "fato_geracao": [
        ("sk_tempo", "i", "hidden"), ("sk_usina", "i", "hidden"), ("sk_fonte", "i", "hidden"), ("sk_submercado", "i", "hidden"),
        ("energia_mwh", "d", ""), ("disponibilidade_mw", "d", ""), ("potencia_mw", "d", ""), ("pld", "d", ""), ("receita_pld", "d", ""),
        ("em_parada", "b", ""), ("status_qualidade", "s", ""), ("radiacao_wm2", "d", ""), ("vento_10m_ms", "d", ""),
        ("vento_100m_ms", "d", ""), ("temperatura_c", "d", ""), ("precipitacao_mm", "d", ""), ("nuvens_pct", "d", ""),
    ],
    "fato_parada_hora": [
        ("sk_tempo", "i", "hidden"), ("sk_usina", "i", "hidden"), ("sk_fonte", "i", "hidden"), ("sk_submercado", "i", "hidden"),
        ("id_parada", "i", ""), ("tipo_parada", "s", ""), ("motivo", "s", ""), ("energia_esperada_mwh", "d", ""), ("pld", "d", ""),
        ("receita_perdida_rs", "d", ""), ("metodo_estimativa", "s", ""),
    ],
    "fato_qualidade": [
        ("id_execucao", "i", ""), ("executado_em", "t", ""), ("fase", "s", ""), ("regra", "s", ""), ("dimensao", "s", ""),
        ("tabela", "s", ""), ("descricao", "s", ""), ("avaliados", "i", ""), ("falhas", "i", ""), ("pct_conformidade", "d", ""),
    ],
    "fato_impacto": [
        ("id_execucao", "i", ""), ("indicador", "s", ""), ("recorte", "s", ""), ("antes", "d", ""), ("depois", "d", ""),
        ("diferenca", "d", ""), ("variacao_pct", "d", ""),
    ],
}

TIPOS = {"i": "int64", "s": "string", "d": "decimal", "t": "dateTime", "dt": "dateTime", "b": "boolean"}

# (fato, dimensão, chave)
RELACOES = [
    ("fato_geracao", "dim_tempo", "sk_tempo"), ("fato_geracao", "dim_usina", "sk_usina"),
    ("fato_geracao", "dim_fonte", "sk_fonte"), ("fato_geracao", "dim_submercado", "sk_submercado"),
    ("fato_pld", "dim_tempo", "sk_tempo"), ("fato_pld", "dim_submercado", "sk_submercado"),
    ("fato_parada_hora", "dim_tempo", "sk_tempo"), ("fato_parada_hora", "dim_usina", "sk_usina"),
    ("fato_parada_hora", "dim_fonte", "sk_fonte"), ("fato_parada_hora", "dim_submercado", "sk_submercado"),
]

# (nome, DAX em uma linha, formato)
MEDIDAS = [
    ("Geração MWh", "SUM ( fato_geracao[energia_mwh] )", "#,0"),
    ("Potência-hora MWh", "SUM ( fato_geracao[potencia_mw] )", "#,0"),
    ("Fator de capacidade", "DIVIDE ( [Geração MWh], [Potência-hora MWh] )", "0.0%"),
    ("Receita PLD", "SUM ( fato_geracao[receita_pld] )", "\"R$\" #,0"),
    ("Preço capturado", "DIVIDE ( [Receita PLD], [Geração MWh] )", "\"R$\" #,0.00"),
    ("PLD médio", "AVERAGE ( fato_pld[pld] )", "\"R$\" #,0.00"),
    ("PLD médio de referência",
     "DIVIDE ( SUMX ( VALUES ( dim_submercado[sk_submercado] ), [Geração MWh] * CALCULATE ( AVERAGE ( fato_pld[pld] ), "
     "REMOVEFILTERS ( dim_usina ), REMOVEFILTERS ( dim_fonte ) ) ), [Geração MWh] )", "\"R$\" #,0.00"),
    ("Razão de captura", "DIVIDE ( [Preço capturado], [PLD médio de referência] )", "0.00"),
    ("Horas indisponíveis", "COUNTROWS ( fato_parada_hora )", "#,0"),
    ("Energia não gerada MWh", "SUM ( fato_parada_hora[energia_esperada_mwh] )", "#,0"),
    ("Receita não realizada", "SUM ( fato_parada_hora[receita_perdida_rs] )", "\"R$\" #,0"),
    ("Horas sem estimativa",
     "CALCULATE ( COUNTROWS ( fato_parada_hora ), ISBLANK ( fato_parada_hora[energia_esperada_mwh] ) )", "#,0"),
    ("Radiação média", "AVERAGE ( fato_geracao[radiacao_wm2] )", "#,0"),
    ("Vento 100 m médio", "AVERAGE ( fato_geracao[vento_100m_ms] )", "#,0.0"),
    ("Correlação geração x radiação",
     "VAR T = FILTER ( CALCULATETABLE ( fato_geracao, dim_fonte[descricao] = \"Solar fotovoltaica\" ), NOT ISBLANK ( fato_geracao[radiacao_wm2] ) && NOT ISBLANK ( fato_geracao[energia_mwh] ) ) "
     "VAR mx = AVERAGEX ( T, fato_geracao[radiacao_wm2] ) VAR my = AVERAGEX ( T, fato_geracao[energia_mwh] ) "
     "VAR cov = SUMX ( T, ( fato_geracao[radiacao_wm2] - mx ) * ( fato_geracao[energia_mwh] - my ) ) "
     "VAR sx = SQRT ( SUMX ( T, ( fato_geracao[radiacao_wm2] - mx ) ^ 2 ) ) "
     "VAR sy = SQRT ( SUMX ( T, ( fato_geracao[energia_mwh] - my ) ^ 2 ) ) RETURN DIVIDE ( cov, sx * sy )", "0.00"),
    ("Correlação geração x vento",
     "VAR T = FILTER ( CALCULATETABLE ( fato_geracao, dim_fonte[descricao] = \"Eólica\" ), NOT ISBLANK ( fato_geracao[vento_100m_ms] ) && NOT ISBLANK ( fato_geracao[energia_mwh] ) ) "
     "VAR mx = AVERAGEX ( T, fato_geracao[vento_100m_ms] ) VAR my = AVERAGEX ( T, fato_geracao[energia_mwh] ) "
     "VAR cov = SUMX ( T, ( fato_geracao[vento_100m_ms] - mx ) * ( fato_geracao[energia_mwh] - my ) ) "
     "VAR sx = SQRT ( SUMX ( T, ( fato_geracao[vento_100m_ms] - mx ) ^ 2 ) ) "
     "VAR sy = SQRT ( SUMX ( T, ( fato_geracao[energia_mwh] - my ) ^ 2 ) ) RETURN DIVIDE ( cov, sx * sy )", "0.00"),
    ("Índice de qualidade", "CALCULATE ( AVERAGE ( fato_qualidade[pct_conformidade] ), fato_qualidade[fase] = \"origem\" ) / 100", "0.0%"),
    ("Índice de qualidade após limpeza",
     "CALCULATE ( AVERAGE ( fato_qualidade[pct_conformidade] ), fato_qualidade[fase] = \"stg\" ) / 100", "0.0%"),
    ("Registros com falha", "CALCULATE ( SUM ( fato_qualidade[falhas] ), fato_qualidade[fase] = \"origem\" )", "#,0"),
    ("Receita antes da limpeza",
     "CALCULATE ( SUM ( fato_impacto[antes] ), fato_impacto[indicador] = \"Receita valorada ao PLD (R$)\", fato_impacto[recorte] = \"Total\" )",
     "\"R$\" #,0"),
    ("Receita depois da limpeza",
     "CALCULATE ( SUM ( fato_impacto[depois] ), fato_impacto[indicador] = \"Receita valorada ao PLD (R$)\", fato_impacto[recorte] = \"Total\" )",
     "\"R$\" #,0"),
    ("Diferença da limpeza", "[Receita depois da limpeza] - [Receita antes da limpeza]", "\"R$\" #,0"),
]

PAGINAS = [
    ("p1_pergunta", "1. A pergunta da diretoria"), ("p2_geracao", "2. Quanto geramos (P1)"),
    ("p3_captura", "3. Quanto vale e quem captura o melhor preço (P2, P3)"), ("p4_pld", "4. Quando o PLD é alto (P4)"),
    ("p5_paradas", "5. Quanto perdemos com paradas (P5)"), ("p6_clima", "6. O clima explica a geração? (P6)"),
    ("p7_qualidade", "7. Podemos confiar nesses números? (P7)"), ("p8_recomendacao", "8. Recomendação"),
]

TAB = "\t"


def escreve(caminho: Path, texto: str) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8", newline="\n")


def tabela_tmdl(nome: str, colunas) -> str:
    linhas = [f"table {nome}", ""]
    for col, tipo, opc in colunas:
        linhas.append(f"{TAB}column {col}")
        linhas.append(f"{TAB*2}dataType: {TIPOS[tipo]}")
        if tipo == "dt":
            linhas.append(f"{TAB*2}formatString: dd/mm/yyyy")
        if tipo == "t":
            linhas.append(f"{TAB*2}formatString: dd/mm/yyyy hh:nn")
        if "hidden" in opc:
            linhas.append(f"{TAB*2}isHidden")
        linhas.append(f"{TAB*2}sourceColumn: {col}")
        linhas.append(f"{TAB*2}summarizeBy: none")
        if opc.startswith("sort:"):
            linhas.append(f"{TAB*2}sortByColumn: {opc.split(':')[1]}")
        linhas.append("")
    linhas += [
        f"{TAB}partition {nome} = m",
        f"{TAB*2}mode: import",
        f"{TAB*2}source =",
        f"{TAB*3}let",
        f"{TAB*4}Source = PostgreSQL.Database(ServidorPG, BancoPG),",
        f"{TAB*4}Tabela = Source{{[Schema=\"dw\",Item=\"{nome}\"]}}[Data]",
        f"{TAB*3}in",
        f"{TAB*4}Tabela",
        "",
    ]
    return "\n".join(linhas)


def tabela_medidas() -> str:
    linhas = ["table _Medidas", ""]
    for nome, dax, fmt in MEDIDAS:
        linhas.append(f"{TAB}measure '{nome}' = {dax}")
        linhas.append(f"{TAB*2}formatString: {fmt}")
        linhas.append("")
    linhas += [
        f"{TAB}column Coluna",
        f"{TAB*2}dataType: int64",
        f"{TAB*2}isHidden",
        f"{TAB*2}sourceColumn: Coluna",
        f"{TAB*2}summarizeBy: none",
        "",
        f"{TAB}partition _Medidas = m",
        f"{TAB*2}mode: import",
        f"{TAB*2}source =",
        f"{TAB*3}let",
        f"{TAB*4}Source = #table(type table [Coluna = Int64.Type], {{{{1}}}})",
        f"{TAB*3}in",
        f"{TAB*4}Source",
        "",
    ]
    return "\n".join(linhas)


def main() -> None:
    for d in (MODELO, RELATORIO):
        if d.exists():
            shutil.rmtree(d)

    escreve(RAIZ / f"{NOME}.pbip", json.dumps(
        {"version": "1.0", "artifacts": [{"report": {"path": f"{NOME}.Report"}}], "settings": {"enableAutoRecovery": True}}, indent=2))

    escreve(MODELO / "definition.pbism", json.dumps({"version": "4.0", "settings": {}}, indent=2))
    d = MODELO / "definition"
    escreve(d / "database.tmdl", "database\n\tcompatibilityLevel: 1567\n")
    nomes = list(T) + ["_Medidas"]
    escreve(d / "model.tmdl", "model Model\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\n"
            + "\n".join(f"ref table {n}" for n in nomes) + "\n")
    escreve(d / "expressions.tmdl",
            'expression ServidorPG = "SEU_HOST" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n\n'
            'expression BancoPG = "neondb" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n')
    for nome, colunas in T.items():
        escreve(d / "tables" / f"{nome}.tmdl", tabela_tmdl(nome, colunas))
    escreve(d / "tables" / "_Medidas.tmdl", tabela_medidas())
    escreve(d / "relationships.tmdl", "\n".join(
        f"relationship {f}_{dm}_{k}\n\tfromColumn: {f}.{k}\n\ttoColumn: {dm}.{k}\n" for f, dm, k in RELACOES))

    escreve(RELATORIO / "definition.pbir", json.dumps(
        {"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NOME}.SemanticModel"}}}, indent=2))
    r = RELATORIO / "definition"
    base = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
    escreve(r / "version.json", json.dumps({"$schema": f"{base}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2))
    escreve(r / "report.json", json.dumps({
        "$schema": f"{base}/report/1.0.0/schema.json",
        "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.59", "type": "SharedResources"}},
        "layoutOptimization": "None"}, indent=2))
    escreve(r / "pages" / "pages.json", json.dumps({
        "$schema": f"{base}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p for p, _ in PAGINAS], "activePageName": PAGINAS[0][0]}, indent=2))
    for pid, titulo in PAGINAS:
        escreve(r / "pages" / pid / "page.json", json.dumps({
            "$schema": f"{base}/page/1.0.0/schema.json", "name": pid, "displayName": titulo,
            "displayOption": "FitToPage", "height": 720, "width": 1280}, indent=2, ensure_ascii=False))

    escreve(RAIZ / ".gitignore", "**/.pbi/\n*.pbix.tmp\n")
    print(f"gerado: {RAIZ / (NOME + '.pbip')}  ({len(T)} tabelas, {len(RELACOES)} relacionamentos, {len(MEDIDAS)} medidas, {len(PAGINAS)} páginas)")


if __name__ == "__main__":
    main()
