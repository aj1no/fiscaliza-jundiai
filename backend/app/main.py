from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from fastapi import FastAPI, Depends, HTTPException, Query, Header, Request
from sqlalchemy.orm import Session
from typing import List, Optional
import os
import redis
from sqlalchemy import text, or_
from app.database.database import engine, SessionLocal, get_db
from app.models import models
from app.schemas import schemas
from app.tasks import worker
from app.analytics import service as analytics_service
from app.analytics.entity_extractor import seed_reference_data
from fastapi.middleware.cors import CORSMiddleware

# Garantir que as tabelas sejam criadas ao iniciar (se o banco estiver pronto)
# Nota: Em producao, recomenda-se usar migracoes (Alembic)
models.Base.metadata.create_all(bind=engine)


def apply_runtime_schema_fixes() -> None:
    """
    Ajustes idempotentes para ambientes MVP sem Alembic:
    - remove deduplicacao forcada por URL (inadequada para dados financeiros vivos)
    - converte colunas monetarias para NUMERIC com precisao de centavos
    """
    try:
        with engine.begin() as conn:
            if conn.dialect.name != "postgresql":
                return

            conn.execute(
                text(
                    """
                    ALTER TABLE IF EXISTS documentos_brutos
                    DROP CONSTRAINT IF EXISTS documentos_brutos_url_origem_key
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_documentos_brutos_url_origem
                    ON documentos_brutos (url_origem)
                    """
                )
            )

            numeric_alters = [
                (
                    "despesas",
                    "valor_empenhado",
                    "numeric(18,2)",
                    "CASE WHEN valor_empenhado IS NULL THEN NULL ELSE round(valor_empenhado::numeric, 2) END",
                ),
                (
                    "despesas",
                    "valor_liquidado",
                    "numeric(18,2)",
                    "CASE WHEN valor_liquidado IS NULL THEN NULL ELSE round(valor_liquidado::numeric, 2) END",
                ),
                (
                    "despesas",
                    "valor_pago",
                    "numeric(18,2)",
                    "CASE WHEN valor_pago IS NULL THEN NULL ELSE round(valor_pago::numeric, 2) END",
                ),
                (
                    "receitas",
                    "valor_orcado",
                    "numeric(18,2)",
                    "CASE WHEN valor_orcado IS NULL THEN NULL ELSE round(valor_orcado::numeric, 2) END",
                ),
                (
                    "receitas",
                    "valor_arrecadado",
                    "numeric(18,2)",
                    "CASE WHEN valor_arrecadado IS NULL THEN NULL ELSE round(valor_arrecadado::numeric, 2) END",
                ),
                (
                    "receitas",
                    "percentual",
                    "numeric(12,6)",
                    "CASE WHEN percentual IS NULL THEN NULL ELSE percentual::numeric(12,6) END",
                ),
                (
                    "servidores_remuneracao",
                    "valor_total_venc",
                    "numeric(18,2)",
                    "CASE WHEN valor_total_venc IS NULL THEN NULL ELSE round(valor_total_venc::numeric, 2) END",
                ),
                (
                    "servidores_remuneracao",
                    "valor_total_mes",
                    "numeric(18,2)",
                    "CASE WHEN valor_total_mes IS NULL THEN NULL ELSE round(valor_total_mes::numeric, 2) END",
                ),
                (
                    "servidores_remuneracao",
                    "valor_salario_base",
                    "numeric(18,2)",
                    "CASE WHEN valor_salario_base IS NULL THEN NULL ELSE round(valor_salario_base::numeric, 2) END",
                ),
            ]

            for table_name, column_name, target_type, using_expression in numeric_alters:
                conn.execute(
                    text(
                        f"""
                        ALTER TABLE IF EXISTS {table_name}
                        ALTER COLUMN {column_name} TYPE {target_type}
                        USING {using_expression}
                        """
                    )
                )
    except Exception as exc:
        print(f"[WARN] Falha ao aplicar ajustes de schema em runtime: {exc}")


apply_runtime_schema_fixes()

