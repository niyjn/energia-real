"""Gera os visuais (PBIR) das 8 páginas do relatório Power BI. Só escreve em energia_real.Report; o modelo não é tocado.

Uso: python gerar_visuais.py      (feche o Power BI sem salvar antes e reabra o .pbip depois)
Depois de abrir: arraste dim_tempo[ano] para "Filtros em todas as páginas" (Filtragem básica) e marque 2025;
na página 4, campo "PLD médio" > Formatação condicional > Cor de tela de fundo. Rodar este script de novo apaga a
escala de cor; rodar gerar_pbip.py de novo apaga o filtro de ano.
"""
from __future__ import annotations

import json
import shutil

from gerar_pbip import PAGINAS, RELATORIO, escreve

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.0.0/schema.json"


def col(entidade: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entidade}}, "Property": prop}}


def med(nome: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "_Medidas"}}, "Property": nome}}


def proj(campo: dict) -> dict:
    tipo = "Measure" if "Measure" in campo else "Column"
    entidade = campo[tipo]["Expression"]["SourceRef"]["Entity"]
    prop = campo[tipo]["Property"]
    return {"field": campo, "queryRef": f"{entidade}.{prop}", "nativeQueryRef": prop}


def literal(texto: str) -> dict:
    return {"expr": {"Literal": {"Value": "'" + texto.replace("'", "''") + "'"}}}


def visual(tipo: str, x: int, y: int, w: int, h: int, papeis: dict[str, list[dict]], titulo: str | None = None) -> dict:
    v: dict = {"visualType": tipo,
               "query": {"queryState": {r: {"projections": [proj(f) for f in fs]} for r, fs in papeis.items()}},
               "drillFilterOtherVisuals": True}
    if titulo:
        v["visualContainerObjects"] = {"title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}},
                                                                   "text": literal(titulo)}}]}
    return {"x": x, "y": y, "w": w, "h": h, "visual": v}


def texto(x: int, y: int, w: int, h: int, linhas: list[str]) -> dict:
    paragrafos = [{"textRuns": [{"value": linha}]} for linha in linhas]
    return {"x": x, "y": y, "w": w, "h": h,
            "visual": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragrafos}}]}}}


def cartao(x, y, medida, titulo=None, w=300, h=110):
    return visual("card", x, y, w, h, {"Values": [med(medida)]}, titulo)


FONTES = ("Fontes: CCEE, Dados Abertos, PLD horário (CC-BY 4.0); Open-Meteo.com, clima histórico (CC-BY 4.0); "
          "dados operacionais fictícios da Campo Real Energia.")

