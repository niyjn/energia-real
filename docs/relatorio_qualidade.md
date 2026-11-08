# Relatório de qualidade de dados

Gerado por `qualidade/relatorio.py` em 08/11/2026 19:41 a partir da execução 25
(tabelas `qualidade.metrica`, `qualidade.decisao`, `qualidade.quarentena` e `qualidade.impacto`).

**Escopo:** todas as tabelas de origem (`campo_real_energia`) e as fontes externas (`tratada.pld_horario`,
`tratada.clima_horario`). A origem não foi alterada: a limpeza gera o schema `stg`.

## 1. Resumo por dimensão (antes da limpeza)

| dimensao | regras | regras_com_falha | registros_com_falha | conformidade_media_pct |
|---|---|---|---|---|
| Completude | 10 | 4 | 105 | 93.99 |
| Unicidade | 5 | 2 | 29 | 97.99 |
| Validade | 9 | 5 | 8 | 96.67 |
| Consistência | 6 | 6 | 66 | 95.14 |
| Acurácia | 8 | 2 | 98 | 99.98 |
| Integridade referencial | 6 | 1 | 25 | 99.99 |
| Atualidade | 4 | 2 | 293 | 97.13 |

## 2. Regras com falha (antes da limpeza)

| tabela | dimensao | regra | descricao | avaliados | falhas | pct_falhas |
|---|---|---|---|---|---|---|
| feriado_nacional | Completude | feriado_anos_sem_cobertura | Anos com PLD/medição sem feriados cadastrados (dimensão de tempo incompleta) | 2 | 1 | 50.0 |
| medicao_geracao | Acurácia | medicao_acima_da_potencia | Energia horária maior que a potência instalada (MW x 1 h) | 78792 | 26 | 0.033 |
| medicao_geracao | Validade | medicao_data_fora_do_periodo | data_hora fora de 2025-01-01 a 2025-12-31 | 78798 | 1 | 0.0013 |
| medicao_geracao | Consistência | medicao_energia_maior_que_disponibilidade | energia_mwh > disponibilidade_mw na mesma hora | 78792 | 30 | 0.0381 |
| medicao_geracao | Completude | medicao_geracao_campos_obrigatorios | Nulo ou vazio em: id_usina, data_hora, energia_mwh | 78798 | 7 | 0.0089 |
| medicao_geracao | Unicidade | medicao_geracao_duplicidade | Mais de um registro por (id_usina, data_hora) | 78798 | 28 | 0.0355 |
| medicao_geracao | Validade | medicao_geracao_energia_mwh_faixa | Energia negativa | 78792 | 4 | 0.0051 |
| medicao_geracao | Completude | medicao_geracao_horas_faltantes | Horas sem registro entre 2025-01-01 00:00:00 e 2025-12-31 23:00:00 por id_usina | 78840 | 96 | 0.1218 |
| medicao_geracao | Integridade referencial | medicao_geracao_id_usina_sem_cadastro | id_usina sem correspondência em usina | 78798 | 25 | 0.0317 |
| medicao_geracao | Atualidade | medicao_geracao_parou_de_enviar | Último registro mais de 24 h antes do fim do período | 9 | 1 | 11.1111 |
| medicao_geracao | Consistência | medicao_geracao_recebido_em_antes_de_data_hora | recebido_em anterior à hora medida | 78798 | 25 | 0.0317 |
| medicao_geracao | Consistência | medicao_gerando_durante_parada | Energia > 0 em hora coberta por parada (parada_usina) | 78798 | 4 | 0.0051 |
| medicao_geracao | Atualidade | medicao_recebida_com_atraso | recebido_em mais de 48 h depois de data_hora | 78798 | 292 | 0.3706 |
| medicao_geracao | Consistência | medicao_solar_gerando_a_noite | Solar com energia > 0 antes das 5h ou depois das 19h | 17473 | 4 | 0.0229 |
| medicao_geracao | Acurácia | medicao_valor_travado | Mesmo valor > 0 por 6+ horas seguidas | 78792 | 72 | 0.0914 |
| parada_usina | Consistência | parada_usina_fim_antes_de_inicio | fim anterior ao início | 11 | 1 | 9.0909 |
| usina | Unicidade | usina_cadastrada_duas_vezes | Mesmo nome normalizado e mesmas coordenadas | 10 | 1 | 10.0 |
| usina | Completude | usina_campos_obrigatorios | Nulo ou vazio em: cod_usina, nome, uf, municipio, latitude, longitude, potencia_mw, data_inicio_operacao | 10 | 1 | 10.0 |
| usina | Validade | usina_cod_usina_formato | cod_usina fora do padrão CRE-[A-Z]{3}-\d{3} | 10 | 1 | 10.0 |
| usina | Consistência | usina_coordenada_fora_da_uf | Coordenadas fora da caixa geográfica da UF declarada | 10 | 2 | 20.0 |
| usina | Validade | usina_inicio_operacao_futuro | data_inicio_operacao depois de hoje | 10 | 1 | 10.0 |
| usina | Validade | usina_latitude_faixa | Latitude fora do território brasileiro | 10 | 1 | 10.0 |

