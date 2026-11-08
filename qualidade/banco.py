"""Acesso ao banco para as etapas de qualidade e DW (reaproveita conexão, config e log da extração)."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from psycopg import sql

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "extração"))

import common  # noqa: E402  (conectar, carregar_env, carregar_config, execucao, log)


def ler_df(conn, consulta: str | sql.Composable, params=None) -> pd.DataFrame:
    """Executa um SELECT e devolve DataFrame; colunas Decimal viram float."""
    with conn.cursor() as cur:
        cur.execute(consulta, params)
        colunas = [c.name for c in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=colunas)
    for c in df.columns:
        if df[c].map(lambda v: isinstance(v, Decimal)).any():
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def _py(v):
    """Converte valores pandas/numpy para tipos que o psycopg entende."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.generic):
        return None if (isinstance(v, np.floating) and np.isnan(v)) else v.item()
    return v


def recriar(conn, schema: str, tabela: str, colunas_ddl: str) -> sql.Identifier:
    """DROP + CREATE da tabela (camadas derivadas são reconstruídas a cada execução)."""
    ident = sql.Identifier(schema, tabela)
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(ident))
    conn.execute(sql.SQL("CREATE TABLE {} (" + colunas_ddl + ")").format(ident))
    return ident


def garantir(conn, schema: str, tabela: str, colunas_ddl: str) -> sql.Identifier:
    """CREATE TABLE IF NOT EXISTS (tabelas com histórico, como as métricas)."""
    ident = sql.Identifier(schema, tabela)
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (" + colunas_ddl + ")").format(ident))
    return ident


def copiar(conn, ident: sql.Identifier, df: pd.DataFrame) -> int:
    """Grava o DataFrame com COPY (rápido mesmo com dezenas de milhares de linhas)."""
    if df.empty:
        return 0
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in df.columns)
    with conn.cursor() as cur:
        with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(ident, cols)) as cp:
            for linha in df.itertuples(index=False, name=None):
                cp.write_row([_py(v) for v in linha])
    return len(df)
