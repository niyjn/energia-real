/* =====================================================================
   VIEWS DE APOIO (leem só do dw) - conferência das perguntas do painel
   e agregações diárias/mensais para quem preferir não usar DAX.
   ===================================================================== */

-- P1: geração e fator de capacidade por usina e mês
CREATE OR REPLACE VIEW dw.vw_geracao_mensal AS
SELECT t.ano_mes, u.cod_usina, u.nome AS usina, f.descricao AS fonte, s.cod_submercado,
       SUM(g.energia_mwh)                                         AS geracao_mwh,
       COUNT(*)                                                   AS horas_medidas,
       ROUND(100 * SUM(g.energia_mwh) / NULLIF(SUM(g.potencia_mw), 0), 2) AS fator_capacidade_pct
FROM dw.fato_geracao g
JOIN dw.dim_tempo t      USING (sk_tempo)
JOIN dw.dim_usina u      USING (sk_usina)
JOIN dw.dim_fonte f      USING (sk_fonte)
JOIN dw.dim_submercado s USING (sk_submercado)
GROUP BY 1, 2, 3, 4, 5;

-- P2/P3: receita valorada ao PLD e preço capturado por fonte
CREATE OR REPLACE VIEW dw.vw_captura_por_fonte AS
WITH pld_medio AS (
    SELECT p.sk_submercado, AVG(p.pld) AS pld_medio
    FROM dw.fato_pld p JOIN dw.dim_tempo t USING (sk_tempo)
    WHERE t.ano = 2025
    GROUP BY 1
)
SELECT f.descricao AS fonte,
       SUM(g.energia_mwh)                                    AS geracao_mwh,
       SUM(g.receita_pld)                                    AS receita_rs,
       ROUND(SUM(g.receita_pld) / NULLIF(SUM(g.energia_mwh), 0), 2) AS preco_capturado,
       -- PLD médio de referência ponderado pela geração em cada submercado
       ROUND(SUM(g.energia_mwh * pm.pld_medio) / NULLIF(SUM(g.energia_mwh), 0), 2) AS pld_medio_submercado,
       ROUND(SUM(g.receita_pld) / NULLIF(SUM(g.energia_mwh * pm.pld_medio), 0), 4) AS razao_captura
FROM dw.fato_geracao g
JOIN dw.dim_fonte f USING (sk_fonte)
JOIN pld_medio pm   USING (sk_submercado)
GROUP BY 1;

-- P4: PLD médio por hora x mês (mapa de calor)
CREATE OR REPLACE VIEW dw.vw_pld_hora_mes AS
SELECT t.ano, t.mes, t.nome_mes, t.hora, s.cod_submercado, ROUND(AVG(p.pld), 2) AS pld_medio
FROM dw.fato_pld p
JOIN dw.dim_tempo t      USING (sk_tempo)
JOIN dw.dim_submercado s USING (sk_submercado)
GROUP BY 1, 2, 3, 4, 5;

-- P5: perdas por parada
CREATE OR REPLACE VIEW dw.vw_perdas_parada AS
SELECT u.cod_usina, u.nome AS usina, ph.id_parada, ph.tipo_parada, ph.motivo, ph.metodo_estimativa,
       COUNT(*)                     AS horas_indisponiveis,
       SUM(ph.energia_esperada_mwh) AS energia_nao_gerada_mwh,
       SUM(ph.receita_perdida_rs)   AS receita_nao_realizada_rs
FROM dw.fato_parada_hora ph
JOIN dw.dim_usina u USING (sk_usina)
GROUP BY 1, 2, 3, 4, 5, 6;

-- Granularidade diária derivada do grão horário
CREATE OR REPLACE VIEW dw.vw_geracao_diaria AS
SELECT t.data, u.cod_usina, SUM(g.energia_mwh) AS geracao_mwh, SUM(g.receita_pld) AS receita_rs,
       AVG(g.radiacao_wm2) AS radiacao_media_wm2, AVG(g.vento_100m_ms) AS vento_100m_medio_ms
FROM dw.fato_geracao g
JOIN dw.dim_tempo t USING (sk_tempo)
JOIN dw.dim_usina u USING (sk_usina)
GROUP BY 1, 2;
