from collections import Counter
from datetime import datetime
import logging

import requests
from sqlalchemy import func, or_

from app.analytics.entity_extractor import normalize_text
from app.models import models

logger = logging.getLogger(__name__)

CAMARA_TRANSPARENCIA_BASE_URL = "https://web.cijun.sp.gov.br/camara/yc/v1"
CAMARA_TRANSPARENCIA_SITE_URL = "https://transparencia.jundiai.sp.leg.br/"
CAMARA_DESPESAS_URL = (
    "https://transparencia.jundiai.sp.leg.br/despesas/por-classificacao-orcamentaria/"
)
CAMARA_RECEITAS_URL = (
    "https://transparencia.jundiai.sp.leg.br/receita/por-classificacao-orcamentaria/"
)


def _document_payload(doc):
    return {
        "id": doc.id,
        "fonte": doc.fonte,
        "tipo_documento": doc.tipo_documento,
        "titulo": doc.titulo,
        "data_publicacao": doc.data_publicacao,
        "url_origem": doc.url_origem,
        "status_processamento": doc.status_processamento,
    }


def _meaningful_parts(value):
    ignored = {"de", "da", "do", "das", "dos", "e", "mun", "secr"}
    return [part for part in normalize_text(value).split() if len(part) > 3 and part not in ignored]


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _camara_get_json(path, params):
    url = f"{CAMARA_TRANSPARENCIA_BASE_URL}/{path.lstrip('/')}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "FiscalizaJundiai/1.0 (+https://transparencia.jundiai.sp.leg.br/)",
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
    except requests.exceptions.SSLError:
        logger.warning("Falha SSL no endpoint da Camara; repetindo com verificacao desativada: %s", url)
        response = requests.get(url, params=params, headers=headers, timeout=30, verify=False)

    response.raise_for_status()
    return {
        "url": response.url,
        "status_code": response.status_code,
        "params": params,
        "data": response.json(),
    }


def _camara_extract_rows(payload):
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("retorno"), list):
        return data["retorno"]
    if isinstance(data, list):
        return data
    return []


def _camara_row_payload(row, url_origem):
    return {
        "descricao": row.get("descricao"),
        "flag": row.get("flag"),
        "valor_inicial": _to_float(row.get("valor_inicial")),
        "creditos": _to_float(row.get("creditos")),
        "dotacao": _to_float(row.get("dotacao")),
        "total_empenhado": _to_float(row.get("empenhado")),
        "total_liquidado": _to_float(row.get("liquidado")),
        "total_pago": _to_float(row.get("pago")),
        "data_inicio": row.get("data_inicio"),
        "data_fim": row.get("data_fim"),
        "url_origem": url_origem,
    }


def vereador_analytics(db, nome):
    target = normalize_text(nome)
    vereador = db.query(models.Vereador).filter(
        or_(
            models.Vereador.nome_normalizado.ilike(f"%{target}%"),
            models.Vereador.nome.ilike(f"%{nome}%"),
        )
    ).first()

    if not vereador:
        return {
            "vereador": None,
            "documentos": [],
            "atuacoes": [],
            "temas": [],
            "linha_tempo": [],
            "observacao": "Vereador nao encontrado na camada analitica. Reprocesse entidades ou refine o nome.",
        }

    atuacoes = db.query(models.AtuacaoVereador).filter(
        models.AtuacaoVereador.vereador_id == vereador.id
    ).order_by(models.AtuacaoVereador.data_atuacao.desc().nullslast()).limit(50).all()

    tema_counter = Counter(a.tema for a in atuacoes if a.tema)
    documentos = [_document_payload(a.documento_bruto) for a in atuacoes if a.documento_bruto]

    return {
        "vereador": {
            "id": vereador.id,
            "nome": vereador.nome,
            "partido": vereador.partido,
            "ativo": vereador.ativo,
        },
        "documentos": documentos,
        "atuacoes": [
            {
                "id": a.id,
                "tipo_atuacao": a.tipo_atuacao,
                "titulo": a.titulo,
                "descricao": a.descricao,
                "data_atuacao": a.data_atuacao,
                "tema": a.tema,
                "bairro": a.bairro,
                "url_origem": a.url_origem,
                "confianca": a.confianca,
                "relacao_provavel": True,
            }
            for a in atuacoes
        ],
        "temas": [{"tema": tema, "total": total} for tema, total in tema_counter.most_common()],
        "linha_tempo": [
            {
                "data": a.data_atuacao,
                "titulo": a.titulo,
                "tipo_atuacao": a.tipo_atuacao,
                "url_origem": a.url_origem,
            }
            for a in atuacoes
        ],
        "observacao": "As atuacoes sao extraidas por regras textuais e representam relacoes provaveis com documentos oficiais.",
    }


