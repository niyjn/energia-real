# Data Warehouse Campo Real Energia

Trabalho prático de Data Warehouse (Centro Universitário Campo Real). Do dado bruto ao indicador:
extração de fontes públicas, qualidade de dados, star schema no PostgreSQL e painel no Power BI.

> *Quanto a nossa geração vale, onde e quando ganhamos ou perdemos dinheiro, e podemos confiar nos números?*

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `origem/` | Script do banco operacional fictício (`campo_real_energia_postgres.sql`) |
| `extração/` | Etapa 1: extratores Python do PLD (CCEE) e do clima (Open-Meteo), com raw, tratada e log |
| `qualidade/` | Etapa 2: profiling, regras por dimensão, limpeza com decisões, impacto antes x depois |
| `dw/` | Etapa 3: DDL, carga (SCD2) e views do star schema |
| `painel/` | Etapa 4: medidas DAX e roteiro de montagem do painel |
| `docs/` | Documento de modelagem e relatório de qualidade |

## Requisitos

- Python 3.10+
- PostgreSQL 13+ (usamos Neon; qualquer PostgreSQL serve)
- Power BI Desktop (para o painel)

```bash
pip install -r requirements.txt
```

O extrator da CCEE pode usar um navegador automatizado (ver "Observação sobre a CCEE"). Nesse caso, instale
também o Chromium do Playwright (`playwright install chromium`) ou tenha Chrome, Edge ou Firefox instalado.

## Configuração

1. Crie um arquivo `.env` na raiz (ele está no `.gitignore` e **não** deve ser versionado):

   ```
   DB_URL=jdbc:postgresql://SEU_HOST:5432/SEU_BANCO
   DB_USER=seu_usuario
   DB_PASSWORD=sua_senha
   ```

2. Parâmetros sem segredo (schemas, retries, anos, colunas, coordenadas corrigidas) ficam em `extração/config.ini`.
   Limites das regras de qualidade ficam no topo de `qualidade/executar.py`.

## Ordem de execução

| # | Comando | O que faz | Grava em |
|---|---|---|---|
| 0 | Rodar `origem/campo_real_energia_postgres.sql` no DBeaver (Alt+X) | Cria o banco de origem | `campo_real_energia.*` |
| 1 | `python extração/ccee_pld.py` | PLD horário 2025 e 2026 pela API CKAN | `raw.ccee_pld_raw`, `tratada.pld_horario` |
| 2 | `python extração/open_meteo.py` | Clima horário nas coordenadas das 9 usinas | `raw.open_meteo_raw`, `tratada.clima_horario` |
| 3 | `python qualidade/executar.py` | Profiling, regras, limpeza, quarentena, impacto | `qualidade.*`, `stg.*` |
| 4 | `python qualidade/relatorio.py` | Gera o relatório de qualidade | `docs/relatorio_qualidade.md` |
| 5 | `python dw/carregar_dw.py` | Cria e carrega o star schema e confere os grãos | `dw.*` |
| 6 | Power BI: seguir `painel/roteiro_painel.md` | Painel lendo só do `dw` | `.pbix` |

Todas as etapas registram início, fim, linhas e status em `raw.log_extracao`. Todas podem ser reexecutadas:
a extração só baixa o que falta, a qualidade e os fatos são reconstruídos, e a `dim_usina` preserva o histórico (SCD2).
Para recriar o DW do zero: `python dw/carregar_dw.py --recriar`.

## Resultados (2025, após a limpeza)

| Indicador | Valor |
|---|---|
| Geração | 3,12 TWh |
| Receita valorada ao PLD | R$ 638,2 milhões |
| Preço capturado médio | R$ 204,45/MWh |
| Melhor captura | Térmica a gás (razão 1,25: despacha no horário de ponta) |
| Pior captura | Solar (razão 0,77: gera ao meio-dia, quando o PLD é baixo) |
| Efeito da limpeza na receita | −R$ 165,7 milhões (−20,6%), quase todo de um dia digitado em kWh |

Detalhes: [docs/relatorio_qualidade.md](docs/relatorio_qualidade.md) e [docs/modelagem.md](docs/modelagem.md).

## Observação sobre a CCEE

O portal de dados abertos da CCEE fica atrás de uma CDN (Akamai) que respondeu **HTTP 403 "Acesso bloqueado"**
a clientes HTTP simples (`requests`, `curl`), enquanto o mesmo portal abria normalmente no navegador. O extrator
registrou as falhas em `raw.log_extracao` (status `ERRO`, sem retentativas inúteis para 403). Com `[http] cliente = auto`
no `config.ini`, quando recebe 403 o extrator passa a fazer as requisições à API por um navegador automatizado
(`extração/sabidez.py`, Selenium ou Playwright). A API consultada, a paginação e o raw são os mesmos.

## Fontes e licenças

- **CCEE, Dados Abertos, PLD horário** (`dadosabertos.ccee.org.br/dataset/pld_horario`): licença CC-BY 4.0.
- **Open-Meteo, Historical Weather API** (`open-meteo.com`): licença CC-BY 4.0, uso não comercial.
- Dados operacionais da Campo Real Energia: fictícios, gerados pelo script da disciplina.
