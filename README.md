# Fiscaliza Jundiaí

Plataforma para acompanhamento e fiscalização de dados públicos de Jundiaí/SP.

## Tecnologias
- **Backend**: FastAPI
- **Frontend**: HTML/JS Estático (Nginx)
- **Banco de Dados**: PostgreSQL
- **Processamento Assíncrono**: Celery + Redis

## Como rodar localmente

### 1. Preparar Ambiente
Copie o arquivo de exemplo para o seu `.env`:
```bash
cp .env.example .env
```

### 2. Subir com Docker Compose
```bash
docker compose up --build
```

### 3. Acessar os Serviços
- **Frontend**: [http://localhost:8080](http://localhost:8080)
- **API Backend**: [http://localhost:8000](http://localhost:8000)
- **Documentação API (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)

## Comandos Úteis

### Ver logs
```bash
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f scheduler
```

### Reiniciar serviços
```bash
docker compose restart backend worker scheduler
```

### Parar e limpar volumes
```bash
docker compose down -v
```

### Executar coleta manual via terminal
```bash
curl -X POST http://localhost:8000/collect/manual
```

## Estrutura de Dados
- **Coleta Agendada**:
  - Imprensa Oficial: a cada 6 horas
  - Câmara: a cada 12 horas
  - Portal da Transparência: 1 vez ao dia (3h AM)