def gastos_secretaria(db, nome, ano=None):
    normalized_query = normalize_text(nome)
    query = db.query(models.Despesa).join(
        models.DocumentoBruto,
        models.Despesa.fonte_documento_id == models.DocumentoBruto.id,
        isouter=True,
    )
    if ano:
        query = query.filter(models.Despesa.ano == ano)

    candidates = query.limit(1000).all()
    despesas = [
        item for item in candidates
        if normalized_query in normalize_text(item.secretaria)
        or any(part in normalize_text(item.secretaria) for part in normalized_query.split() if len(part) > 3)
    ]
    resumo_secretaria = [
        item for item in despesas
        if item.fonte_documento and item.fonte_documento.tipo_documento == "despesa_secretaria"
    ]
    contratos = [
        item for item in despesas
        if item.fonte_documento and item.fonte_documento.tipo_documento == "contrato"
    ][:50]
    despesas_para_total = resumo_secretaria or despesas

    if not despesas_para_total:
        docs_query = db.query(models.DocumentoBruto).join(
            models.DocumentoProcessado,
            models.DocumentoBruto.id == models.DocumentoProcessado.documento_bruto_id,
        ).filter(
            models.DocumentoBruto.fonte == "portal_transparencia",
            or_(
                models.DocumentoBruto.titulo.ilike(f"%{nome}%"),
                models.DocumentoProcessado.texto_limpo.ilike(f"%{nome}%"),
                models.DocumentoProcessado.texto_limpo.ilike(f"%{normalized_query}%"),
            ),
        )
        if ano:
            docs_query = docs_query.filter(models.DocumentoBruto.titulo.ilike(f"%{ano}%"))
        docs = docs_query.limit(50).all()
        return {
            "secretaria": nome,
            "ano": ano,
            "total_empenhado": None,
            "total_liquidado": None,
            "total_pago": None,
            "documentos": [_document_payload(doc) for doc in docs],
            "baseado_em_aproximacao_textual": True,
            "observacao": "Nao ha valores monetarios estruturados para esta consulta; foram retornados documentos oficiais relacionados quando encontrados.",
        }

    def total(field):
        values = [getattr(d, field) for d in despesas_para_total if getattr(d, field) is not None]
        return sum(values) if values else None

    docs = [d.fonte_documento for d in despesas_para_total if d.fonte_documento]
    return {
        "secretaria": nome,
        "ano": ano,
        "total_empenhado": total("valor_empenhado"),
        "total_liquidado": total("valor_liquidado"),
        "total_pago": total("valor_pago"),
        "documentos": [_document_payload(doc) for doc in docs],
        "registros": [
            {
                "id": d.id,
                "ano": d.ano,
                "secretaria": d.secretaria,
                "fornecedor": d.fornecedor,
                "cnpj": d.cnpj,
                "objeto": d.objeto,
                "valor_empenhado": d.valor_empenhado,
                "valor_liquidado": d.valor_liquidado,
                "valor_pago": d.valor_pago,
                "url_origem": d.url_origem,
            }
            for d in despesas_para_total
        ],
        "contratos_relacionados": [
            {
                "id": d.id,
                "ano": d.ano,
                "secretaria": d.secretaria,
                "fornecedor": d.fornecedor,
                "objeto": d.objeto,
                "valor_empenhado": d.valor_empenhado,
                "valor_pago": d.valor_pago,
                "url_origem": d.url_origem,
            }
            for d in contratos
        ],
        "baseado_em_aproximacao_textual": False,
        "observacao": (
            "Totais priorizam o resumo oficial por secretaria. Contratos relacionados aparecem como evidencias, "
            "sem dupla contagem no total."
        ),
    }