Regras sem nenhuma falha também ficam registradas em `qualidade.metrica` (total de 48 regras na origem).

## 3. Problemas, decisões e justificativas

Critério: só corrigimos quando há evidência independente do valor certo (outra coluna, outra tabela, a física da
usina ou a unidade). Sem evidência, sinalizamos (mantém e marca) ou isolamos em quarentena.

| tabela | problema | dimensao | registros | decisao | justificativa |
|---|---|---|---|---|---|
| usina | Usina cadastrada duas vezes (código fora do padrão, nome com espaço, município vazio) | Unicidade | 1 | Descartar | Mesmo nome e coordenadas de uma usina válida; nenhuma medição aponta para o id duplicado. |
| usina | Latitude com sinal trocado (CRE-UFV-004) | Validade | 1 | Corrigir | Latitude com sinal trocado: Janaúba/MG fica no hemisfério sul (-15,80); município, UF e longitude confirmam. |
| usina | UF diferente da localização (CRE-BIO-005) | Consistência | 1 | Corrigir | UF digitada errada: Sertãozinho fica em SP; coordenadas (-21,14; -47,99) e submercado SE confirmam. |
| usina | Data de início de operação no futuro | Validade | 1 | Sinalizar | A usina tem medições em 2025, então a data está errada, mas não há fonte para a correta: o campo fica nulo (desconhecido) e a usina é marcada. |
| parada_usina | Parada com fim antes do início, sem efeito na medição | Consistência | 1 | Quarentena | Trocar início e fim daria uma parada de 10 h, mas o SCADA registra geração normal nessas horas; o lançamento manual não é confiável para estimar perdas. |
| medicao_geracao | Medição sem usina (id_usina nulo) | Integridade referencial | 1 | Quarentena | Não é possível atribuir a geração a nenhuma usina nem a um submercado. |
| medicao_geracao | Medições de equipamento fora do cadastro (id_usina inexistente) | Integridade referencial | 24 | Quarentena | Valores idênticos aos de outra usina no mesmo dia: provável cópia; aguardam o cadastro ser revisto. |
| medicao_geracao | Data fora do período (ano digitado errado) | Validade | 1 | Quarentena | A hora provável (pelo recebido_em) já tem leitura do SCADA; corrigir o ano criaria uma duplicata. |
| medicao_geracao | Reprocessamento duplicou um dia inteiro | Unicidade | 24 | Descartar | Cópias idênticas chegaram 1 dia depois; mantida a primeira recepção. |
| medicao_geracao | Lançamento manual por cima da leitura automática (+10%) | Unicidade | 4 | Quarentena | O SCADA é a fonte primária; o manual chegou 2 dias depois sem justificativa. Fica isolado para revisão. |
| medicao_geracao | Planilha digitada em kWh | Acurácia | 24 | Corrigir | Origem MANUAL e valores ~1000x acima da potência; divididos por 1000 ficam no padrão da usina. |
| medicao_geracao | Energia negativa (consumo auxiliar registrado como geração) | Validade | 4 | Corrigir | Geração não pode ser negativa: a usina estava consumindo, ou seja, gerou 0 nessas horas. |
| medicao_geracao | Solar gerando à noite (relógio do medidor) | Consistência | 4 | Corrigir | Não há irradiação depois das 19 h; a geração física é 0. |
| medicao_geracao | Geração lançada durante manutenção (disponibilidade 0) | Consistência | 4 | Corrigir | A própria linha informa disponibilidade 0 e a parada programada está em parada_usina. |
| medicao_geracao | Energia acima da potência instalada (vírgula deslocada, pico de leitura) | Acurácia | 2 | Corrigir | Valor impossível descartado e substituído por interpolação das horas vizinhas (lacuna de 1 h). |
| medicao_geracao | Energia nula (falha de comunicação) | Completude | 6 | Corrigir | Lacuna de até 6 h interpolada linearmente; marcada como IMPUTADO. |
| medicao_geracao | Medidor travado (mesmo valor por horas) | Acurácia | 72 | Sinalizar | O valor é plausível para a usina e não há leitura alternativa; mantido e marcado. |
| medicao_geracao | Integração atrasada (dado chegou semanas depois) | Atualidade | 288 | Sinalizar | O valor está correto; o atraso importa para a operação, não para o faturamento. |
| medicao_geracao | Recebido antes de ter sido medido (relógio do servidor) | Consistência | 24 | Sinalizar | Erro no carimbo de recepção, não na energia; o valor é mantido. |
| feriado_nacional | Feriados ausentes para [2026] | Completude | 13 | Corrigir | O PLD cobre 2026, mas a origem só tem 2025. Feriados nacionais são calculados por regra (datas fixas + móveis a partir da Páscoa); a coluna origem identifica os calculados. |

