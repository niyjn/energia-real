# Catálogo de regras de qualidade

São **48 regras**, avaliadas na execução 25 (`python qualidade/relatorio.py` regenera este arquivo).
Cada regra tem um nome único por tabela, uma dimensão e uma descrição, gravados na tabela `qualidade.metrica`.

As regras são aplicadas por funções reutilizáveis de [`qualidade/regras.py`](../qualidade/regras.py) a cada tabela, em
`regras_medicao`, `regras_cadastro` e `regras_externas`, em [`qualidade/executar.py`](../qualidade/executar.py).
As decisões sobre cada problema encontrado (corrigir, descartar, quarentena ou sinalizar) estão em
[relatorio_qualidade.md](relatorio_qualidade.md).

## Resumo por dimensão

| dimensao | regras |
|---|---|
| Completude | 10 |
| Unicidade | 5 |
| Validade | 9 |
| Consistência | 6 |
| Acurácia | 8 |
| Integridade referencial | 6 |
| Atualidade | 4 |

## Completude (10 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 1 | `clima_horario` | `clima_horario_campos_obrigatorios` | Nulo ou vazio em: temperatura_c, radiacao_wm2, vento_10m_ms, vento_100m_ms, precipitacao_mm, nuvens_pct | 145152 | 0 |
| 2 | `clima_horario` | `clima_horario_horas_faltantes` | Horas sem registro entre 2025-01-01 00:00 e 2026-11-03 23:00:00 por cod_usina | 145152 | 0 |
| 3 | `feriado_nacional` | `feriado_anos_sem_cobertura` | Anos com PLD/medição sem feriados cadastrados (dimensão de tempo incompleta) | 2 | 1 |
| 4 | `fonte_energia` | `fonte_energia_campos_obrigatorios` | Nulo ou vazio em: cod_fonte, descricao | 6 | 0 |
| 5 | `medicao_geracao` | `medicao_geracao_campos_obrigatorios` | Nulo ou vazio em: id_usina, data_hora, energia_mwh | 78798 | 7 |
| 6 | `medicao_geracao` | `medicao_geracao_horas_faltantes` | Horas sem registro entre 2025-01-01 00:00:00 e 2025-12-31 23:00:00 por id_usina | 78840 | 96 |
| 7 | `parada_usina` | `parada_usina_campos_obrigatorios` | Nulo ou vazio em: id_usina, inicio, fim, tipo, motivo | 11 | 0 |
| 8 | `pld_horario` | `pld_horario_horas_faltantes` | Horas sem registro entre 2025-01-01 00:00:00 e 2026-11-09 23:00:00 por cod_submercado | 65088 | 0 |
| 9 | `submercado` | `submercado_campos_obrigatorios` | Nulo ou vazio em: cod_submercado, nome | 4 | 0 |
| 10 | `usina` | `usina_campos_obrigatorios` | Nulo ou vazio em: cod_usina, nome, uf, municipio, latitude, longitude, potencia_mw, data_inicio_operacao | 10 | 1 |

## Unicidade (5 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 11 | `fonte_energia` | `fonte_energia_duplicidade` | Mais de um registro por (cod_fonte) | 6 | 0 |
| 12 | `medicao_geracao` | `medicao_geracao_duplicidade` | Mais de um registro por (id_usina, data_hora) | 78798 | 28 |
| 13 | `pld_horario` | `pld_horario_duplicidade` | Mais de um registro por (data_hora, cod_submercado) | 65088 | 0 |
| 14 | `submercado` | `submercado_duplicidade` | Mais de um registro por (cod_submercado) | 4 | 0 |
| 15 | `usina` | `usina_cadastrada_duas_vezes` | Mesmo nome normalizado e mesmas coordenadas | 10 | 1 |

## Validade (9 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 16 | `medicao_geracao` | `medicao_data_fora_do_periodo` | data_hora fora de 2025-01-01 a 2025-12-31 | 78798 | 1 |
| 17 | `medicao_geracao` | `medicao_geracao_energia_mwh_faixa` | Energia negativa | 78792 | 4 |
| 18 | `parada_usina` | `parada_tipo_invalido` | tipo diferente de Programada/Forçada | 11 | 0 |
| 19 | `pld_horario` | `pld_fora_piso_teto` | PLD fora do piso/teto regulatório do ano | 65088 | 0 |
| 20 | `usina` | `usina_cod_usina_formato` | cod_usina fora do padrão CRE-[A-Z]{3}-\d{3} | 10 | 1 |
| 21 | `usina` | `usina_inicio_operacao_futuro` | data_inicio_operacao depois de hoje | 10 | 1 |
| 22 | `usina` | `usina_latitude_faixa` | Latitude fora do território brasileiro | 10 | 1 |
| 23 | `usina` | `usina_longitude_faixa` | Longitude fora do território brasileiro | 10 | 0 |
| 24 | `usina` | `usina_potencia_mw_faixa` | Potência instalada não positiva | 10 | 0 |