def gastos_por_termo(db, termo, ano=None):
    normalized = normalize_text(termo)
    ignored = {
        "quanto", "gastou", "gasto", "gastos", "com", "sobre", "para", "pela",
        "pelo", "foi", "foram", "em", "de", "da", "do", "das", "dos", "jundiai",
    }
    terms = [part for part in normalized.split() if len(part) > 2 and part not in ignored and not part.isdigit()]

    query = db.query(models.Despesa).join(
        models.DocumentoBruto,
        models.Despesa.fonte_documento_id == models.DocumentoBruto.id,
        isouter=True,
    )
    if ano:
        query = query.filter(models.Despesa.ano == ano)

    candidates = query.limit(1500).all()
    matches = []
    for item in candidates:
        if item.fonte_documento and item.fonte_documento.tipo_documento == "despesa_secretaria":
            continue
        haystack = normalize_text(" ".join(filter(None, [
            item.secretaria,
            item.fornecedor,
            item.objeto,
            item.url_origem,
            item.fonte_documento.titulo if item.fonte_documento else None,
        ])))
        if terms and all(term in haystack for term in terms):
            matches.append(item)

    if not matches and terms:
        matches = [
            item for item in candidates
            if item.fonte_documento and item.fonte_documento.tipo_documento != "despesa_secretaria"
            and any(term in normalize_text(" ".join(filter(None, [item.objeto, item.fornecedor, item.secretaria]))) for term in terms)
        ][:30]

    def total(field):
        values = [getattr(item, field) for item in matches if getattr(item, field) is not None]
        return sum(values) if values else None

    docs = [item.fonte_documento for item in matches if item.fonte_documento]
    return {
        "termo": termo,
        "ano": ano,
        "total_empenhado": total("valor_empenhado"),
        "total_liquidado": total("valor_liquidado"),
        "total_pago": total("valor_pago"),
        "documentos": [_document_payload(doc) for doc in docs],
        "registros": [
            {
                "id": item.id,
                "ano": item.ano,
                "secretaria": item.secretaria,
                "fornecedor": item.fornecedor,
                "objeto": item.objeto,
                "valor_empenhado": item.valor_empenhado,
                "valor_liquidado": item.valor_liquidado,
                "valor_pago": item.valor_pago,
                "url_origem": item.url_origem,
            }
            for item in matches
        ],
        "baseado_em_aproximacao_textual": True,
        "observacao": (
            "Resultado calculado por correspondencia textual em contratos/despesas oficiais. "
            "Quando nao houver valor pago/liquidado no endpoint, o campo permanece nulo."
        ),
    }


def gastos_por_secretarias(db, ano=None):
    query = db.query(models.Despesa).join(
        models.DocumentoBruto,
        models.Despesa.fonte_documento_id == models.DocumentoBruto.id,
    ).filter(models.DocumentoBruto.tipo_documento == "despesa_secretaria")
    if ano:
        query = query.filter(models.Despesa.ano == ano)

    rows = query.all()
    result = [
        {
            "secretaria": row.secretaria,
            "ano": row.ano,
            "total_empenhado": row.valor_empenhado,
            "total_liquidado": row.valor_liquidado,
            "total_pago": row.valor_pago,
            "url_origem": row.url_origem,
            "documento_id": row.fonte_documento_id,
        }
        for row in rows
    ]
    result.sort(key=lambda item: item.get("total_pago") or item.get("total_empenhado") or 0, reverse=True)
    return {
        "ano": ano,
        "secretarias": result,
        "observacao": "Totais por secretaria vindos do endpoint oficial de despesa por classificacao orcamentaria.",
    }


def receitas_analytics(db, ano=None, termo=None, limit=100):
    query = db.query(models.Receita)
    if ano:
        query = query.filter(models.Receita.ano == ano)

    rows = query.limit(2000).all()
    if termo:
        normalized = normalize_text(termo)
        rows = [
            row for row in rows
            if normalized in normalize_text(row.descricao)
            or normalized in normalize_text(row.classificacao)
        ]
    rows = rows[:limit]

    total_arrecadado = sum(row.valor_arrecadado for row in rows if row.valor_arrecadado is not None)
    total_orcado = sum(row.valor_orcado for row in rows if row.valor_orcado is not None)
    return {
        "ano": ano,
        "termo": termo,
        "total_orcado": total_orcado if rows else None,
        "total_arrecadado": total_arrecadado if rows else None,
        "registros": [
            {
                "id": row.id,
                "ano": row.ano,
                "classificacao": row.classificacao,
                "descricao": row.descricao,
                "valor_orcado": row.valor_orcado,
                "valor_arrecadado": row.valor_arrecadado,
                "percentual": row.percentual,
                "url_origem": row.url_origem,
            }
            for row in rows
        ],
        "observacao": "Arrecadacao baseada no endpoint publico de receita por classificacao orcamentaria.",
    }


