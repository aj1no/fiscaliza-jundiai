import hashlib
import json
import logging
import math
import os
import re
import uuid
from collections import Counter

import requests

from app.analytics.entity_extractor import normalize_text
from app.models import models


logger = logging.getLogger(__name__)

CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "250"))
VECTOR_DIMENSIONS = int(os.getenv("RAG_VECTOR_DIMENSIONS", "384"))
EMBEDDING_MODEL = "local-hash-v1"
QDRANT_URL = (os.getenv("QDRANT_URL") or "").rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "fiscaliza_jundiai_chunks")
QDRANT_TIMEOUT = int(os.getenv("QDRANT_TIMEOUT", "20"))

STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "entre", "na", "nas", "no", "nos", "o", "os", "ou", "para",
    "pela", "pelas", "pelo", "pelos", "por", "que", "se", "um", "uma",
    "jundiai", "municipio", "municipal",
}


def clean_chunk_text(text):
    return re.sub(r"\s+", " ", (text or "").replace("\x00", " ")).strip()


def split_text_into_chunks(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = clean_chunk_text(text)
    if not text:
        return []

    chunk_size = max(300, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size - 100))
    chunks = []
    start = 0

    while start < len(text):
        target_end = min(start + chunk_size, len(text))
        end = target_end
        if target_end < len(text):
            boundary = max(
                text.rfind(". ", start + int(chunk_size * 0.65), target_end),
                text.rfind("; ", start + int(chunk_size * 0.65), target_end),
                text.rfind(" ", start + int(chunk_size * 0.75), target_end),
            )
            if boundary > start:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

    return chunks


def tokenize_for_embedding(text):
    normalized = normalize_text(text)
    tokens = re.findall(r"\b[a-z0-9]{3,}\b", normalized)
    return [token for token in tokens if token not in STOPWORDS]


def build_local_embedding(text, dimensions=VECTOR_DIMENSIONS):
    tokens = tokenize_for_embedding(text)
    if not tokens:
        return {}

    counts = Counter(tokens)
    vector = {}
    for token, count in counts.items():
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        bucket = int(digest[:12], 16) % dimensions
        vector[str(bucket)] = vector.get(str(bucket), 0.0) + (1.0 + math.log(count))

    norm = math.sqrt(sum(value * value for value in vector.values()))
    if not norm:
        return {}
    return {key: round(value / norm, 6) for key, value in vector.items()}


def serialize_embedding(vector):
    return json.dumps(vector, sort_keys=True, separators=(",", ":"))


def deserialize_embedding(value):
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return {str(key): float(score) for key, score in data.items()}


def cosine_similarity_sparse(left, right):
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def sparse_to_dense(vector, dimensions=VECTOR_DIMENSIONS):
    dense = [0.0] * dimensions
    for key, value in vector.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= index < dimensions:
            dense[index] = float(value)
    return dense


def chunk_hash(processed_id, chunk_index, text):
    raw = f"{processed_id}:{chunk_index}:{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def qdrant_enabled():
    return bool(QDRANT_URL)


def _qdrant_headers():
    headers = {"Content-Type": "application/json"}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    return headers


