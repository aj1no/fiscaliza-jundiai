# Arquitetura do Projeto - Fiscaliza Jundiai

O Fiscaliza Jundiai e uma aplicacao Docker Compose para coleta, processamento, busca e visualizacao de dados publicos oficiais do municipio.

## Componentes principais

### Frontend

- HTML, CSS e JavaScript estaticos.
- Servido por Nginx.
- Painel publico em `http://localhost:8080`.
- Consome a API FastAPI em `http://localhost:8000`.
- Exibe documentos, filtros, busca, paginacao e indicadores financeiros.

### Backend

- FastAPI.
- Endpoints REST para documentos, busca, coleta manual e analytics.
- Cria tabelas no startup via SQLAlchemy para o MVP.
- Expoe Swagger em `/docs`.

### Banco de dados

- PostgreSQL.
- Guarda documentos brutos, documentos processados, logs de coleta e tabelas analiticas.
- O volume `postgres_data` preserva os dados locais entre reinicios.

### Fila e processamento

- Redis como broker.
- Celery worker para coletas e processamento de documentos.
- Celery beat para agendamento:
  - Imprensa Oficial: a cada 6 horas.
  - Camara/SAPL: a cada 12 horas.
  - Portal da Transparencia: diariamente as 03:00.

### Storage

- Arquivos brutos ficam em `storage/raw/` dentro do volume Docker.
- O `.gitignore` evita subir arquivos coletados para o repositorio.

## Fluxo de dados

```text
Frontend -> FastAPI -> Celery -> Coletores -> Storage/Banco -> Processamento -> Busca/Analytics -> Frontend
```

1. O usuario acessa o painel ou dispara `/collect/manual`.
2. A API enfileira tarefas no Celery.
3. Os coletores acessam fontes oficiais.
4. Documentos brutos sao salvos em banco e storage.
5. O worker processa PDF, HTML, JSON e CSV.
6. O texto extraido alimenta busca e analytics.
7. O frontend consulta endpoints para exibir documentos e indicadores.

## Fontes integradas

- Imprensa Oficial: PDF.
- Camara Municipal/SAPL: HTML.
- Portal da Transparencia: JSON e CSV.

## Endpoints principais

- `GET /health`
- `POST /collect/manual`
- `GET /documents`
- `GET /search`
- `GET /analytics/receitas`
- `GET /analytics/gastos/secretarias`
- `GET /analytics/gastos/secretaria`
- `GET /analytics/gastos/termo`
- `GET /ask`
- `GET /rag`

## Stack tecnica

- Python 3.11
- FastAPI
- SQLAlchemy
- PostgreSQL 15
- Redis
- Celery
- BeautifulSoup
- Requests
- Nginx
- HTML/CSS/JavaScript
- Docker Compose

## Decisoes do MVP

- Sem dados simulados.
- Coletas reais com logs e tratamento de erro.
- Frontend estatico para reduzir complexidade.
- Processamento textual local antes de qualquer camada de IA externa.
- Links oficiais preservados para auditoria.