def camara_financeiro(ano=None):
    ano = ano or datetime.now().year
    erros = []

    despesa_total_payload = None
    despesa_acoes_payload = None
    receita_payload = None

    despesa_total_params = {
        "ano": ano,
        "data_inicial": 1,
        "data_final": 12,
        "tipo": 1,
        "page": 1,
    }
    despesa_acoes_params = {
        "ano": ano,
        "data_inicial": 1,
        "data_final": 12,
        "tipo": 5,
        "page": 1,
        "per_page": 1000000,
    }
    receita_params = {
        "ano": ano,
        "mes_inicial": 1,
        "mes_final": 12,
    }

    try:
        despesa_total_payload = _camara_get_json(
            "Despesas/GetDespesasPorClassificacao",
            despesa_total_params,
        )
    except Exception as exc:
        logger.exception("Falha ao buscar despesas totais da Camara")
        erros.append({
            "categoria": "despesa_total",
            "url": f"{CAMARA_TRANSPARENCIA_BASE_URL}/Despesas/GetDespesasPorClassificacao",
            "params": despesa_total_params,
            "erro": str(exc),
        })

    try:
        despesa_acoes_payload = _camara_get_json(
            "Despesas/GetDespesasPorClassificacao",
            despesa_acoes_params,
        )
    except Exception as exc:
        logger.exception("Falha ao buscar despesas por acao da Camara")
        erros.append({
            "categoria": "despesa_acoes",
            "url": f"{CAMARA_TRANSPARENCIA_BASE_URL}/Despesas/GetDespesasPorClassificacao",
            "params": despesa_acoes_params,
            "erro": str(exc),
        })

    try:
        receita_payload = _camara_get_json(
            "Receitas/GetReceitasPorClassificacao",
            receita_params,
        )
    except Exception as exc:
        logger.exception("Falha ao buscar receitas da Camara")
        erros.append({
            "categoria": "receita",
            "url": f"{CAMARA_TRANSPARENCIA_BASE_URL}/Receitas/GetReceitasPorClassificacao",
            "params": receita_params,
            "erro": str(exc),
        })

    despesa_total_rows = _camara_extract_rows(despesa_total_payload or {})
    despesa_acoes_rows = _camara_extract_rows(despesa_acoes_payload or {})
    receita_rows = _camara_extract_rows(receita_payload or {})

    despesa_total = _camara_row_payload(despesa_total_rows[0], CAMARA_DESPESAS_URL) if despesa_total_rows else None
    acoes = [
        _camara_row_payload(row, CAMARA_DESPESAS_URL)
        for row in despesa_acoes_rows
        if any(_to_float(row.get(field)) for field in ("empenhado", "liquidado", "pago"))
    ]
    acoes.sort(key=lambda row: row.get("total_pago") or row.get("total_empenhado") or 0, reverse=True)

    receita_total_row = next(
        (
            row for row in receita_rows
            if "total geral" in normalize_text(row.get("descricao"))
        ),
        None,
    )
    receita_uso = receita_total_row
    if not receita_uso and receita_rows:
        receita_uso = {
            "descricao": "Soma de receitas retornadas",
            "arrecadado": sum(_to_float(row.get("arrecadado")) or 0 for row in receita_rows),
            "orcado": sum(_to_float(row.get("orcado")) or 0 for row in receita_rows),
            "percentual": None,
        }

    return {
        "ano": ano,
        "fonte": "camara_municipal",
        "orgao": "Camara Municipal de Jundiai",
        "url_origem": CAMARA_TRANSPARENCIA_SITE_URL,
        "despesa": {
            **(despesa_total or {
                "descricao": None,
                "flag": None,
                "valor_inicial": None,
                "creditos": None,
                "dotacao": None,
                "total_empenhado": None,
                "total_liquidado": None,
                "total_pago": None,
                "data_inicio": None,
                "data_fim": None,
                "url_origem": CAMARA_DESPESAS_URL,
            }),
            "endpoint": despesa_total_payload.get("url") if despesa_total_payload else None,
            "status_code": despesa_total_payload.get("status_code") if despesa_total_payload else None,
            "params": despesa_total_params,
        },
        "receita": {
            "descricao": receita_uso.get("descricao") if receita_uso else None,
            "situacao": receita_uso.get("situacao") if receita_uso else None,
            "total_orcado": _to_float(receita_uso.get("orcado")) if receita_uso else None,
            "total_arrecadado": _to_float(receita_uso.get("arrecadado")) if receita_uso else None,
            "percentual": _to_float(receita_uso.get("percentual")) if receita_uso else None,
            "url_origem": CAMARA_RECEITAS_URL,
            "endpoint": receita_payload.get("url") if receita_payload else None,
            "status_code": receita_payload.get("status_code") if receita_payload else None,
            "params": receita_params,
        },
        "acoes": acoes,
        "rastreabilidade": {
            "despesa_total": {
                "endpoint": despesa_total_payload.get("url") if despesa_total_payload else None,
                "status_code": despesa_total_payload.get("status_code") if despesa_total_payload else None,
                "params": despesa_total_params,
                "registros": len(despesa_total_rows),
            },
            "despesa_acoes": {
                "endpoint": despesa_acoes_payload.get("url") if despesa_acoes_payload else None,
                "status_code": despesa_acoes_payload.get("status_code") if despesa_acoes_payload else None,
                "params": despesa_acoes_params,
                "registros": len(despesa_acoes_rows),
            },
            "receita": {
                "endpoint": receita_payload.get("url") if receita_payload else None,
                "status_code": receita_payload.get("status_code") if receita_payload else None,
                "params": receita_params,
                "registros": len(receita_rows),
            },
        },
        "erros": erros,
        "observacao": (
            "Resumo calculado a partir dos endpoints publicos de receitas e despesas por classificacao "
            "orcamentaria da Camara Municipal. As acoes usam tipo=5 para evitar dupla contagem hierarquica."
        ),
    }


