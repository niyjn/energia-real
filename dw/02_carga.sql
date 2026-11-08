/* =====================================================================
   DATA WAREHOUSE CAMPO REAL ENERGIA - CARGA
   ---------------------------------------------------------------------
   Pré-requisitos: extração (raw/tratada) e qualidade (stg/qualidade) executadas.
   Dimensões: upsert (tipo 1) e SCD tipo 2 na dim_usina.
   Fatos: recarga completa (as camadas de origem são fotografias completas).
   Executado por dw/carregar_dw.py dentro de uma transação.
   ===================================================================== */

-- ---------------------------------------------------------------- membros desconhecidos
INSERT INTO dw.dim_tempo (sk_tempo, nome_mes, nome_dia_semana, estacao, periodo_hidrologico, patamar_carga)
VALUES (-1, 'N/D', 'N/D', 'N/D', 'N/D', 'N/D') ON CONFLICT (sk_tempo) DO NOTHING;

INSERT INTO dw.dim_submercado (sk_submercado, cod_submercado, nome, regioes)
VALUES (-1, '??', 'Desconhecido', 'Desconhecido') ON CONFLICT (sk_submercado) DO NOTHING;

INSERT INTO dw.dim_fonte (sk_fonte, cod_fonte, descricao)
VALUES (-1, '???', 'Desconhecida') ON CONFLICT (sk_fonte) DO NOTHING;

INSERT INTO dw.dim_usina (sk_usina, cod_usina, nome, valido_de)
VALUES (-1, '???', 'Desconhecida', '1900-01-01') ON CONFLICT (sk_usina) DO NOTHING;

-- ---------------------------------------------------------------- dim_tempo (2025-2026, hora a hora)
INSERT INTO dw.dim_tempo
SELECT
    TO_CHAR(t.data_hora, 'YYYYMMDDHH24')::INT,
    t.data_hora,
    t.data_hora::DATE,
    EXTRACT(YEAR FROM t.data_hora),
    EXTRACT(QUARTER FROM t.data_hora),
    EXTRACT(MONTH FROM t.data_hora),
    (ARRAY['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto',
           'Setembro','Outubro','Novembro','Dezembro'])[EXTRACT(MONTH FROM t.data_hora)],
    TO_CHAR(t.data_hora, 'YYYY-MM'),
    EXTRACT(DAY FROM t.data_hora),
    EXTRACT(ISODOW FROM t.data_hora),
    (ARRAY['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo'])[EXTRACT(ISODOW FROM t.data_hora)],
    EXTRACT(HOUR FROM t.data_hora),
    EXTRACT(ISODOW FROM t.data_hora) >= 6,
    COALESCE(f.tipo = 'Feriado', FALSE),
    COALESCE(f.tipo = 'Ponto facultativo', FALSE),
    f.descricao,
    EXTRACT(ISODOW FROM t.data_hora) < 6 AND f.data IS NULL,
    CASE WHEN TO_CHAR(t.data_hora, 'MM-DD') >= '12-21' OR TO_CHAR(t.data_hora, 'MM-DD') < '03-20' THEN 'Verão'
         WHEN TO_CHAR(t.data_hora, 'MM-DD') < '06-21' THEN 'Outono'
         WHEN TO_CHAR(t.data_hora, 'MM-DD') < '09-22' THEN 'Inverno'
         ELSE 'Primavera' END,
    CASE WHEN EXTRACT(MONTH FROM t.data_hora) IN (11, 12, 1, 2, 3, 4) THEN 'Úmido' ELSE 'Seco' END,
    -- Patamar de carga (simplificação): dia útil: leve 0-6h, pesada 18-20h, média no resto;
    -- sábado: leve 0-6h, média no resto; domingo/feriado: média 17-21h, leve no resto.
    CASE
        WHEN EXTRACT(ISODOW FROM t.data_hora) < 6 AND f.data IS NULL THEN
             CASE WHEN EXTRACT(HOUR FROM t.data_hora) <= 6 THEN 'Leve'
                  WHEN EXTRACT(HOUR FROM t.data_hora) BETWEEN 18 AND 20 THEN 'Pesada'
                  ELSE 'Média' END
        WHEN EXTRACT(ISODOW FROM t.data_hora) = 6 AND f.data IS NULL THEN
             CASE WHEN EXTRACT(HOUR FROM t.data_hora) <= 6 THEN 'Leve' ELSE 'Média' END
        ELSE CASE WHEN EXTRACT(HOUR FROM t.data_hora) BETWEEN 17 AND 21 THEN 'Média' ELSE 'Leve' END
    END,
    EXTRACT(ISODOW FROM t.data_hora) < 6 AND f.data IS NULL AND EXTRACT(HOUR FROM t.data_hora) BETWEEN 18 AND 20