### Registros em quarentena (`qualidade.quarentena`)

| tabela | motivo | registros |
|---|---|---|
| medicao_geracao | cópia idêntica de reprocessamento | 24 |
| medicao_geracao | id_usina inexistente no cadastro | 24 |
| medicao_geracao | lançamento manual conflitante com leitura SCADA | 4 |
| medicao_geracao | id_usina nulo | 1 |
| medicao_geracao | data_hora fora do período de medição | 1 |
| parada_usina | fim < inicio e a medição mostra geração normal no período: parada não aconteceu como lançada | 1 |
| usina | Duplicata de usina já cadastrada (mesmo nome normalizado e mesmas coordenadas) | 1 |

### Status final da medição na camada limpa (`stg.medicao_geracao`)

| status_qualidade | registros |
|---|---|
| ORIGINAL | 78316 |
| SINALIZADO | 384 |
| CORRIGIDO | 36 |
| IMPUTADO | 8 |

## 4. Mesmas regras depois da limpeza (medição)

Falhas que permanecem em `stg` são as que decidimos **sinalizar** (atraso, relógio, medidor travado) ou que
não dependem de correção de valor (horas que nunca chegaram).

| regra | falhas_origem | falhas_stg |
|---|---|---|
| medicao_acima_da_potencia | 26 | 0 |
| medicao_data_fora_do_periodo | 1 | 0 |
| medicao_energia_maior_que_disponibilidade | 30 | 0 |
| medicao_geracao_campos_obrigatorios | 7 | 0 |
| medicao_geracao_duplicidade | 28 | 0 |
| medicao_geracao_energia_mwh_faixa | 4 | 0 |
| medicao_geracao_horas_faltantes | 96 | 96 |
| medicao_geracao_id_usina_sem_cadastro | 25 | 0 |
| medicao_geracao_parou_de_enviar | 1 | 1 |
| medicao_geracao_recebido_em_antes_de_data_hora | 25 | 24 |
| medicao_gerando_durante_parada | 4 | 0 |
| medicao_recebida_com_atraso | 292 | 288 |
| medicao_solar_gerando_a_noite | 4 | 0 |
| medicao_valor_travado | 72 | 72 |

## 5. Impacto nos indicadores de negócio (2025)

| indicador | recorte | antes | depois | diferenca | variacao_pct |
|---|---|---|---|---|---|
| Geração (MWh) | Total | 3.865.571,23 | 3.121.701,47 | -743.869,76 | -19.2435 |
| Receita valorada ao PLD (R$) | Total | 803.911.871,49 | 638.244.186,46 | -165.667.685,03 | -20.6077 |
| Preço capturado (R$/MWh) | Total | 207,97 | 204,45 | -3,51 | -1.6893 |

### Receita valorada ao PLD por fonte

| indicador | recorte | antes | depois | diferenca | variacao_pct |
|---|---|---|---|---|---|
| Receita valorada ao PLD (R$) | Total | 803.911.871,49 | 638.244.186,46 | -165.667.685,03 | -20.6077 |
| Receita valorada ao PLD (R$) | BIO | 210.063.259,94 | 44.738.199,08 | -165.325.060,86 | -78.7025 |
| Receita valorada ao PLD (R$) | EOL | 129.964.313,72 | 129.904.772,43 | -59.541,29 | -0.0458 |
| Receita valorada ao PLD (R$) | PCH | 28.881.614,53 | 28.878.304,57 | -3.309,96 | -0.0115 |
| Receita valorada ao PLD (R$) | UFV | 42.730.538,62 | 42.689.539,44 | -40.999,18 | -0.0959 |
| Receita valorada ao PLD (R$) | UHE | 304.819.854,64 | 304.752.807,62 | -67.047,02 | -0.022 |
| Receita valorada ao PLD (R$) | UTE | 87.452.290,04 | 87.280.563,32 | -171.726,72 | -0.1964 |

**Leitura:** quase toda a diferença vem de um único dia da biomassa digitado em kWh (valores 1000x maiores).
Sem a etapa de qualidade, a receita apresentada à diretoria estaria superestimada em cerca de um quarto.
Nas demais fontes, a limpeza muda a receita em menos de 0,2%.