def temas_frequentes(db, limit=20):
    rows = db.query(
        models.EntidadeExtraida.valor,
        func.count(models.EntidadeExtraida.id).label("total"),
    ).filter(
        models.EntidadeExtraida.tipo_entidade == "tema"
    ).group_by(models.EntidadeExtraida.valor).order_by(func.count(models.EntidadeExtraida.id).desc()).limit(limit).all()
    return [{"tema": value, "total": total} for value, total in rows]


def bairros_frequentes(db, limit=20):
    rows = db.query(
        models.EntidadeExtraida.valor,
        func.count(models.EntidadeExtraida.id).label("total"),
    ).filter(
        models.EntidadeExtraida.tipo_entidade == "bairro"
    ).group_by(models.EntidadeExtraida.valor).order_by(func.count(models.EntidadeExtraida.id).desc()).limit(limit).all()
    return [{"bairro": value, "total": total} for value, total in rows]


def secretarias_frequentes(db, tipo_documento=None, limit=20):
    query = db.query(
        models.EntidadeExtraida.valor,
        func.count(models.EntidadeExtraida.id).label("total"),
    ).join(
        models.DocumentoProcessado,
        models.EntidadeExtraida.documento_processado_id == models.DocumentoProcessado.id,
    ).join(
        models.DocumentoBruto,
        models.DocumentoProcessado.documento_bruto_id == models.DocumentoBruto.id,
    ).filter(models.EntidadeExtraida.tipo_entidade == "secretaria")

    if tipo_documento:
        query = query.filter(models.DocumentoBruto.tipo_documento == tipo_documento)

    rows = query.group_by(models.EntidadeExtraida.valor).order_by(
        func.count(models.EntidadeExtraida.id).desc()
    ).limit(limit).all()
    return [{"secretaria": value, "total": total} for value, total in rows]


