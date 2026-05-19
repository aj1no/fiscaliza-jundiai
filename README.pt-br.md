# Fiscaliza Jundiai

*[Read in English](README.md)*

Painel publico para coletar, organizar, pesquisar e analisar dados oficiais de Jundiai/SP.

O projeto nasceu como coletor documental e evoluiu para um painel analitico simples, com busca textual, indicadores financeiros e rastreabilidade para as fontes oficiais.

> Aviso: este e um projeto independente de interesse cidadao, sem vinculo institucional oficial com Prefeitura ou Camara.

## Status do projeto

MVP funcional em Docker:

- Frontend estatico servido por Nginx.
- API FastAPI.
- PostgreSQL para persistencia.
- Redis + Celery para coletas e processamento em segundo plano.
- Coleta real da Imprensa Oficial, Camara/SAPL e Portal da Transparencia.
- Processamento de PDF, HTML, JSON e CSV.
- Busca textual unificada.
- Painel financeiro com metadados de confiabilidade (consolidado, parcial, inseguro_para_soma).

## Fontes de dados

| Fonte | Conteudo | Formatos |
| --- | --- | --- |
| Imprensa Oficial de Jundiai | Publicacoes oficiais, editais e atos administrativos | PDF |
| Camara Municipal / SAPL | Resumos de sessoes plenarias | HTML |
| Portal da Transparencia | Licitacoes, contratos, receitas e despesas por secretaria | JSON e CSV |

Todas as telas mantem links para a origem oficial sempre que disponivel.

## Funcionalidades

- Coleta manual e agendada de documentos oficiais.
- Deduplicacao por hash e chave composta, com atualizacao de dados financeiros vivos quando o conteudo muda.
- Extracao de texto para busca em PDF, HTML, JSON e CSV.
- Busca por titulo, fonte, tipo de documento e texto extraido.
- Painel publico com filtros por fonte e tipo.
- Paginacao de documentos por fonte.
- Indicadores de transparencia:
  - valor coletado de arrecadacao;
  - valor coletado de gasto pago;
  - execucao financeira observada;
  - valor coletado empenhado;
  - valor coletado liquidado;
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

Os endpoints administrativos exigem token no header `X-Admin-Token`.

PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/collect/manual -Headers @{ "X-Admin-Token" = "SEU_TOKEN" }
```

Bash:

```bash
curl -X POST http://localhost:8000/collect/manual -H "X-Admin-Token: SEU_TOKEN"
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

Os endpoints financeiros retornam metadados de qualidade, por exemplo:

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

### Administracao (token obrigatorio)

```text
POST /collect/manual
POST /analytics/process
POST /rag/index
GET /tasks/{task_id}
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
| `ADMIN_TOKEN` | Token de acesso para endpoints administrativos |
| `ADMIN_RATE_WINDOW_SECONDS` | Janela de rate limit (segundos) dos endpoints administrativos |
| `ADMIN_RATE_LIMIT_COLLECT` | Maximo de chamadas de `/collect/manual` por janela e IP |
| `ADMIN_RATE_LIMIT_ANALYTICS` | Maximo de chamadas de `/analytics/process` por janela e IP |
| `ADMIN_RATE_LIMIT_RAG` | Maximo de chamadas de `/rag/index` por janela e IP |
| `PORTAL_TRANSPARENCIA_ANO` | Exercicio consultado no Portal da Transparencia |
| `PORTAL_TRANSPARENCIA_LIMIT` | Limite de licitacoes por coleta |
| `PORTAL_TRANSPARENCIA_PAGE_SIZE` | Tamanho fixo da pagina de licitacoes |
| `PORTAL_TRANSPARENCIA_DESPESAS_SECRETARIA_LIMIT` | Limite de despesas por secretaria |
| `PORTAL_TRANSPARENCIA_CONTRATOS_LIMIT` | Limite de contratos |
| `PORTAL_TRANSPARENCIA_RECEITAS_LIMIT` | Limite de receitas |
| `FINANCE_DATA_STALE_HOURS` | Janela para alertar coleta antiga no painel e analytics |



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
- Se houver limite de coleta ativo, os valores sao tratados como parciais.
- O endpoint `/ask` faz interpretacao simples por regras e busca local; nao substitui auditoria formal.

## Roadmap

- Detalhamento analitico da Camara Municipal por vereador.
- Melhorias na busca interpretativa.
- Comparativos historicos por ano.
- Exportacao de consultas em CSV.
- Testes automatizados para coletores e endpoints criticos.
