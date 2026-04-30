import logging
import json
import os
import re
from datetime import datetime

from bs4 import BeautifulSoup
from celery import Celery
from celery.schedules import crontab

from app.database.database import SessionLocal, engine
from app.models import models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL.replace("/0", "/1"))

celery_app = Celery(
    "fiscaliza_tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)
celery = celery_app

models.Base.metadata.create_all(bind=engine)
engine.dispose()

celery_app.conf.beat_schedule = {
    "collect-imprensa-oficial-6h": {
        "task": "collect_imprensa_oficial",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "collect-camara-sessoes-12h": {
        "task": "collect_camara_sessoes",
        "schedule": crontab(minute=0, hour="*/12"),
    },
    "collect-portal-transparencia-daily": {
        "task": "collect_portal_transparencia",
        "schedule": crontab(minute=0, hour=3),
    },
}
celery_app.conf.timezone = "America/Sao_Paulo"


@celery_app.task(name="collect_imprensa_oficial")
def collect_imprensa_oficial():
    from app.collectors.imprensa_oficial import ImprensaOficialCollector

    logger.info("Iniciando task: collect_imprensa_oficial")
    try:
        collector = ImprensaOficialCollector()
        count = collector.collect()
        return f"Sucesso: {count} documentos coletados"
    except Exception as e:
        logger.error("Erro em collect_imprensa_oficial: %s", e)
        return f"Erro: {str(e)}"


@celery_app.task(name="collect_camara_sessoes")
def collect_camara_sessoes():
    from app.collectors.camara_sessoes import CamaraSessoesCollector

    logger.info("Iniciando task: collect_camara_sessoes")
    try:
        collector = CamaraSessoesCollector()
        count = collector.collect()
        return f"Sucesso: {count} documentos coletados"
    except Exception as e:
        logger.error("Erro em collect_camara_sessoes: %s", e)
        return f"Erro: {str(e)}"


@celery_app.task(name="collect_portal_transparencia")
def collect_portal_transparencia():
    from app.collectors.portal_transparencia import PortalTransparenciaCollector

    logger.info("Iniciando task: collect_portal_transparencia")
    try:
        collector = PortalTransparenciaCollector()
        count = collector.collect()
        if count:
            process_unprocessed_documents.delay()
        return f"Sucesso: {count} registros coletados"
    except Exception as e:
        logger.error("Erro em collect_portal_transparencia: %s", e)
        return f"Erro: {str(e)}"


@celery_app.task(name="run_all_collectors")
def run_all_collectors():
    logger.info("Iniciando run_all_collectors")
    collect_imprensa_oficial.delay()
    collect_camara_sessoes.delay()
    collect_portal_transparencia.delay()
    return "Todas as coletas foram enfileiradas"


def _extract_html_text(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    return soup.get_text(" ", strip=True)


def extract_text_from_json(data: dict) -> str:
    ignored_keys = {
        "id",
        "hash",
        "hash_arquivo",
        "hash_texto",
        "metadata",
        "metadados",
        "conteudo_hash",
        "checksum",
    }
    preferred_sections = ("normalizado", "registro_bruto", "rastreabilidade")
    parts = []

    def walk(value, parent_key=None):
        key = str(parent_key or "").lower()
        if key in ignored_keys:
            return

        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, child_key)
            return

        if isinstance(value, list):
            for item in value:
                walk(item, parent_key)
            return

        if value is None or isinstance(value, bool):
            return

        if isinstance(value, (int, float)):
            if key and key not in ignored_keys:
                parts.append(f"{parent_key}: {value}")
            return

        text = str(value).replace("\x00", "").strip()
        if not text:
            return

        if key:
            parts.append(f"{parent_key}: {text}")
        else:
            parts.append(text)

    if isinstance(data, dict):
        for section in preferred_sections:
            if section in data:
                walk(data[section], section)

        for key, value in data.items():
            if key not in preferred_sections:
                walk(value, key)

    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _extract_json_text(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("JSON bruto nao e um objeto")
    return extract_text_from_json(data)


@celery_app.task(name="process_document")
def process_document(doc_id):
    from app.processors.pdf_extractor import PDFExtractor
    from app.processors.text_classifier import TextClassifier

    db = SessionLocal()
    doc = None
    try:
        doc = db.query(models.DocumentoBruto).get(doc_id)
        if not doc:
            return "Documento nao encontrado"

        existing_proc = db.query(models.DocumentoProcessado).filter(
            models.DocumentoProcessado.documento_bruto_id == doc.id
        ).first()
        if existing_proc and existing_proc.texto_limpo:
            if doc.status_processamento != "processado":
                doc.status_processamento = "processado"
                doc.erro_processamento = None
                db.commit()
            return f"Documento {doc_id} ja processado"

        formato = (doc.formato or "").lower()
        if not doc.caminho_arquivo:
            raise ValueError("Documento sem caminho_arquivo")

        if formato == "pdf":
            texto = PDFExtractor.extract_text(doc.caminho_arquivo)
        elif formato == "html":
            texto = _extract_html_text(doc.caminho_arquivo)
        elif formato == "json":
            texto = _extract_json_text(doc.caminho_arquivo)
        else:
            raise ValueError(f"Formato nao suportado para processamento: {doc.formato}")

        texto = (texto or "").replace("\x00", "")
        texto_limpo = re.sub(r"\s+", " ", texto).strip()
        if not texto_limpo:
            raise ValueError("Nenhum texto extraido do documento")

        classifier = TextClassifier()
        tema = classifier.classify(texto_limpo)

        if existing_proc:
            existing_proc.texto_extraido = texto
            existing_proc.texto_limpo = texto_limpo
            existing_proc.tema_principal = tema
            existing_proc.confianca = 0.8
            existing_proc.status = "processado"
        else:
            db.add(models.DocumentoProcessado(
                documento_bruto_id=doc.id,
                texto_extraido=texto,
                texto_limpo=texto_limpo,
                tema_principal=tema,
                confianca=0.8,
                status="processado",
            ))

        doc.status_processamento = "processado"
        doc.erro_processamento = None
        doc.atualizado_em = datetime.utcnow()
        db.commit()
        logger.info("Documento %s processado com %s caracteres", doc_id, len(texto_limpo))
        return f"Documento {doc_id} processado"
    except Exception as e:
        logger.error("Erro ao processar documento %s: %s", doc_id, e)
        if doc:
            db.rollback()
            doc.status_processamento = "erro"
            doc.erro_processamento = str(e)
            doc.atualizado_em = datetime.utcnow()
            db.commit()
        return f"Erro: {str(e)}"
    finally:
        db.close()


@celery_app.task(name="process_unprocessed_documents")
def process_unprocessed_documents():
    db = SessionLocal()
    try:
        docs = db.query(models.DocumentoBruto).outerjoin(
            models.DocumentoProcessado,
            models.DocumentoBruto.id == models.DocumentoProcessado.documento_bruto_id,
        ).filter(
            (models.DocumentoProcessado.id.is_(None)) |
            (models.DocumentoProcessado.texto_limpo.is_(None)) |
            (models.DocumentoProcessado.texto_limpo == "")
        ).all()
        doc_ids = [doc.id for doc in docs]
    finally:
        db.close()

    for doc_id in doc_ids:
        process_document.delay(doc_id)

    return f"{len(doc_ids)} documentos enfileirados para processamento"