def _format_brl(value):
    if value is None:
        return None
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _chunk_payload(scored_chunk):
    chunk = scored_chunk["chunk"]
    payload = scored_chunk.get("payload") or {}
    if not chunk:
        return {
            "score": scored_chunk["score"],
            "chunk_id": payload.get("chunk_id"),
            "chunk_index": payload.get("chunk_index"),
            "trecho": (payload.get("texto") or "")[:900],
            "documento": {
                "id": payload.get("documento_bruto_id"),
                "fonte": payload.get("fonte"),
                "tipo_documento": payload.get("tipo_documento"),
                "titulo": payload.get("titulo"),
                "data_publicacao": payload.get("data_publicacao"),
                "url_origem": payload.get("url_origem"),
                "status_processamento": None,
            },
            "embedding_model": payload.get("embedding_model"),
            "vector_store": scored_chunk.get("vector_store"),
            "baseado_em_vetor_local": True,
        }

    doc = chunk.documento_bruto
    return {
        "score": scored_chunk["score"],
        "chunk_id": chunk.id,
        "chunk_index": chunk.chunk_index,
        "trecho": chunk.texto_limpo[:900],
        "documento": _document_payload(doc) if doc else None,
        "embedding_model": chunk.embedding_model,
        "vector_store": scored_chunk.get("vector_store"),
        "baseado_em_vetor_local": True,
    }


def retrieve_chunks(db, q, limit=8):
    from app.analytics.vector_rag import search_chunks

    return [_chunk_payload(item) for item in search_chunks(db, q, limit=limit)]


def retrieve_documents(db, q, limit=8):
    normalized = normalize_text(q)
    ignored = {"que", "qual", "quanto", "com", "para", "por", "uma", "uns", "das", "dos", "jundiai", "2026"}
    terms = [term for term in normalized.split() if len(term) > 2 and term not in ignored]
    docs = db.query(models.DocumentoBruto, models.DocumentoProcessado).join(
        models.DocumentoProcessado,
        models.DocumentoBruto.id == models.DocumentoProcessado.documento_bruto_id,
    ).limit(500).all()

    scored = []
    for doc, processed in docs:
        text = normalize_text(" ".join(filter(None, [
            doc.titulo,
            doc.fonte,
            doc.tipo_documento,
            processed.texto_limpo,
        ])))
        score = sum(text.count(term) for term in terms)
        if score:
            snippet = (processed.texto_limpo or "")[:700]
            scored.append((score, doc, snippet))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "score": score,
            "documento": _document_payload(doc),
            "trecho": snippet,
        }
        for score, doc, snippet in scored[:limit]
    ]


def rag_answer(db, q):
    evidencias = retrieve_chunks(db, q)
    fallback_textual = False
    if not evidencias:
        evidencias = retrieve_documents(db, q)
        fallback_textual = True

    structured = None
    normalized = normalize_text(q)
    if "quanto" in normalized or "gastou" in normalized or "gasto" in normalized or "gastos" in normalized:
        structured = gastos_por_termo(db, q, _extract_year(normalized))

    totals = ""
    if structured:
        total_pago = _format_brl(structured.get("total_pago"))
        total_liquidado = _format_brl(structured.get("total_liquidado"))
        total_empenhado = _format_brl(structured.get("total_empenhado"))
        total_parts = []
        if total_pago:
            total_parts.append(f"pago identificado: {total_pago}")
        if total_liquidado:
            total_parts.append(f"liquidado: {total_liquidado}")
        if total_empenhado:
            total_parts.append(f"empenhado: {total_empenhado}")
        if total_parts:
            totals = " Totais estruturados encontrados: " + "; ".join(total_parts) + "."

    return {
        "tipo": "rag_vetorial_local" if not fallback_textual else "rag_textual_fallback",
        "consulta": q,
        "resposta": (
            "Encontrei trechos oficiais relacionados por busca vetorial local."
            + totals
            + " Confira as fontes antes de concluir valores ou responsabilidades."
            if evidencias else
            "Nao encontrei documentos oficiais coletados que sustentem uma resposta para essa pergunta."
        ),
        "evidencias": evidencias,
        "analise_estruturada": structured,
        "baseado_em_aproximacao_textual": True,
        "observacao": (
            "Esta RAG usa chunks com overlap e embeddings locais por hash. Ela ja prepara a arquitetura "
            "para pgvector/OpenAI/Gemini, mas ainda nao usa um modelo semantico externo."
        ),
    }


