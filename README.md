# Fiscaliza Jundiai

*[Ler em Português](README.pt-br.md)*

Public dashboard to collect, organize, search, and analyze official data from Jundiai/SP (Brazil).

The project started as a document collector and evolved into a simple analytical dashboard, with full-text search, financial indicators, and traceability to official sources.

> Disclaimer: This is an independent citizen-interest project, with no official institutional ties to the City Hall or City Council.

## Project Status

Functional MVP running on Docker:

- Static frontend served by Nginx.
- FastAPI backend.
- PostgreSQL for persistence.
- Redis + Celery for background collections and processing.
- Real data collection from the Official Gazette (Imprensa Oficial), City Council (Câmara/SAPL), and Transparency Portal.
- Processing of PDF, HTML, JSON, and CSV files.
- Unified full-text search.
- Financial dashboard with reliability metadata (consolidated, partial, inseguro_para_soma).

## Data Sources

| Source | Content | Formats |
| --- | --- | --- |
| Official Gazette of Jundiai | Official publications, notices, and administrative acts | PDF |
| City Council / SAPL | Summaries of plenary sessions | HTML |
| Transparency Portal | Biddings, contracts, revenues, and expenses by department | JSON and CSV |

All screens keep links to the official source whenever available.

## Features

- Manual and scheduled collection of official documents.
- Deduplication by hash and composite key, with updates to live financial data when content changes.
- Text extraction for search in PDF, HTML, JSON, and CSV.
- Search by title, source, document type, and extracted text.
- Public dashboard with source and type filters.
- Document pagination by source.
- Transparency indicators:
  - collected revenue value;
  - collected paid expense value;
  - observed financial execution;
  - collected committed value;
  - collected liquidated value;
  - complete ranking of expenses by department.
- Analytical endpoints for questions about expenses, revenues, themes, neighborhoods, and councilors.

## How to run

### 1. Create the environment file

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

### 2. Start the containers

```bash
docker compose up --build -d
```

### 3. Check API health

PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/health
```

Bash:

```bash
curl http://localhost:8000/health
```

### 4. Access

- Frontend: http://localhost:8080
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs

## Manual Collection

Administrative endpoints require a token in the `X-Admin-Token` header.

PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/collect/manual -Headers @{ "X-Admin-Token" = "YOUR_TOKEN" }
```

Bash:

```bash
curl -X POST http://localhost:8000/collect/manual -H "X-Admin-Token: YOUR_TOKEN"
```

Then monitor the logs:

```bash
docker compose logs -f worker
```

## Useful Endpoints

### Documents

```text
GET /documents?fonte=portal_transparencia&limit=5
GET /documents?fonte=camara_sessoes&limit=5
GET /documents?fonte=imprensa_oficial&limit=5
GET /search?q=educacao
GET /search?q=licitacao
```

### Analytics

```text
GET /analytics/receitas?ano=2026
GET /analytics/gastos/secretarias?ano=2026
GET /analytics/gastos/secretaria?nome=Saude&ano=2026
GET /analytics/gastos/termo?termo=Festa%20da%20Uva&ano=2026
GET /analytics/temas
GET /analytics/bairros
GET /analytics/secretarias
GET /analytics/vereadores/{nome}
GET /ask?q=quanto%20foi%20gasto%20com%20saude
GET /rag?q=contratos%20da%20cultura
```

Financial endpoints return quality metadata, for example:

```json
{
  "metadados": {
    "coleta_completa": false,
    "registros_encontrados": 150,
    "limite_aplicado": 150,
    "nivel_confiabilidade": "parcial"
  }
}
```

### Administration (token required)

```text
POST /collect/manual
POST /analytics/process
POST /rag/index
GET /tasks/{task_id}
```

## Environment Variables

Main configurations are in `.env.example`.

| Variable | Usage |
| --- | --- |
| `POSTGRES_USER` | PostgreSQL user |
| `POSTGRES_PASSWORD` | PostgreSQL local password |
| `POSTGRES_DB` | Database used by the application |
| `DATABASE_URL` | SQLAlchemy URL used by backend and worker |
| `REDIS_URL` | Redis used by the application |
| `CELERY_BROKER_URL` | Celery Broker |
| `CELERY_RESULT_BACKEND` | Celery Result Backend |
| `ADMIN_TOKEN` | Access token for administrative endpoints |
| `ADMIN_RATE_WINDOW_SECONDS` | Rate limit window (seconds) for admin endpoints |
| `ADMIN_RATE_LIMIT_COLLECT` | Max `/collect/manual` calls per window and IP |
| `ADMIN_RATE_LIMIT_ANALYTICS` | Max `/analytics/process` calls per window and IP |
| `ADMIN_RATE_LIMIT_RAG` | Max `/rag/index` calls per window and IP |
| `PORTAL_TRANSPARENCIA_ANO` | Fiscal year queried in the Transparency Portal |
| `PORTAL_TRANSPARENCIA_LIMIT` | Bidding limit per collection |
| `PORTAL_TRANSPARENCIA_PAGE_SIZE` | Fixed page size for biddings |
| `PORTAL_TRANSPARENCIA_DESPESAS_SECRETARIA_LIMIT` | Expense limit per department |
| `PORTAL_TRANSPARENCIA_CONTRATOS_LIMIT` | Contract limit |
| `PORTAL_TRANSPARENCIA_RECEITAS_LIMIT` | Revenue limit |
| `FINANCE_DATA_STALE_HOURS` | Window to alert stale collection on the dashboard and analytics |

Never push the `.env` file to GitHub. Use only `.env.example`.

## Project Structure

```text
backend/
  app/
    analytics/      # entity extraction and analytical queries
    collectors/     # official source collectors
    models/         # SQLAlchemy models
    tasks/          # Celery worker, beat and processing
frontend/
  index.html
  index.css
  app.js
docs/
  arquitetura.md
  fontes_de_dados.md
  portal_transparencia.md
docker-compose.yml
```

## Development Routine

```bash
docker compose ps
docker compose logs -f backend worker scheduler
docker compose restart backend worker
docker compose restart frontend
docker compose down
```

To restart from scratch by deleting local volumes:

```bash
docker compose down -v
docker compose up --build -d
```

Use `down -v` carefully, as this deletes local PostgreSQL data.

## Precautions and Limits

- The system does not create simulated data.
- If a real collection fails, the error must be logged.
- Anonymized or masked data by the official source must be preserved as received.
- Financial values depend on the public endpoints available in the Transparency Portal.
- If an active collection limit is set, values are treated as partial.
- The `/ask` endpoint performs simple rule-based interpretation and local search; it does not replace formal auditing.

## Roadmap

- Analytical detailing of the City Council by councilor.
- Improvements in interpretive search.
- Historical comparisons by year.
- Export queries to CSV.
- Automated tests for critical collectors and endpoints.