PAGINA_VISUAIS: dict[str, list[dict]] = {
    "p1_pergunta": [
        texto(20, 20, 1240, 70, ["Quanto a nossa geração vale, onde e quando ganhamos ou perdemos dinheiro, e podemos confiar nos números?"]),
        cartao(20, 110, "Geração MWh", "Geração (MWh)"),
        cartao(340, 110, "Receita PLD", "Receita valorada ao PLD"),
        cartao(660, 110, "Preço capturado", "Preço capturado (R$/MWh)"),
        cartao(980, 110, "Índice de qualidade após limpeza", "Índice de qualidade (após limpeza)"),
        visual("clusteredColumnChart", 20, 250, 620, 400, {"Category": [col("dim_fonte", "descricao")], "Y": [med("Receita PLD")]},
               "Receita valorada ao PLD por fonte"),
        visual("clusteredColumnChart", 660, 250, 600, 400, {"Category": [col("dim_fonte", "descricao")], "Y": [med("Razão de captura")]},
               "Razão de captura por fonte (1 = preço médio do submercado)"),
        texto(20, 670, 1240, 40, [FONTES]),
    ],
    "p2_geracao": [
        visual("slicer", 20, 20, 300, 160, {"Values": [col("dim_fonte", "descricao")]}, "Fonte"),
        visual("slicer", 20, 190, 300, 160, {"Values": [col("dim_submercado", "nome")]}, "Submercado"),
        visual("columnChart", 340, 20, 920, 330,
               {"Category": [col("dim_tempo", "ano_mes")], "Series": [col("dim_fonte", "descricao")], "Y": [med("Geração MWh")]},
               "Geração mensal por fonte (MWh)"),
        visual("pivotTable", 20, 370, 1240, 330,
               {"Rows": [col("dim_usina", "nome")], "Values": [med("Geração MWh"), med("Fator de capacidade")]},
               "Geração e fator de capacidade por usina"),
    ],
    "p3_captura": [
        visual("clusteredBarChart", 20, 20, 620, 330, {"Category": [col("dim_fonte", "descricao")], "Y": [med("Receita PLD")]},
               "Receita valorada ao PLD por fonte"),
        visual("clusteredBarChart", 660, 20, 600, 330, {"Category": [col("dim_fonte", "descricao")], "Y": [med("Razão de captura")]},
               "Razão de captura (preço capturado / PLD médio do submercado)"),
        visual("tableEx", 20, 370, 1240, 330,
               {"Values": [col("dim_fonte", "descricao"), med("Geração MWh"), med("Receita PLD"), med("Preço capturado"),
                           med("PLD médio de referência"), med("Razão de captura")]},
               "Preço capturado por fonte"),
    ],
    "p4_pld": [
        visual("pivotTable", 20, 20, 760, 680,
               {"Rows": [col("dim_tempo", "hora")], "Columns": [col("dim_tempo", "nome_mes")], "Values": [med("PLD médio")]},
               "PLD médio por hora x mês (R$/MWh)"),
        visual("lineChart", 800, 20, 460, 680,
               {"Category": [col("dim_tempo", "hora")], "Series": [col("dim_fonte", "descricao")], "Y": [med("Geração MWh")]},
               "Perfil horário de geração por fonte"),
    ],
    "p5_paradas": [
        cartao(20, 20, "Horas indisponíveis", "Horas indisponíveis", w=300),
        cartao(340, 20, "Energia não gerada MWh", "Energia não gerada (MWh)", w=300),
        cartao(660, 20, "Receita não realizada", "Receita não realizada", w=300),
        cartao(980, 20, "Horas sem estimativa", "Horas sem estimativa (parada cobre o mês todo)", w=280),
        visual("clusteredBarChart", 20, 150, 1240, 540,
               {"Category": [col("dim_usina", "nome")], "Series": [col("fato_parada_hora", "tipo_parada")], "Y": [med("Receita não realizada")]},
               "Receita não realizada por usina e tipo de parada"),
    ],
    "p6_clima": [
        visual("slicer", 20, 20, 300, 200, {"Values": [col("dim_fonte", "descricao")]}, "Fonte (escolha Solar ou Eólica)"),
        cartao(20, 240, "Correlação geração x radiação", "Correlação geração x radiação (Solar)", w=300),
        cartao(20, 370, "Correlação geração x vento", "Correlação geração x vento (Eólica)", w=300),
        visual("scatterChart", 340, 20, 920, 680,
               {"Category": [col("dim_tempo", "data")], "X": [med("Radiação média")], "Y": [med("Geração MWh")]},
               "Geração diária x radiação média (marque Solar no filtro da esquerda)"),
    ],
    "p7_qualidade": [
        cartao(20, 20, "Índice de qualidade", "Índice de qualidade (origem)", w=240),
        cartao(280, 20, "Índice de qualidade após limpeza", "Índice de qualidade (após limpeza)", w=240),
        cartao(540, 20, "Receita antes da limpeza", "Receita antes da limpeza", w=240),
        cartao(800, 20, "Receita depois da limpeza", "Receita depois da limpeza", w=240),
        cartao(1060, 20, "Diferença da limpeza", "Diferença da limpeza", w=200),
        visual("tableEx", 20, 150, 1240, 550,
               {"Values": [col("fato_qualidade", "fase"), col("fato_qualidade", "dimensao"), col("fato_qualidade", "tabela"),
                           col("fato_qualidade", "regra"), col("fato_qualidade", "avaliados"), col("fato_qualidade", "falhas"),
                           col("fato_qualidade", "pct_conformidade")]},
               "Regras de qualidade: registros avaliados, falhas e conformidade"),
    ],
    "p8_recomendacao": [
        texto(40, 40, 1200, 560, [
            "Recomendações",
            "1. A solar captura o pior preço (gera ao meio-dia, quando o PLD é baixo): priorizar contratos/hedge para ela e avaliar armazenamento para deslocar energia para 18h-21h.",
            "2. O PLD é mais alto de agosto a dezembro e entre 18h e 21h: programar manutenções fora desse período.",
            "3. A térmica a gás captura o melhor preço porque despacha na ponta: manter flexibilidade para esse horário.",
            "4. Os maiores erros de dado vieram de lançamentos manuais (unidade errada, duplicidade): validar unidade, faixa e duplicidade na entrada.",
        ]),
        texto(40, 640, 1200, 60, [FONTES]),
    ],
}


def main() -> None:
    nomes_validos = {p for p, _ in PAGINAS}
    for pagina, visuais in PAGINA_VISUAIS.items():
        assert pagina in nomes_validos, pagina
        pasta = RELATORIO / "definition" / "pages" / pagina / "visuals"
        if pasta.exists():
            shutil.rmtree(pasta)
        for i, v in enumerate(visuais, 1):
            nome = f"v{i:02d}"
            conteudo = {"$schema": SCHEMA, "name": nome,
                        "position": {"x": v["x"], "y": v["y"], "z": i, "height": v["h"], "width": v["w"], "tabOrder": i},
                        "visual": v["visual"]}
            escreve(pasta / nome / "visual.json", json.dumps(conteudo, indent=2, ensure_ascii=False))
    total = sum(len(v) for v in PAGINA_VISUAIS.values())
    print(f"gerados {total} visuais em {len(PAGINA_VISUAIS)} páginas")


if __name__ == "__main__":
    main()