def _qdrant_request(method, path, **kwargs):
    if not qdrant_enabled():
        raise RuntimeError("Qdrant nao configurado")

    url = f"{QDRANT_URL}{path}"
    response = requests.request(
        method,
        url,
        headers=_qdrant_headers(),
        timeout=QDRANT_TIMEOUT,
        **kwargs,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def ensure_qdrant_collection():
    if not qdrant_enabled():
        return False

    path = f"/collections/{QDRANT_COLLECTION}"
    try:
        _qdrant_request("GET", path)
        return True
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise

    payload = {
        "vectors": {
            "size": VECTOR_DIMENSIONS,
            "distance": "Cosine",
        }
    }
    _qdrant_request("PUT", path, json=payload)
    logger.info("Colecao Qdrant criada: %s", QDRANT_COLLECTION)
    return True


def _qdrant_point_id(chunk):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fiscaliza-jundiai:{chunk.hash_chunk}"))


def qdrant_upsert_chunks(chunks):
    if not qdrant_enabled() or not chunks:
        return 0

    ensure_qdrant_collection()
    points = []
    for chunk in chunks:
        vector = sparse_to_dense(deserialize_embedding(chunk.embedding))
        doc = chunk.documento_bruto
        points.append({
            "id": _qdrant_point_id(chunk),
            "vector": vector,
            "payload": {
                "chunk_id": chunk.id,
                "documento_processado_id": chunk.documento_processado_id,
                "documento_bruto_id": chunk.documento_bruto_id,
                "chunk_index": chunk.chunk_index,
                "fonte": doc.fonte if doc else None,
                "tipo_documento": doc.tipo_documento if doc else None,
                "titulo": doc.titulo if doc else None,
                "url_origem": doc.url_origem if doc else None,
                "data_publicacao": doc.data_publicacao.isoformat() if doc and doc.data_publicacao else None,
                "texto": chunk.texto_limpo[:1800],
                "hash_chunk": chunk.hash_chunk,
                "embedding_model": chunk.embedding_model,
            },
        })

    _qdrant_request(
        "PUT",
        f"/collections/{QDRANT_COLLECTION}/points",
        params={"wait": "true"},
        json={"points": points},
    )
    logger.info("Qdrant: %s chunks enviados para %s", len(points), QDRANT_COLLECTION)
    return len(points)


def qdrant_search_chunks(db, query, limit=8):
    if not qdrant_enabled():
        return []

    query_embedding = build_local_embedding(query)
    if not query_embedding:
        return []

    ensure_qdrant_collection()
    response = _qdrant_request(
        "POST",
        f"/collections/{QDRANT_COLLECTION}/points/search",
        json={
            "vector": sparse_to_dense(query_embedding),
            "limit": limit,
            "with_payload": True,
        },
    )
    results = response.get("result") or []
    scored = []
    for item in results:
        payload = item.get("payload") or {}
        chunk = None
        chunk_id = payload.get("chunk_id")
        if chunk_id:
            chunk = db.query(models.DocumentoChunk).get(chunk_id)
        scored.append({
            "score": round(float(item.get("score") or 0), 4),
            "chunk": chunk,
            "payload": payload,
            "vector_store": "qdrant",
        })
    return scored


def qdrant_status():
    if not qdrant_enabled():
        return {
            "enabled": False,
            "collection": QDRANT_COLLECTION,
            "url_configured": False,
        }

    try:
        result = _qdrant_request("GET", f"/collections/{QDRANT_COLLECTION}")
        points_count = (result.get("result") or {}).get("points_count")
        return {
            "enabled": True,
            "collection": QDRANT_COLLECTION,
            "url_configured": True,
            "points_count": points_count,
            "status": result.get("status"),
        }
    except Exception as exc:
        return {
            "enabled": True,
            "collection": QDRANT_COLLECTION,
            "url_configured": True,
            "error": str(exc),
        }


def rebuild_chunks_for_processed_document(db, processed_doc):
    if not processed_doc or not processed_doc.texto_limpo:
        return 0

    db.query(models.DocumentoChunk).filter(
        models.DocumentoChunk.documento_processado_id == processed_doc.id
    ).delete()

    chunks = split_text_into_chunks(processed_doc.texto_limpo)
    chunk_rows = []
    for index, chunk in enumerate(chunks):
        normalized_chunk = clean_chunk_text(chunk)
        embedding = build_local_embedding(normalized_chunk)
        chunk_row = models.DocumentoChunk(
            documento_processado_id=processed_doc.id,
            documento_bruto_id=processed_doc.documento_bruto_id,
            chunk_index=index,
            texto=chunk,
            texto_limpo=normalized_chunk,
            embedding=serialize_embedding(embedding),
            embedding_model=EMBEDDING_MODEL,
            hash_chunk=chunk_hash(processed_doc.id, index, normalized_chunk),
            tamanho=len(normalized_chunk),
        )
        db.add(chunk_row)
        chunk_rows.append(chunk_row)

    db.flush()
    try:
        qdrant_upsert_chunks(chunk_rows)
    except Exception as exc:
        logger.error("Falha ao enviar chunks para Qdrant: %s", exc)

    return len(chunks)


def ensure_chunks_for_processed_document(db, processed_doc):
    if not processed_doc:
        return 0

    existing = db.query(models.DocumentoChunk).filter(
        models.DocumentoChunk.documento_processado_id == processed_doc.id
    ).count()
    if existing:
        return existing
    return rebuild_chunks_for_processed_document(db, processed_doc)


def search_chunks(db, query, limit=8):
    try:
        qdrant_results = qdrant_search_chunks(db, query, limit=limit)
        if qdrant_results:
            return qdrant_results
    except Exception as exc:
        logger.error("Falha na busca Qdrant; usando fallback local: %s", exc)

    query_embedding = build_local_embedding(query)
    if not query_embedding:
        return []

    rows = db.query(models.DocumentoChunk).join(
        models.DocumentoBruto,
        models.DocumentoChunk.documento_bruto_id == models.DocumentoBruto.id,
    ).limit(5000).all()

    normalized_query = normalize_text(query)
    terms = [term for term in tokenize_for_embedding(query) if len(term) > 3]
    scored = []
    for chunk in rows:
        vector = deserialize_embedding(chunk.embedding)
        vector_score = cosine_similarity_sparse(query_embedding, vector)
        text = normalize_text(" ".join(filter(None, [
            chunk.texto_limpo,
            chunk.documento_bruto.titulo if chunk.documento_bruto else None,
            chunk.documento_bruto.fonte if chunk.documento_bruto else None,
            chunk.documento_bruto.tipo_documento if chunk.documento_bruto else None,
        ])))
        lexical_score = sum(text.count(term) for term in terms) * 0.035
        phrase_boost = 0.12 if normalized_query and normalized_query in text else 0
        score = vector_score + lexical_score + phrase_boost
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "score": round(score, 4),
            "chunk": chunk,
            "payload": None,
            "vector_store": "postgres_local",
        }
        for score, chunk in scored[:limit]
    ]