app = FastAPI(title="Fiscaliza Jundiai - API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
ADMIN_RATE_WINDOW_SECONDS = int(os.getenv("ADMIN_RATE_WINDOW_SECONDS", "60"))
ADMIN_RATE_LIMIT_COLLECT = int(os.getenv("ADMIN_RATE_LIMIT_COLLECT", "2"))
ADMIN_RATE_LIMIT_ANALYTICS = int(os.getenv("ADMIN_RATE_LIMIT_ANALYTICS", "1"))
ADMIN_RATE_LIMIT_RAG = int(os.getenv("ADMIN_RATE_LIMIT_RAG", "1"))

_admin_rate_hits = defaultdict(deque)
_admin_rate_lock = Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(key: str, max_calls: int, window_seconds: int) -> None:
    now = monotonic()
    with _admin_rate_lock:
        bucket = _admin_rate_hits[key]
        while bucket and (now - bucket[0]) > window_seconds:
            bucket.popleft()
        if len(bucket) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail="Muitas chamadas administrativas em pouco tempo. Tente novamente em instantes.",
            )
        bucket.append(now)


def require_admin(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Endpoint administrativo indisponivel: ADMIN_TOKEN nao configurado.",
        )
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Nao autorizado")


def _admin_guard(request: Request, scope: str, max_calls: int) -> None:
    rate_key = f"{scope}:{_client_ip(request)}"
    _enforce_rate_limit(rate_key, max_calls=max_calls, window_seconds=ADMIN_RATE_WINDOW_SECONDS)


def admin_collect_guard(request: Request, _: None = Depends(require_admin)) -> None:
    _admin_guard(request, "collect_manual", ADMIN_RATE_LIMIT_COLLECT)


def admin_analytics_guard(request: Request, _: None = Depends(require_admin)) -> None:
    _admin_guard(request, "analytics_process", ADMIN_RATE_LIMIT_ANALYTICS)


def admin_rag_guard(request: Request, _: None = Depends(require_admin)) -> None:
    _admin_guard(request, "rag_index", ADMIN_RATE_LIMIT_RAG)


@app.get("/")
def read_root():
    return {
        "app": "Fiscaliza Jundiai",
        "status": "online",
        "version": "2.1.0",
    }


@app.get("/health", response_model=schemas.HealthResponse)
def health_check(db: Session = Depends(get_db)):
    health = {"status": "ok", "database": "ok", "redis": "ok"}

    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        health["database"] = f"error: {str(e)}"
        health["status"] = "error"

    try:
        r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        r.ping()
    except Exception as e:
        health["redis"] = f"error: {str(e)}"
        health["status"] = "error"

    return health


@app.post("/collect/manual")
def trigger_manual_collect(_: None = Depends(admin_collect_guard)):
    task = worker.run_all_collectors.delay()
    return {"message": "Coleta manual iniciada", "task_id": task.id}


@app.get("/tasks/{task_id}")
def get_task_status(task_id: str, _: None = Depends(require_admin)):
    task_result = worker.celery_app.AsyncResult(task_id)
    payload = {
        "task_id": task_id,
        "status": task_result.state,
        "ready": task_result.ready(),
    }

    if task_result.state == "FAILURE":
        payload["error"] = str(task_result.result)
        payload["traceback"] = task_result.traceback
    elif task_result.ready():
        payload["result"] = task_result.result

    return payload