FROM generate_series(TIMESTAMP '2025-01-01 00:00', TIMESTAMP '2026-12-31 23:00', INTERVAL '1 hour') AS t(data_hora)
LEFT JOIN stg.feriado f ON f.data = t.data_hora::DATE
ON CONFLICT (sk_tempo) DO UPDATE SET
    feriado = EXCLUDED.feriado, ponto_facultativo = EXCLUDED.ponto_facultativo, nome_feriado = EXCLUDED.nome_feriado,
    dia_util = EXCLUDED.dia_util, patamar_carga = EXCLUDED.patamar_carga, horario_ponta = EXCLUDED.horario_ponta;

-- ---------------------------------------------------------------- dimensões tipo 1
INSERT INTO dw.dim_submercado (cod_submercado, nome, regioes)
SELECT TRIM(cod_submercado), nome, regioes FROM campo_real_energia.submercado
ON CONFLICT (cod_submercado) DO UPDATE SET nome = EXCLUDED.nome, regioes = EXCLUDED.regioes;

INSERT INTO dw.dim_fonte (cod_fonte, descricao, renovavel, intermitente)
SELECT cod_fonte, descricao, renovavel, intermitente FROM campo_real_energia.fonte_energia
ON CONFLICT (cod_fonte) DO UPDATE SET descricao = EXCLUDED.descricao, renovavel = EXCLUDED.renovavel,
                                      intermitente = EXCLUDED.intermitente;

-- ---------------------------------------------------------------- dim_usina: SCD tipo 2
CREATE TEMP TABLE tmp_usina ON COMMIT DROP AS
SELECT u.*,
       MD5(CONCAT_WS('|', u.nome, u.potencia_mw, u.situacao, u.cod_fonte, u.cod_submercado)) AS hash_scd2
FROM stg.usina u;

-- já existia versão? (decide se a nova versão vale "desde sempre" ou a partir de agora)
CREATE TEMP TABLE tmp_usina_existente ON COMMIT DROP AS
SELECT DISTINCT cod_usina FROM dw.dim_usina WHERE sk_usina <> -1;

-- 1) fecha a versão atual de quem mudou algum atributo tipo 2
UPDATE dw.dim_usina d
   SET valido_ate = LOCALTIMESTAMP, versao_atual = FALSE
  FROM tmp_usina s
 WHERE d.cod_usina = s.cod_usina AND d.versao_atual AND d.hash_scd2 <> s.hash_scd2;

-- 2) abre versão nova para usinas novas ou recém-fechadas
INSERT INTO dw.dim_usina (cod_usina, nome, cod_fonte, cod_submercado, uf, municipio, latitude, longitude,
                          potencia_mw, data_inicio_operacao, situacao, flag_qualidade, hash_scd2, valido_de)
SELECT s.cod_usina, s.nome, s.cod_fonte, s.cod_submercado, s.uf, s.municipio, s.latitude, s.longitude,
       s.potencia_mw, s.data_inicio_operacao, s.situacao, s.flag_qualidade, s.hash_scd2,
       CASE WHEN e.cod_usina IS NULL THEN TIMESTAMP '1900-01-01' ELSE LOCALTIMESTAMP END
FROM tmp_usina s
LEFT JOIN tmp_usina_existente e USING (cod_usina)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_usina d WHERE d.cod_usina = s.cod_usina AND d.versao_atual);

-- 3) tipo 1: correções de cadastro sobrescrevem a versão atual (não são mudanças de negócio)
UPDATE dw.dim_usina d
   SET uf = s.uf, municipio = s.municipio, latitude = s.latitude, longitude = s.longitude,
       data_inicio_operacao = s.data_inicio_operacao, flag_qualidade = s.flag_qualidade
  FROM tmp_usina s
 WHERE d.cod_usina = s.cod_usina AND d.versao_atual;

-- ---------------------------------------------------------------- fatos (recarga completa)
TRUNCATE dw.fato_pld, dw.fato_geracao, dw.fato_parada_hora;

INSERT INTO dw.fato_pld (sk_tempo, sk_submercado, pld)
SELECT COALESCE(t.sk_tempo, -1), COALESCE(s.sk_submercado, -1), p.pld
FROM tratada.pld_horario p
LEFT JOIN dw.dim_tempo t      ON t.data_hora = p.data_hora
LEFT JOIN dw.dim_submercado s ON s.cod_submercado = TRIM(p.cod_submercado);

-- versão da usina vigente na hora medida (SCD2)
CREATE TEMP TABLE tmp_med ON COMMIT DROP AS
SELECT m.*, su.cod_usina, su.cod_fonte, su.cod_submercado,
       COALESCE(t.sk_tempo, -1)      AS sk_tempo,
       COALESCE(du.sk_usina, -1)     AS sk_usina,
       COALESCE(df.sk_fonte, -1)     AS sk_fonte,
       COALESCE(ds.sk_submercado, -1) AS sk_submercado,
       du.potencia_mw                AS potencia_versao