## Consistência (6 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 25 | `medicao_geracao` | `medicao_energia_maior_que_disponibilidade` | energia_mwh > disponibilidade_mw na mesma hora | 78792 | 30 |
| 26 | `medicao_geracao` | `medicao_geracao_recebido_em_antes_de_data_hora` | recebido_em anterior à hora medida | 78798 | 25 |
| 27 | `medicao_geracao` | `medicao_gerando_durante_parada` | Energia > 0 em hora coberta por parada (parada_usina) | 78798 | 4 |
| 28 | `medicao_geracao` | `medicao_solar_gerando_a_noite` | Solar com energia > 0 antes das 5h ou depois das 19h | 17473 | 4 |
| 29 | `parada_usina` | `parada_usina_fim_antes_de_inicio` | fim anterior ao início | 11 | 1 |
| 30 | `usina` | `usina_coordenada_fora_da_uf` | Coordenadas fora da caixa geográfica da UF declarada | 10 | 2 |

## Acurácia (8 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 31 | `clima_horario` | `clima_horario_nuvens_pct_faixa` | nuvens_pct fisicamente implausível (fora de [0, 100]) | 145152 | 0 |
| 32 | `clima_horario` | `clima_horario_precipitacao_mm_faixa` | precipitacao_mm fisicamente implausível (fora de [0, 300]) | 145152 | 0 |
| 33 | `clima_horario` | `clima_horario_radiacao_wm2_faixa` | radiacao_wm2 fisicamente implausível (fora de [0, 1400]) | 145152 | 0 |
| 34 | `clima_horario` | `clima_horario_temperatura_c_faixa` | temperatura_c fisicamente implausível (fora de [-10, 50]) | 145152 | 0 |
| 35 | `clima_horario` | `clima_horario_vento_100m_ms_faixa` | vento_100m_ms fisicamente implausível (fora de [0, 75]) | 145152 | 0 |
| 36 | `clima_horario` | `clima_horario_vento_10m_ms_faixa` | vento_10m_ms fisicamente implausível (fora de [0, 60]) | 145152 | 0 |
| 37 | `medicao_geracao` | `medicao_acima_da_potencia` | Energia horária maior que a potência instalada (MW x 1 h) | 78792 | 26 |
| 38 | `medicao_geracao` | `medicao_valor_travado` | Mesmo valor > 0 por 6+ horas seguidas | 78792 | 72 |

## Integridade referencial (6 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 39 | `clima_horario` | `clima_horario_cod_usina_sem_cadastro` | cod_usina sem correspondência em usina | 145152 | 0 |
| 40 | `medicao_geracao` | `medicao_geracao_id_usina_sem_cadastro` | id_usina sem correspondência em usina | 78798 | 25 |
| 41 | `parada_usina` | `parada_usina_id_usina_sem_cadastro` | id_usina sem correspondência em usina | 11 | 0 |
| 42 | `pld_horario` | `pld_horario_cod_submercado_sem_cadastro` | cod_submercado sem correspondência em submercado | 65088 | 0 |
| 43 | `usina` | `usina_cod_submercado_sem_cadastro` | cod_submercado sem correspondência em submercado | 10 | 0 |
| 44 | `usina` | `usina_id_fonte_sem_cadastro` | id_fonte sem correspondência em fonte_energia | 10 | 0 |

## Atualidade (4 regras)

| # | tabela | regra | o que verifica | avaliados | falhas na origem |
|---|---|---|---|---|---|
| 45 | `clima_horario` | `clima_horario_parou_de_enviar` | Último registro mais de 48 h antes do fim do período | 9 | 0 |
| 46 | `medicao_geracao` | `medicao_geracao_parou_de_enviar` | Último registro mais de 24 h antes do fim do período | 9 | 1 |
| 47 | `medicao_geracao` | `medicao_recebida_com_atraso` | recebido_em mais de 48 h depois de data_hora | 78798 | 292 |
| 48 | `pld_horario` | `pld_horario_parou_de_enviar` | Último registro mais de 24 h antes do fim do período | 4 | 0 |
