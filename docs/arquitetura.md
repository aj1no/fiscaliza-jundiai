# Arquitetura do Projeto - Fiscaliza Jundiaí

O projeto "Fiscaliza Jundiaí" é uma plataforma de transparência pública composta por um ecossistema de coleta, processamento e disponibilização de dados.

## Componentes Principais

### 1. Backend (FastAPI + Python)
- **API**: Endpoints REST para consulta de documentos, gastos, vereadores e dashboards.
- **Collectors**: Módulos especializados em realizar o scraping de fontes oficiais.
- **Processors**: Motores de extração de texto (PDF/HTML), classificação por temas e extração de entidades (NLP).
- **Database**: PostgreSQL para armazenamento estruturado e busca textual.
- **Tasks**: Agendamento de coletas periódicas.

### 2. Frontend (Next.js / React)
- Interface moderna e responsiva.
- Dashboards dinâmicos com gráficos de evolução de gastos e proposições.
- Sistema de busca avançada com filtros.
- Páginas dedicadas para Executivo, Legislativo e Imprensa Oficial.

### 3. Banco de Dados (PostgreSQL)
- Estrutura relacional para normalização de dados de fontes distintas.
- Uso de Full-Text Search para busca rápida em conteúdos de documentos.

## Fluxo de Dados
1. **Coleta**: Crawlers acessam as fontes oficiais periodicamente.
2. **Ingestão**: Os dados brutos são salvos e os arquivos (PDFs) são baixados.
3. **Processamento**: 
   - Extração de texto.
   - Classificação automática (Saúde, Educação, etc.).
   - Identificação de entidades (Vereadores, Empresas, Bairros).
4. **Armazenamento**: Dados processados são indexados no banco.
5. **Consumo**: Usuários acessam via interface web.

## Stack Técnica
- **Linguagem**: Python 3.x
- **Framework Web**: FastAPI
- **Frontend**: Next.js + CSS Vanilla (Premium Design)
- **DB**: PostgreSQL
- **NLP**: spaCy / Regex
- **Scraping**: BeautifulSoup, Requests, Playwright (se necessário)
- **DevOps**: Docker + Docker Compose
