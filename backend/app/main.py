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
