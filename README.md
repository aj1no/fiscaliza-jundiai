# Fiscaliza Jundiai

Painel publico para coletar, organizar, pesquisar e analisar dados oficiais de Jundiai/SP.

O projeto nasceu como coletor documental e evoluiu para um painel analitico simples, com busca textual, indicadores financeiros e rastreabilidade para as fontes oficiais.

## Status do projeto

MVP funcional em Docker:

- Frontend estatico servido por Nginx.
- API FastAPI.
- PostgreSQL para persistencia.
- Redis + Celery para coletas e processamento em segundo plano.
- Coleta real da Imprensa Oficial, Camara/SAPL e Portal da Transparencia.
- Processamento de PDF, HTML, JSON e CSV.
- Busca textual unificada.
- Painel financeiro com arrecadacao, despesas pagas, empenhadas e liquidadas por secretaria.

## Fontes de dados

| Fonte | Conteudo | Formatos |
| --- | --- | --- |
| Imprensa Oficial de Jundiai | Publicacoes oficiais, editais e atos administrativos | PDF |
| Camara Municipal / SAPL | Resumos de sessoes plenarias | HTML |
| Portal da Transparencia | Licitacoes, contratos, receitas e despesas por secretaria | JSON e CSV |

Todas as telas mantem links para a origem oficial sempre que disponivel.

## Funcionalidades

- Coleta manual e agendada de documentos oficiais.
- Deduplicacao por hash e chave composta.
- Extracao de texto para busca em PDF, HTML, JSON e CSV.
- Busca por titulo, fonte, tipo de documento e texto extraido.
- Painel publico com filtros por fonte e tipo.
- Paginacao de documentos por fonte.
- Indicadores de transparencia:
  - total arrecadado;
  - total pago;
  - saldo simples;
  - total empenhado;
  - total liquidado;
  - ranking completo de gastos por secretaria.
- Endpoints analiticos para perguntas sobre gastos, receitas, temas, bairros e vereadores.

## Como rodar

### 1. Criar o arquivo de ambiente

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

### 2. Subir os containers

```bash
docker compose up --build -d
```

### 3. Verificar saude da API

PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/health
```

Bash:

```bash
curl http://localhost:8000/health
```

### 4. Acessar

- Frontend: http://localhost:8080
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs

## Coleta manual

PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/collect/manual
```

Bash:

```bash
curl -X POST http://localhost:8000/collect/manual
```

Depois acompanhe os logs:

```bash
docker compose logs -f worker
```

## Endpoints uteis

### Documentos

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

## Variaveis de ambiente

As principais configuracoes ficam em `.env.example`.

| Variavel | Uso |
| --- | --- |
| `POSTGRES_USER` | Usuario do PostgreSQL |
| `POSTGRES_PASSWORD` | Senha local do PostgreSQL |
| `POSTGRES_DB` | Banco usado pela aplicacao |
| `DATABASE_URL` | URL SQLAlchemy usada pelo backend e worker |
| `REDIS_URL` | Redis usado pela aplicacao |
| `CELERY_BROKER_URL` | Broker do Celery |
| `CELERY_RESULT_BACKEND` | Backend de resultados do Celery |
| `PORTAL_TRANSPARENCIA_ANO` | Exercicio consultado no Portal da Transparencia |
| `PORTAL_TRANSPARENCIA_LIMIT` | Limite de licitacoes por coleta |
| `PORTAL_TRANSPARENCIA_PAGE_SIZE` | Tamanho fixo da pagina de licitacoes |
| `PORTAL_TRANSPARENCIA_DESPESAS_SECRETARIA_LIMIT` | Limite de despesas por secretaria |
| `PORTAL_TRANSPARENCIA_CONTRATOS_LIMIT` | Limite de contratos |
| `PORTAL_TRANSPARENCIA_RECEITAS_LIMIT` | Limite de receitas |

Nunca envie o arquivo `.env` para o GitHub. Use apenas `.env.example`.

## Estrutura do projeto

```text
backend/
  app/
    analytics/      # extracao de entidades e consultas analiticas
    collectors/     # coletores das fontes oficiais
    models/         # modelos SQLAlchemy
    tasks/          # Celery worker, beat e processamento
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

## Rotina de desenvolvimento

```bash
docker compose ps
docker compose logs -f backend worker scheduler
docker compose restart backend worker
docker compose restart frontend
docker compose down
```

Para reiniciar do zero apagando volumes locais:

```bash
docker compose down -v
docker compose up --build -d
```

Use `down -v` com cuidado, pois isso apaga os dados locais do PostgreSQL.

## Cuidados e limites

- O sistema nao cria dados simulados.
- Se uma coleta real falhar, o erro deve ser registrado em log.
- Dados anonimizados ou mascarados pela fonte oficial devem ser preservados como vieram.
- Os valores financeiros dependem dos endpoints publicos disponiveis no Portal da Transparencia.
- O endpoint `/ask` faz interpretacao simples por regras e busca local; nao substitui auditoria formal.

## Roadmap

- Detalhamento analitico da Camara Municipal por vereador.
- Melhorias na busca interpretativa.
- Comparativos historicos por ano.
- Exportacao de consultas em CSV.
- Testes automatizados para coletores e endpoints criticos.