@app.get("/documents", response_model=List[schemas.DocumentoBruto])
def list_documents(
    q: Optional[str] = None,
    fonte: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(models.DocumentoBruto)

    if q:
        query = query.filter(models.DocumentoBruto.titulo.ilike(f"%{q}%"))
    if fonte:
        query = query.filter(models.DocumentoBruto.fonte == fonte)

    return query.order_by(models.DocumentoBruto.data_publicacao.desc()).limit(limit).all()


@app.get("/documents/{doc_id}", response_model=schemas.DocumentoBruto)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(models.DocumentoBruto).filter(models.DocumentoBruto.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
    return doc


@app.get("/search")
def search_documents(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    results = db.query(models.DocumentoBruto).join(
        models.DocumentoProcessado,
        models.DocumentoBruto.id == models.DocumentoProcessado.documento_bruto_id,
        isouter=True,
    ).filter(
        or_(
            models.DocumentoBruto.titulo.ilike(f"%{q}%"),
            models.DocumentoBruto.fonte.ilike(f"%{q}%"),
            models.DocumentoBruto.tipo_documento.ilike(f"%{q}%"),
            models.DocumentoProcessado.texto_extraido.ilike(f"%{q}%"),
            models.DocumentoProcessado.texto_limpo.ilike(f"%{q}%"),
        )
    ).limit(limit).all()

    return results


@app.post("/analytics/process")
def trigger_analytics_processing(
    limit: Optional[int] = Query(default=None, ge=1, le=5000),
    _: None = Depends(admin_analytics_guard),
):
    task = worker.process_all_entities.delay(limit)
    return {"message": "Processamento analitico iniciado", "task_id": task.id}


@app.get("/analytics/vereadores/{nome}")
def analytics_vereador(nome: str, db: Session = Depends(get_db)):
    seed_reference_data(db)
    return analytics_service.vereador_analytics(db, nome)


@app.get("/analytics/gastos/secretaria")
def analytics_gastos_secretaria(
    nome: str = Query(..., min_length=1),
    ano: Optional[int] = None,
    db: Session = Depends(get_db),
):
    seed_reference_data(db)
    return analytics_service.gastos_secretaria(db, nome, ano)


@app.get("/analytics/gastos/termo")
def analytics_gastos_termo(
    termo: str = Query(..., min_length=1),
    ano: Optional[int] = None,
    db: Session = Depends(get_db),
):
    return analytics_service.gastos_por_termo(db, termo, ano)


@app.get("/analytics/gastos/secretarias")
def analytics_gastos_secretarias(
    ano: Optional[int] = None,
    db: Session = Depends(get_db),
):
    return analytics_service.gastos_por_secretarias(db, ano)


@app.get("/analytics/receitas")
def analytics_receitas(
    ano: Optional[int] = None,
    termo: Optional[str] = None,
    limit: int = Query(100, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    return analytics_service.receitas_analytics(db, ano=ano, termo=termo, limit=limit)


@app.get("/analytics/servidores/remuneracao")
def analytics_servidores_remuneracao(
    ano: Optional[int] = None,
    mes: Optional[int] = None,
    secretaria: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return analytics_service.servidores_remuneracao(
        db,
        ano=ano,
        mes=mes,
        secretaria=secretaria,
        limit=limit,
    )


@app.get("/analytics/auditoria/remuneracao")
def analytics_auditoria_remuneracao(
    ano: int = Query(..., ge=2000, le=2100),
    ate_mes: int = Query(12, ge=1, le=12),
    db: Session = Depends(get_db),
):
    return analytics_service.auditoria_remuneracao_mensal(db, ano=ano, ate_mes=ate_mes)


@app.get("/analytics/camara/financeiro")
def analytics_camara_financeiro(ano: Optional[int] = None):
    return analytics_service.camara_financeiro(ano)


@app.get("/analytics/temas")
def analytics_temas(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)):
    return analytics_service.temas_frequentes(db, limit=limit)


@app.get("/analytics/bairros")
def analytics_bairros(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)):
    return analytics_service.bairros_frequentes(db, limit=limit)


@app.get("/analytics/secretarias")
def analytics_secretarias(
    tipo_documento: Optional[str] = None,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return analytics_service.secretarias_frequentes(db, tipo_documento=tipo_documento, limit=limit)


@app.get("/ask")
def ask(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    seed_reference_data(db)
    return analytics_service.interpret_question(db, q)


@app.get("/rag")
def rag(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    return analytics_service.rag_answer(db, q)


@app.get("/rag/search")
def rag_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=30),
    db: Session = Depends(get_db),
):
    return analytics_service.retrieve_chunks(db, q, limit=limit)


@app.post("/rag/index")
def trigger_rag_index(
    limit: Optional[int] = Query(default=None, ge=1, le=10000),
    force: bool = False,
    _: None = Depends(admin_rag_guard),
):
    task = worker.process_all_document_chunks.delay(limit, force)
    return {"message": "Indexacao RAG iniciada", "task_id": task.id}


@app.get("/rag/status")
def rag_status(db: Session = Depends(get_db)):
    from app.analytics.vector_rag import qdrant_status

    return {
        "documentos_processados": db.query(models.DocumentoProcessado).count(),
        "chunks": db.query(models.DocumentoChunk).count(),
        "embedding_model": "local-hash-v1",
        "qdrant": qdrant_status(),
    }
