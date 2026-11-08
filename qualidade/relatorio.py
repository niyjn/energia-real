"""Gera docs/relatorio_qualidade.md a partir das tabelas do schema qualidade (última execução).

Uso: python relatorio.py
"""
from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd

import regras as R
from banco import RAIZ, common, ler_df


def tabela_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "_Nenhum registro._\n"
    cab = "| " + " | ".join(df.columns) + " |\n|" + "|".join("---" for _ in df.columns) + "|\n"
    linhas = "".join("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |\n" for r in df.itertuples(index=False))
    return cab + linhas


def brl(v) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> int:
    common.carregar_env()
    with common.conectar() as conn:
        ex = conn.execute("SELECT MAX(id_execucao) FROM qualidade.metrica").fetchone()[0]
        met = ler_df(conn, "SELECT * FROM qualidade.metrica WHERE id_execucao = %s ORDER BY tabela, regra", (ex,))
        dec = ler_df(conn, "SELECT tabela, problema, dimensao, registros, decisao, justificativa FROM qualidade.decisao")
        imp = ler_df(conn, "SELECT indicador, recorte, antes, depois, diferenca, variacao_pct FROM qualidade.impacto")
        qua = ler_df(conn, "SELECT tabela, motivo, COUNT(*) AS registros FROM qualidade.quarentena GROUP BY 1, 2 ORDER BY 1, 3 DESC")
        stg = ler_df(conn, "SELECT status_qualidade, COUNT(*) AS registros FROM stg.medicao_geracao GROUP BY 1 ORDER BY 2 DESC")

    origem, depois = met[met["fase"] == "origem"], met[met["fase"] == "stg"]
    por_dim = (origem.groupby("dimensao").agg(regras=("regra", "count"), regras_com_falha=("falhas", lambda s: int((s > 0).sum())),
                                              registros_com_falha=("falhas", "sum"), conformidade_media_pct=("pct_conformidade", "mean"))
               .reindex(R.DIMENSOES).reset_index())
    por_dim["conformidade_media_pct"] = por_dim["conformidade_media_pct"].round(2)

    comp = origem[["regra", "falhas"]].merge(depois[["regra", "falhas"]], on="regra", suffixes=("_origem", "_stg"))
    total = imp[imp["recorte"] == "Total"].copy()
    for c in ("antes", "depois", "diferenca"):
        total[c] = total[c].map(brl)
    receita = imp[imp["indicador"].str.startswith("Receita")].copy()
    for c in ("antes", "depois", "diferenca"):
        receita[c] = receita[c].map(brl)

    texto = f"""# Relatório de qualidade de dados

Gerado por `qualidade/relatorio.py` em {datetime.now():%d/%m/%Y %H:%M} a partir da execução {ex}
(tabelas `qualidade.metrica`, `qualidade.decisao`, `qualidade.quarentena` e `qualidade.impacto`).

**Escopo:** todas as tabelas de origem (`campo_real_energia`) e as fontes externas (`tratada.pld_horario`,
`tratada.clima_horario`). A origem não foi alterada: a limpeza gera o schema `stg`.

## 1. Resumo por dimensão (antes da limpeza)

{tabela_md(por_dim)}
## 2. Regras com falha (antes da limpeza)

{tabela_md(origem[origem["falhas"] > 0][["tabela", "dimensao", "regra", "descricao", "avaliados", "falhas", "pct_falhas"]])}
Regras sem nenhuma falha também ficam registradas em `qualidade.metrica` (total de {len(origem)} regras na origem).

## 3. Problemas, decisões e justificativas

Critério: só corrigimos quando há evidência independente do valor certo (outra coluna, outra tabela, a física da
usina ou a unidade). Sem evidência, sinalizamos (mantém e marca) ou isolamos em quarentena.

{tabela_md(dec)}
### Registros em quarentena (`qualidade.quarentena`)

{tabela_md(qua)}
### Status final da medição na camada limpa (`stg.medicao_geracao`)

{tabela_md(stg)}
## 4. Mesmas regras depois da limpeza (medição)

Falhas que permanecem em `stg` são as que decidimos **sinalizar** (atraso, relógio, medidor travado) ou que
não dependem de correção de valor (horas que nunca chegaram).

{tabela_md(comp)}
## 5. Impacto nos indicadores de negócio (2025)

{tabela_md(total)}
### Receita valorada ao PLD por fonte

{tabela_md(receita)}
**Leitura:** quase toda a diferença vem de um único dia da biomassa digitado em kWh (valores 1000x maiores).
Sem a etapa de qualidade, a receita apresentada à diretoria estaria superestimada em cerca de um quarto.
Nas demais fontes, a limpeza muda a receita em menos de 0,2%.
"""
    destino = RAIZ / "docs" / "relatorio_qualidade.md"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print(f"gerado: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
