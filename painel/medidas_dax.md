# Medidas DAX do painel

Crie uma tabela vazia `_Medidas` (Inserir → Inserir Dados → OK) e cole cada medida nela.
Todas leem só do schema `dw`.

## Geração (P1)

```dax
Geração MWh = SUM ( fato_geracao[energia_mwh] )

Potência-hora MWh = SUM ( fato_geracao[potencia_mw] )      -- potência x 1 h de cada hora medida

Fator de capacidade = DIVIDE ( [Geração MWh], [Potência-hora MWh] )     -- formato %
```

## Receita e captura (P2, P3)

```dax
Receita PLD = SUM ( fato_geracao[receita_pld] )

Preço capturado = DIVIDE ( [Receita PLD], [Geração MWh] )               -- R$/MWh

PLD médio = AVERAGE ( fato_pld[pld] )

-- PLD médio do(s) submercado(s) onde a geração aconteceu, ponderado pela geração
PLD médio de referência =
DIVIDE (
    SUMX (
        VALUES ( dim_submercado[sk_submercado] ),
        [Geração MWh]
            * CALCULATE ( AVERAGE ( fato_pld[pld] ), REMOVEFILTERS ( dim_usina ), REMOVEFILTERS ( dim_fonte ) )
    ),
    [Geração MWh]
)

Razão de captura = DIVIDE ( [Preço capturado], [PLD médio de referência] )   -- > 1 = vende acima da média
```

## Perdas por parada (P5)

```dax
Horas indisponíveis = COUNTROWS ( fato_parada_hora )

Energia não gerada MWh = SUM ( fato_parada_hora[energia_esperada_mwh] )

Receita não realizada = SUM ( fato_parada_hora[receita_perdida_rs] )

Horas sem estimativa =
CALCULATE ( COUNTROWS ( fato_parada_hora ), ISBLANK ( fato_parada_hora[energia_esperada_mwh] ) )
```

## Clima (P6)

```dax
Radiação média = AVERAGE ( fato_geracao[radiacao_wm2] )
Vento 100 m médio = AVERAGE ( fato_geracao[vento_100m_ms] )

-- Correlação de Pearson entre geração e radiação no contexto do visual (use com filtro Fonte = Solar)
Correlação geração x radiação =
VAR T = FILTER ( fato_geracao, NOT ISBLANK ( fato_geracao[radiacao_wm2] ) && NOT ISBLANK ( fato_geracao[energia_mwh] ) )
VAR n = COUNTROWS ( T )
VAR mx = AVERAGEX ( T, fato_geracao[radiacao_wm2] )
VAR my = AVERAGEX ( T, fato_geracao[energia_mwh] )
VAR cov = SUMX ( T, ( fato_geracao[radiacao_wm2] - mx ) * ( fato_geracao[energia_mwh] - my ) )
VAR sx = SQRT ( SUMX ( T, ( fato_geracao[radiacao_wm2] - mx ) ^ 2 ) )
VAR sy = SQRT ( SUMX ( T, ( fato_geracao[energia_mwh] - my ) ^ 2 ) )
RETURN DIVIDE ( cov, sx * sy )
```

Para a eólica, duplique a medida trocando `radiacao_wm2` por `vento_100m_ms`.

## Confiança nos números (P7)

```dax
Índice de qualidade =
CALCULATE ( AVERAGE ( fato_qualidade[pct_conformidade] ), fato_qualidade[fase] = "origem" ) / 100

Índice de qualidade após limpeza =
CALCULATE ( AVERAGE ( fato_qualidade[pct_conformidade] ), fato_qualidade[fase] = "stg" ) / 100

Registros com falha = CALCULATE ( SUM ( fato_qualidade[falhas] ), fato_qualidade[fase] = "origem" )

Receita antes da limpeza =
CALCULATE ( SUM ( fato_impacto[antes] ), fato_impacto[indicador] = "Receita valorada ao PLD (R$)", fato_impacto[recorte] = "Total" )

Receita depois da limpeza =
CALCULATE ( SUM ( fato_impacto[depois] ), fato_impacto[indicador] = "Receita valorada ao PLD (R$)", fato_impacto[recorte] = "Total" )

Diferença da limpeza = [Receita depois da limpeza] - [Receita antes da limpeza]
```