FROM stg.medicao_geracao m
JOIN stg.usina su            ON su.id_usina = m.id_usina
LEFT JOIN dw.dim_tempo t     ON t.data_hora = m.data_hora
LEFT JOIN dw.dim_usina du    ON du.cod_usina = su.cod_usina AND m.data_hora >= du.valido_de AND m.data_hora < du.valido_ate
LEFT JOIN dw.dim_fonte df    ON df.cod_fonte = su.cod_fonte
LEFT JOIN dw.dim_submercado ds ON ds.cod_submercado = su.cod_submercado;

INSERT INTO dw.fato_geracao
SELECT m.sk_tempo, m.sk_usina, m.sk_fonte, m.sk_submercado,
       m.energia_mwh, m.disponibilidade_mw, m.potencia_versao,
       fp.pld, ROUND(m.energia_mwh * fp.pld, 2),
       EXISTS (SELECT 1 FROM stg.parada_usina p
               WHERE p.id_usina = m.id_usina AND m.data_hora BETWEEN p.inicio AND p.fim),
       m.status_qualidade,
       c.radiacao_wm2, c.vento_10m_ms, c.vento_100m_ms, c.temperatura_c, c.precipitacao_mm, c.nuvens_pct
FROM tmp_med m
LEFT JOIN dw.fato_pld fp          ON fp.sk_tempo = m.sk_tempo AND fp.sk_submercado = m.sk_submercado
LEFT JOIN tratada.clima_horario c ON c.cod_usina = m.cod_usina AND c.data_hora = m.data_hora;

-- Energia esperada numa hora de parada = média da mesma usina, mesma hora do dia e mesmo mês,
-- calculada só com horas fora de parada. Se a parada cobre o mês inteiro, não há base: fica NULL.
INSERT INTO dw.fato_parada_hora
WITH horas AS (
    SELECT p.id_parada, p.id_usina, p.tipo, p.motivo, h.data_hora
    FROM stg.parada_usina p
    CROSS JOIN LATERAL generate_series(p.inicio, p.fim, INTERVAL '1 hour') AS h(data_hora)
), base AS (
    SELECT m.id_usina, EXTRACT(MONTH FROM m.data_hora) AS mes, EXTRACT(HOUR FROM m.data_hora) AS hora,
           AVG(m.energia_mwh) AS media
    FROM stg.medicao_geracao m
    WHERE m.energia_mwh IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM stg.parada_usina p
                      WHERE p.id_usina = m.id_usina AND m.data_hora BETWEEN p.inicio AND p.fim)
    GROUP BY 1, 2, 3
)
SELECT COALESCE(t.sk_tempo, -1), COALESCE(du.sk_usina, -1), COALESCE(df.sk_fonte, -1), COALESCE(ds.sk_submercado, -1),
       h.id_parada, h.tipo, h.motivo,
       ROUND(b.media, 3), fp.pld, ROUND(b.media * fp.pld, 2),
       CASE WHEN b.media IS NULL THEN 'sem base: parada cobre o mês inteiro'
            ELSE 'média da mesma hora no mesmo mês, fora de paradas' END
FROM horas h
JOIN stg.usina su            ON su.id_usina = h.id_usina
LEFT JOIN base b             ON b.id_usina = h.id_usina AND b.mes = EXTRACT(MONTH FROM h.data_hora)
                            AND b.hora = EXTRACT(HOUR FROM h.data_hora)
LEFT JOIN dw.dim_tempo t     ON t.data_hora = h.data_hora
LEFT JOIN dw.dim_usina du    ON du.cod_usina = su.cod_usina AND h.data_hora >= du.valido_de AND h.data_hora < du.valido_ate
LEFT JOIN dw.dim_fonte df    ON df.cod_fonte = su.cod_fonte
LEFT JOIN dw.dim_submercado ds ON ds.cod_submercado = su.cod_submercado
LEFT JOIN dw.fato_pld fp     ON fp.sk_tempo = t.sk_tempo AND fp.sk_submercado = ds.sk_submercado;

-- ---------------------------------------------------------------- qualidade (última execução)
DELETE FROM dw.fato_qualidade;
INSERT INTO dw.fato_qualidade
SELECT id_execucao, executado_em, fase, regra, dimensao, tabela, descricao, avaliados, falhas, pct_conformidade
FROM qualidade.metrica
WHERE id_execucao = (SELECT MAX(id_execucao) FROM qualidade.metrica);

DELETE FROM dw.fato_impacto;
INSERT INTO dw.fato_impacto
SELECT id_execucao, indicador, recorte, antes, depois, diferenca, variacao_pct FROM qualidade.impacto;
