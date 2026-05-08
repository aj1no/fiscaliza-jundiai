# Coletor do Portal da Transparencia

Este documento descreve o estado atual do coletor real do Portal da Transparencia de Jundiai.

## Fonte oficial

- Portal publico: `https://transparencia.jundiai.sp.gov.br/`
- Sistema de consultas: `https://web21.cijun.sp.gov.br/PMJ/YC/`

O coletor investiga HTML, links e endpoints publicos antes da coleta. Quando existe exportacao CSV oficial, ela e priorizada. Quando nao existe CSV adequado, o coletor usa endpoints JSON/XHR publicos.

## Categorias coletadas

| Tipo de documento | Origem | Formato salvo |
| --- | --- | --- |
| `licitacao` | `Despesas/GetDespesaPorLicitacao` | JSON |
| `despesa_secretaria` | `Despesas/ClassificacaoOrcamentaria` com `tipo_download=CSV` | CSV |
| `contrato` | `Despesas/GetDespesaPorContrato` | JSON |
| `receita_classificacao` | `Receitas/GetReceitasPorClassificacao` | JSON |

## Endpoints principais

### Licitacoes

```text
https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesaPorLicitacao
```

Parametros usados:

- `ano`
- `licitacao`
- `modalidade`
- `objeto`
- `data_inicial`
- `data_final`
- `page`
- `per_page`

### Despesas por secretaria

O coletor prioriza o CSV oficial da tela "Por Classificacao Orcamentaria":

```text
https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/ClassificacaoOrcamentaria
```

Parametros usados para CSV:

- `ano`
- `data_inicial=1`
- `data_final=12`
- `tipo=1`
- `executaConsulta=true`
- `per_page=1000000`
- `tipo_download=CSV`
- `page=1`

Se o CSV falhar, o coletor tenta o endpoint JSON:

```text
https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesasPorClassificacao
```

### Contratos

```text
https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesaPorContrato
```

### Receitas por classificacao

```text
https://web21.cijun.sp.gov.br/PMJ/YC/Receitas/GetReceitasPorClassificacao
```

## Campos normalizados

O JSON bruto salvo no storage preserva:

- `fonte`
- `categoria`
- `rastreabilidade`
  - endpoint acessado
  - URL final
  - parametros enviados
  - status code
- `registro_bruto`
- `normalizado`

Campos normalizados variam por categoria, mas seguem a ideia:

- `fonte`
- `tipo_documento`
- `titulo`
- `ano`
- `secretaria`
- `fornecedor`
- `cnpj`
- `objeto`
- `valor_empenhado`
- `valor_liquidado`
- `valor_pago`
- `valor_orcado`
- `valor_arrecadado`
- `url_origem`
- `hash_arquivo`
- `status_processamento`

Quando o endpoint nao retorna um campo, o valor fica `null`. O coletor nao inventa dados.

## Hash e deduplicacao

A deduplicacao principal e por `hash_arquivo`.

Como fallback, o coletor tambem evita duplicidade por:

```text
fonte + tipo_documento + url_origem + titulo + data_publicacao
```

Tambem ha protecao pratica por `url_origem`, importante para registros financeiros que podem ter sido coletados antes por JSON e depois por CSV.

No payload bruto usado para hash, `data_coleta` fica `null` de proposito para manter o hash estavel entre execucoes. A data real da coleta fica salva em `documentos_brutos.data_coleta`.

## Variaveis de ambiente

| Variavel | Padrao | Teto interno | Uso |
| --- | --- | --- | --- |
| `PORTAL_TRANSPARENCIA_ANO` | ano atual | 2100 | Exercicio consultado |
| `PORTAL_TRANSPARENCIA_ANO_INICIAL` | `PORTAL_TRANSPARENCIA_ANO` | 2100 | Inicio da janela de anos |
| `PORTAL_TRANSPARENCIA_ANO_FINAL` | `PORTAL_TRANSPARENCIA_ANO` | 2100 | Fim da janela de anos |
| `PORTAL_TRANSPARENCIA_MAX_ANOS_COLETA` | 10 | 15 | Teto de seguranca para backfill |
| `PORTAL_TRANSPARENCIA_LIMIT` | 100 | 1000 | Licitacoes |
| `PORTAL_TRANSPARENCIA_PAGE_SIZE` | 100 | 100 | Tamanho fixo da pagina |
| `PORTAL_TRANSPARENCIA_DESPESAS_SECRETARIA_LIMIT` | 150 | 500 | Despesas por secretaria |
| `PORTAL_TRANSPARENCIA_CONTRATOS_LIMIT` | 150 | 500 | Contratos |
| `PORTAL_TRANSPARENCIA_RECEITAS_LIMIT` | 150 | 500 | Receitas |
| `PORTAL_TRANSPARENCIA_MES` | mes de referencia | 12 | Mes unico para remuneracao (modo fixo) |
| `PORTAL_TRANSPARENCIA_MES_INICIAL` | `PORTAL_TRANSPARENCIA_MES` | 12 | Inicio do intervalo de meses |
| `PORTAL_TRANSPARENCIA_MES_FINAL` | `PORTAL_TRANSPARENCIA_MES` | 12 | Fim do intervalo de meses |
| `PORTAL_TRANSPARENCIA_REMUNERACAO_LIMIT` | 10000 | 20000 | Limite CSV remuneracao por consulta |

### Recorte temporal recomendado

Para fiscalizacao da gestao iniciada em 01/01/2025:

```env
PORTAL_TRANSPARENCIA_ANO_INICIAL=2025
PORTAL_TRANSPARENCIA_ANO_FINAL=2026
PORTAL_TRANSPARENCIA_MAX_ANOS_COLETA=10
```

Comportamento da remuneracao sem override de mes:

- ano passado: coleta `1..12`;
- ano atual: coleta `1..mes de referencia` (mes atual - 1).

Se quiser forcar um intervalo fixo para todos os anos, defina `PORTAL_TRANSPARENCIA_MES_INICIAL` e `PORTAL_TRANSPARENCIA_MES_FINAL`.

## Execucao manual

Com Docker Compose rodando:

```bash
curl -X POST http://localhost:8000/collect/manual
```

Consultar documentos do portal:

```bash
curl "http://localhost:8000/documents?fonte=portal_transparencia&limit=5"
```

Consultar gastos por secretaria:

```bash
curl "http://localhost:8000/analytics/gastos/secretarias?ano=2026"
```

Consultar receitas:

```bash
curl "http://localhost:8000/analytics/receitas?ano=2026"
```

## Logs esperados

O coletor registra:

- URL acessada;
- status code;
- quantidade de links encontrados;
- endpoints/exportacoes encontrados;
- categoria coletada;
- quantidade de registros encontrados;
- quantidade de registros salvos;
- duplicados por hash;
- duplicados por chave composta;
- erros completos com stack trace.

## Limitacoes conhecidas

- Os valores dependem do que os endpoints publicos retornam.
- Alguns endpoints nao retornam data especifica por item.
- Dados anonimizados ou mascarados pela fonte oficial sao preservados.
- O CSV de despesas por secretaria e agregado, nao uma lista individual de cada pagamento.
- O painel financeiro usa esses agregados por secretaria para facilitar leitura publica.
