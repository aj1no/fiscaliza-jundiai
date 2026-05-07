# RAG do Fiscaliza Jundiai

## Estado atual

O projeto usa uma camada de RAG baseada em chunks.
Por padrao, os vetores ficam salvos no PostgreSQL atual como fallback local.
Se `QDRANT_URL` estiver configurado, os chunks tambem sao enviados para uma colecao no Qdrant.

## Fluxo

1. O coletor salva documentos em `documentos_brutos`.
2. `process_document` extrai texto para `documentos_processados`.
3. O texto limpo e quebrado em chunks com overlap.
4. Cada chunk recebe um embedding local por hash.
5. Os chunks sao salvos em `documento_chunks`.
6. Se configurado, os chunks sao enviados para o Qdrant.
7. `/rag` busca os chunks mais parecidos com a pergunta.
8. A resposta sempre retorna evidencias e links oficiais.

## Chunking

Padrao atual:

- `RAG_CHUNK_SIZE=1200`
- `RAG_CHUNK_OVERLAP=250`
- `RAG_VECTOR_DIMENSIONS=384`

O overlap evita que uma informacao importante fique cortada entre dois chunks.

## Busca Vetorial

O embedding atual e `local-hash-v1`.
Ele cria um vetor numerico local a partir dos termos do chunk e calcula similaridade por cosseno.

Isso permite busca vetorial local e tambem envio para Qdrant sem adicionar dependencia nova no Python.

## Qdrant

Variaveis opcionais:

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `QDRANT_COLLECTION=fiscaliza_jundiai_chunks`
- `QDRANT_TIMEOUT=20`

Quando `QDRANT_URL` estiver vazio, a API usa o fallback local no PostgreSQL.
Quando estiver preenchido, o fluxo de indexacao cria a colecao no Qdrant, envia os chunks e usa Qdrant primeiro nas buscas.

O Qdrant espera vetores densos. Por isso, o vetor esparso local e convertido para uma lista densa com `RAG_VECTOR_DIMENSIONS`.
Ao migrar para OpenAI/Gemini embeddings, mantenha a dimensao da colecao igual a dimensao do modelo escolhido.

## Endpoints

Indexar chunks existentes:

```bash
POST /rag/index
POST /rag/index?force=true
POST /rag/index?limit=100
```

Buscar chunks:

```bash
GET /rag/search?q=festa%20da%20uva&limit=8
```

Ver status do indice:

```bash
GET /rag/status
```

Responder com RAG:

```bash
GET /rag?q=quanto%20foi%20gasto%20na%20festa%20da%20uva%20de%202026
```

## Limites

Esta versao ainda usa `local-hash-v1`, nao um modelo semantico externo.
Mesmo assim, ja integra com Qdrant como vector store e deixa o projeto pronto para trocar o gerador de embeddings depois.

Para perguntas de valores, o sistema combina RAG com dados estruturados de despesas/receitas quando disponiveis.

## Proximo passo recomendado

1. Criar um cluster gratuito no Qdrant Cloud.
2. Copiar a URL e a API key para o `.env`.
3. Reiniciar `backend` e `worker`.
4. Rodar `POST /rag/index?force=true`.
5. Conferir `GET /rag/status`.