def interpret_question(db, q):
    normalized = normalize_text(q)

    if "fez" in normalized or "vereador" in normalized or "vereadora" in normalized:
        for vereador in db.query(models.Vereador).all():
            if vereador.nome_normalizado in normalized or any(part in normalized for part in _meaningful_parts(vereador.nome_normalizado)):
                return {
                    "tipo": "analytics_vereador",
                    "consulta": q,
                    "resposta": vereador_analytics(db, vereador.nome),
                    "baseado_em_aproximacao_textual": True,
                }

    if any(term in normalized for term in ["arrecadacao", "arrecadou", "receita", "receitas"]):
        ano = _extract_year(normalized)
        clean_term = _extract_subject_term(
            q,
            {
                "quanto", "qual", "total", "foi", "foram", "arrecadado", "arrecadada",
                "arrecadou", "arrecadacao", "receita", "receitas", "com", "em", "de",
                "da", "do", "das", "dos", "jundiai",
            },
        )
        return {
            "tipo": "analytics_receitas",
            "consulta": q,
            "resposta": receitas_analytics(db, ano=ano, termo=clean_term or None),
            "baseado_em_aproximacao_textual": False,
        }

    if "quanto" in normalized or "gastou" in normalized or "gasto" in normalized:
        if any(term in normalized for term in ["cada secretaria", "por secretaria", "secretarias"]):
            return {
                "tipo": "analytics_gastos_secretarias",
                "consulta": q,
                "resposta": gastos_por_secretarias(db, _extract_year(normalized)),
                "baseado_em_aproximacao_textual": False,
            }
        for secretaria in db.query(models.Secretaria).all():
            if secretaria.nome_normalizado in normalized or any(part in normalized for part in _meaningful_parts(secretaria.nome_normalizado)):
                ano = _extract_year(normalized)
                return {
                    "tipo": "analytics_gastos_secretaria",
                    "consulta": q,
                    "resposta": gastos_secretaria(db, secretaria.nome, ano),
                    "baseado_em_aproximacao_textual": True,
                }
        return {
            "tipo": "analytics_gastos_termo",
            "consulta": q,
            "resposta": gastos_por_termo(db, q, _extract_year(normalized)),
            "baseado_em_aproximacao_textual": True,
        }

    if "tema" in normalized or "assunto" in normalized:
        return {
            "tipo": "analytics_temas",
            "consulta": q,
            "resposta": temas_frequentes(db),
            "baseado_em_aproximacao_textual": False,
        }

    if "bairro" in normalized:
        return {
            "tipo": "analytics_bairros",
            "consulta": q,
            "resposta": bairros_frequentes(db),
            "baseado_em_aproximacao_textual": False,
        }

    if "secretaria" in normalized or "secretarias" in normalized:
        if "licitacao" in normalized or "licitacoes" in normalized:
            return {
                "tipo": "analytics_secretarias_licitacoes",
                "consulta": q,
                "resposta": secretarias_frequentes(db, tipo_documento="licitacao"),
                "baseado_em_aproximacao_textual": True,
                "observacao": "Contagem por mencoes textuais de secretarias/siglas em documentos de licitacao.",
            }
        return {
            "tipo": "analytics_secretarias",
            "consulta": q,
            "resposta": secretarias_frequentes(db),
            "baseado_em_aproximacao_textual": True,
        }

    if any(term in normalized for term in ["contrato", "contratos", "licitacao", "licitacoes"]):
        for secretaria in db.query(models.Secretaria).all():
            parts = [part for part in secretaria.nome_normalizado.split() if len(part) > 3]
            if secretaria.nome_normalizado in normalized or any(part in normalized for part in parts):
                return {
                    "tipo": "analytics_documentos_secretaria",
                    "consulta": q,
                    "resposta": gastos_secretaria(db, secretaria.nome, _extract_year(normalized)),
                    "baseado_em_aproximacao_textual": True,
                    "observacao": "Consulta interpretada como documentos de transparencia relacionados a uma secretaria ou tema.",
                }

    return rag_answer(db, q)


def _extract_year(text):
    import re

    match = re.search(r"\b(20\d{2})\b", text)
    return int(match.group(1)) if match else None


def _extract_subject_term(text, ignored):
    import re

    normalized = normalize_text(re.sub(r"\b20\d{2}\b", "", text))
    parts = [part for part in normalized.split() if len(part) > 2 and part not in ignored]
    return " ".join(parts).strip()
