# Roteiro do painel (Power BI)

O arquivo `.pbix` precisa ser montado no Power BI Desktop. Este roteiro cobre conexão, modelo,
páginas e a narrativa. As medidas estão em [medidas_dax.md](medidas_dax.md).

## 1. Conexão (somente o DW)

1. **Obter dados → Banco de dados PostgreSQL**. Servidor: o host do `DB_URL` do `.env`, sem `jdbc:postgresql://`
   (ex.: `ep-...-pooler.sa-east-1.aws.neon.tech`). Banco: `neondb`. Modo: **Importar**.
2. Credenciais: **Banco de dados**, com o usuário e a senha do `.env`. Marque criptografia (o Neon exige SSL).
3. Selecione **apenas** as tabelas do schema `dw`:
   `dim_tempo`, `dim_usina`, `dim_fonte`, `dim_submercado`, `fato_geracao`, `fato_pld`, `fato_parada_hora`,
   `fato_qualidade`, `fato_impacto`.
4. Não filtre `dim_usina` por `versao_atual`: cada fato já aponta para a versão da usina vigente na hora (SCD2).

## 2. Modelo (exibição de modelo)

Relacionamentos 1:* com filtro em direção única, da dimensão para o fato:

| Dimensão | Fatos |
|---|---|
| `dim_tempo[sk_tempo]` | `fato_geracao`, `fato_pld`, `fato_parada_hora` |
| `dim_submercado[sk_submercado]` | `fato_geracao`, `fato_pld`, `fato_parada_hora` |
| `dim_usina[sk_usina]` | `fato_geracao`, `fato_parada_hora` |
| `dim_fonte[sk_fonte]` | `fato_geracao`, `fato_parada_hora` |

`fato_qualidade` e `fato_impacto` ficam soltas (são relatórios da etapa de qualidade).

Ajustes:
- `dim_tempo[nome_mes]` → **Classificar por coluna** `mes`; `nome_dia_semana` → por `dia_semana`.
- Marque `dim_tempo` como tabela de datas usando `data_hora`.
- Oculte as colunas `sk_*` nos fatos.
- Formatos: R$ com 0 casas para receitas, R$ com 2 casas para preços, % com 1 casa.

## 3. Páginas (história: pergunta → evidência → recomendação)

Números de referência abaixo vêm da carga de 08/11/2026 (2025, após a limpeza). Confira no seu painel.

### Página 1. A pergunta da diretoria
- Título: *Quanto a nossa geração vale, onde e quando ganhamos ou perdemos dinheiro, e podemos confiar nos números?*
- Cartões: Geração MWh (≈ 3,12 TWh), Receita PLD (≈ R$ 638 mi), Preço capturado (≈ R$ 204/MWh),
  Índice de qualidade após limpeza.

### Página 2. P1: quanto geramos
- Colunas empilhadas: Geração MWh por `ano_mes`, legenda `dim_fonte[descricao]`.
- Matriz: usina × (Geração MWh, Fator de capacidade).
- Segmentações: fonte, submercado.
- Leitura: hidrelétricas respondem por quase metade da energia; solar e eólica têm a sazonalidade esperada.

### Página 3. P2 e P3: quanto vale e quem captura o melhor preço
- Barras: Receita PLD por fonte.
- Barras: Razão de captura por fonte, com linha constante em 1.
- Tabela: fonte, Preço capturado, PLD médio de referência, Razão de captura.
- Leitura: a térmica a gás captura ~25% acima da média (despacha na ponta); a solar captura ~23% abaixo
  (gera ao meio-dia, quando o PLD é baixo); eólica e biomassa ficam acima de 1.

### Página 4. P4: quando o PLD é alto e se geramos nesses momentos
- Matriz (mapa de calor): linhas `hora`, colunas `nome_mes`, valor PLD médio, formatação condicional de cor de fundo.
- Linhas: Geração MWh por `hora`, legenda fonte (perfil horário).
- Leitura: o PLD é mais alto entre 18h e 21h e de agosto a dezembro (período seco). Solar está fora dessa janela;
  térmica e eólica (venta mais à noite) estão dentro.

### Página 5. P5: quanto perdemos com paradas
- Barras: Receita não realizada por usina, legenda `tipo_parada`.
- Cartões: Horas indisponíveis, Energia não gerada MWh, Receita não realizada, Horas sem estimativa.
- Nota no visual: a parada de entressafra da biomassa (dez/2025, 744 h) não tem estimativa, porque a parada
  cobre o mês inteiro e não há base de comparação; a usina não geraria no período de qualquer forma.
- Leitura: as maiores perdas são programadas (revisão da UHE Iguaçu em julho, overhaul da UTE em junho);
  as forçadas somam bem menos.

### Página 6. P6: o clima explica a geração? (fonte complementar: Open-Meteo)
- Dispersão: eixo X Radiação média, eixo Y Geração MWh, detalhes `data_hora` (filtro: fonte Solar).
- Dispersão: Vento 100 m médio × Geração MWh (filtro: fonte Eólica).
- Cartões: as duas medidas de correlação.
- Leitura: radiação explica bem a solar (r ≈ 0,91); vento explica parte da eólica (r ≈ 0,51), o resto é
  ruído de medição e o ponto da reanálise não ser a torre da usina.

### Página 7. P7: podemos confiar nesses números?
- Matriz: `dimensao` × `tabela`, valor Índice de qualidade (origem) e após limpeza.
- Tabela: regra, falhas, pct_conformidade (`fase = origem`, falhas > 0).
- Cartões: Receita antes, depois, Diferença da limpeza.
- Leitura: um único dia de biomassa digitado em kWh inflaria a receita em ~R$ 166 mi (+26%).
  Sem a etapa de qualidade, o número apresentado à diretoria estaria errado.

### Página 8. Recomendação
Sugestões (ajustem com o que o grupo concluir):
1. Priorizar contratos/hedge para a solar, que captura o pior preço; avaliar armazenamento para deslocar energia para 18h-21h.
2. Programar manutenções fora de agosto a novembro, quando o PLD é mais alto.
3. Validação automática na entrada de lançamentos manuais (unidade, faixa, duplicidade), que causaram os maiores erros.

### Rodapé obrigatório (todas as páginas ou na capa)
Fontes: CCEE, Dados Abertos, PLD horário (CC-BY 4.0); Open-Meteo.com, clima histórico (CC-BY 4.0);
dados operacionais fictícios da Campo Real Energia.
