# Coletor do Portal da Transparencia

## Endpoint publico usado

O coletor usa o endpoint publico:

`https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesaPorLicitacao`

Ele foi escolhido porque aparece no HTML da pagina oficial de despesas por licitacao e retorna JSON estruturado, evitando scraping visual. A pagina publica relacionada e:

`https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/Licitacao`

## Parametros conhecidos

O endpoint aceita, no minimo:

- `ano`: exercicio consultado.
- `licitacao`: numero da licitacao, opcional.
- `modalidade`: codigo da modalidade; `0` consulta todas.
- `objeto`: filtro textual sobre a descricao/objeto.
- `data_inicial`: filtro de data, quando usado pela pagina oficial.
- `data_final`: filtro de data, quando usado pela pagina oficial.
- `page`: pagina zero-based.
- `per_page`: tamanho da pagina. O coletor limita a 100 por chamada.

## Campos retornados

Nos testes atuais, cada item de `licitacoes` retorna:

- `licitacao`
- `modalidade`
- `exercicio`
- `descricao`
- `codigo_modalidade`
- `registro_preco`

O JSON tambem retorna `total_itens`, usado para controlar a paginacao.

## Normalizacao

Cada arquivo bruto salvo em `storage/raw/portal_transparencia` contem:

- rastreabilidade da chamada: endpoint, URL final, parametros e status code.
- `registro_bruto`: item retornado pelo endpoint.
- `normalizado`: estrutura padronizada com fonte, tipo, titulo, numero da licitacao, modalidade, objeto, ano, orgao, valor, URL de origem e status.

Campos nao presentes no endpoint, como `orgao`, `valor` e datas especificas, ficam como `null`.
No JSON bruto, `data_coleta` fica `null` para manter o hash estavel entre execucoes; a data real da coleta fica em `documentos_brutos.data_coleta`.

## Configuracao

Variaveis opcionais:

- `PORTAL_TRANSPARENCIA_ANO`: ano consultado. Padrao: ano atual.
- `PORTAL_TRANSPARENCIA_LIMIT`: limite maximo de registros coletados. Padrao: `100`; teto interno: `1000`.
- `PORTAL_TRANSPARENCIA_PAGE_SIZE`: tamanho de pagina. Padrao: `100`; teto interno: `100`.

## Execucao manual

Com os containers em execucao:

```bash
curl -X POST http://localhost:8000/collect/manual
```

Para consultar os documentos coletados:

```bash
curl "http://localhost:8000/documents?fonte=portal_transparencia&limit=5"
```

## Limitacoes conhecidas

- O MVP coleta a categoria `licitacao`.
- O endpoint de licitacoes nao retornou, nos testes atuais, valor, orgao ou data especifica por item.
- O coletor preserva exatamente os dados publicos recebidos e nao tenta contornar anonimizacoes ou mascaramentos.
- A deduplicacao e feita por `hash_arquivo` e, quando o hash muda por alteracao de rastreabilidade, tambem pela chave composta `fonte + tipo_documento + url_origem + titulo + data_publicacao`.
