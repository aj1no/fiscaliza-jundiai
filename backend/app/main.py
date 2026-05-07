from fastapi import FastAPI, Depends, HTTPException, Query
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
def trigger_manual_collect():
    task = worker.run_all_collectors.delay()
    return {"message": "Coleta manual iniciada", "task_id": task.id}


@app.get("/documents", response_model=List[schemas.DocumentoBruto])
def list_documents(
    q: Optional[str] = None,
    fonte: Optional[str] = None,
    limit: int = 50,
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
    ).limit(50).all()

    return results


@app.post("/analytics/process")
def trigger_analytics_processing(limit: Optional[int] = None):
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
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return analytics_service.receitas_analytics(db, ano=ano, termo=termo, limit=limit)


@app.get("/analytics/camara/financeiro")
def analytics_camara_financeiro(ano: Optional[int] = None):
    return analytics_service.camara_financeiro(ano)


@app.get("/analytics/temas")
def analytics_temas(limit: int = 20, db: Session = Depends(get_db)):
    return analytics_service.temas_frequentes(db, limit=limit)


@app.get("/analytics/bairros")
def analytics_bairros(limit: int = 20, db: Session = Depends(get_db)):
    return analytics_service.bairros_frequentes(db, limit=limit)


@app.get("/analytics/secretarias")
def analytics_secretarias(
    tipo_documento: Optional[str] = None,
    limit: int = 20,
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
    limit: int = 8,
    db: Session = Depends(get_db),
):
    return analytics_service.retrieve_chunks(db, q, limit=limit)


@app.post("/rag/index")
def trigger_rag_index(limit: Optional[int] = None, force: bool = False):
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
