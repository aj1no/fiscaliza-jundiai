from collections import Counter
from datetime import datetime, timedelta
import json
import logging
import os

import requests
from sqlalchemy import func, or_

from app.analytics.entity_extractor import normalize_text
from app.analytics import llm_service
from app.models import models

logger = logging.getLogger(__name__)

DATA_STALE_HOURS = max(1, int(os.getenv("FINANCE_DATA_STALE_HOURS", "24")))

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


COMMON_QUERY_ALIASES = {
    "emeb": ["emeb", "emebs"],
    "emebs": ["emeb", "emebs"],
    "asfalto": ["asfalto", "pavimentacao", "pavimento", "recapeamento", "recape"],
    "asfaltamento": ["asfalto", "pavimentacao", "pavimento", "recapeamento", "recape"],
    "rua": ["rua", "via", "vias", "pavimentacao", "recapeamento"],
    "ruas": ["rua", "via", "vias", "pavimentacao", "recapeamento"],
    "creche": ["creche", "educacao infantil"],
    "creches": ["creche", "educacao infantil"],
}


def _query_term_groups(text, ignored):
    terms = [
        part for part in normalize_text(text).split()
        if len(part) > 2 and part not in ignored and not part.isdigit()
    ]
    groups = []
    for term in terms:
        aliases = COMMON_QUERY_ALIASES.get(term, [term])
        if term.endswith("s") and len(term) > 4:
            aliases = [*aliases, term[:-1]]
        normalized_aliases = [normalize_text(alias) for alias in aliases if normalize_text(alias)]
        groups.append(sorted(set(normalized_aliases)))
    return groups


def _matches_term_group(haystack, group):
    return any(alias in haystack for alias in group)


def _flatten_term_groups(groups):
    return sorted({alias for group in groups for alias in group})


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_numeric(values):
    total = 0.0
    found = False
    for value in values:
        parsed = _to_float(value)
        if parsed is None:
            continue
        total += parsed
        found = True
    return total if found else None


def _row_timestamp(row):
    doc = getattr(row, "fonte_documento", None)
    return (
        getattr(doc, "atualizado_em", None)
        or getattr(doc, "data_coleta", None)
        or getattr(doc, "criado_em", None)
        or getattr(row, "criado_em", None)
        or datetime.min
    )


def _dedupe_latest_rows(rows, key_fn):
    latest_by_key = {}
    for row in rows:
        key = key_fn(row)
        if key is None:
            key = ("row", getattr(row, "id", id(row)))
        stamp = _row_timestamp(row)
        current = latest_by_key.get(key)
        if current is None or stamp >= current[0]:
            latest_by_key[key] = (stamp, row)
    return [item[1] for item in latest_by_key.values()]


def _is_top_level_revenue_classification(value):
    if value is None:
        return False
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 6:
        return False
    trailing_zeros = len(digits) - len(digits.rstrip("0"))
    return trailing_zeros >= 6


def _revenue_digits(value):
    if value is None:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def _revenue_pattern_recognized(rows):
    digits = [_revenue_digits(row.classificacao) for row in rows if _revenue_digits(row.classificacao)]
    if not digits:
        return {
            "recognized": False,
            "reason": "nenhuma classificacao numerica valida encontrada",
            "dominant_length": None,
            "coverage": 0.0,
        }

    length_counter = Counter(len(item) for item in digits)
    dominant_length, dominant_count = length_counter.most_common(1)[0]
    coverage = dominant_count / len(digits)
    recognized_length = dominant_length in {8, 15}
    recognized = coverage >= 0.9 and recognized_length
    reason = None
    if not recognized:
        reason = (
            f"padrao de classificacao nao reconhecido com seguranca "
            f"(comprimento_dominante={dominant_length}, cobertura={coverage:.2f})"
        )
    return {
        "recognized": recognized,
        "reason": reason,
        "dominant_length": dominant_length,
        "coverage": coverage,
    }


def _parse_snapshot_observations(raw_observacoes):
    if not raw_observacoes:
        return []
    try:
        payload = json.loads(raw_observacoes)
    except Exception:
        return [str(raw_observacoes)]
    if isinstance(payload, list):
        return [str(item) for item in payload if str(item).strip()]
    return [str(payload)]


def _safe_json_load(raw_value, default):
    if not raw_value:
        return default
    try:
        return json.loads(raw_value)
    except Exception:
        return default


def _latest_snapshot(db, category, ano=None):
    query = db.query(models.ColetaSnapshot).filter(
        models.ColetaSnapshot.fonte == "portal_transparencia",
        models.ColetaSnapshot.categoria == category,
    )
    if ano is not None:
        query = query.filter(models.ColetaSnapshot.ano == ano)
    return query.order_by(models.ColetaSnapshot.criado_em.desc()).first()


def _snapshot_metadata(
    db,
    *,
    category,
    ano=None,
    default_level="parcial",
    source_label="Portal da Transparencia de Jundiai",
    fallback_observations=None,
):
    snapshot = _latest_snapshot(db, category, ano=ano)
    now = datetime.utcnow()
    observations = list(fallback_observations or [])
    if snapshot:
        observations.extend(_parse_snapshot_observations(snapshot.observacoes))
        stale = snapshot.criado_em and (now - snapshot.criado_em) > timedelta(hours=DATA_STALE_HOURS)
        if stale:
            observations.append(
                f"ultima coleta com mais de {DATA_STALE_HOURS} horas; consulte nova coleta para dados atualizados"
            )
        return {
            "fonte": source_label,
            "ano": snapshot.ano if snapshot.ano is not None else ano,
            "data_ultima_coleta": snapshot.criado_em,
            "coleta_completa": bool(snapshot.coleta_completa),
            "registros_encontrados": snapshot.registros_encontrados or 0,
            "registros_coletados": snapshot.registros_coletados or 0,
            "registros_novos": getattr(snapshot, "registros_novos", 0) or 0,
            "registros_atualizados": getattr(snapshot, "registros_atualizados", 0) or 0,
            "limite_aplicado": snapshot.limite_aplicado,
            "endpoint": snapshot.endpoint,
            "parametros_consulta": _safe_json_load(snapshot.parametros, {}),
            "status_code": snapshot.status_code,
            "hash_conteudo": snapshot.hash_conteudo,
            "nivel_confiabilidade": snapshot.nivel_confiabilidade or default_level,
            "observacoes": observations,
        }
    return {
        "fonte": source_label,
        "ano": ano,
        "data_ultima_coleta": None,
        "coleta_completa": False,
        "registros_encontrados": 0,
        "registros_coletados": 0,
        "registros_novos": 0,
        "registros_atualizados": 0,
        "limite_aplicado": None,
        "endpoint": None,
        "parametros_consulta": {},
        "status_code": None,
        "hash_conteudo": None,
        "nivel_confiabilidade": default_level,
        "observacoes": ["nao ha snapshot recente para esta categoria"] + observations,
    }


def _normalize_finance_metadata(metadata, *, default_level="parcial"):
    base = dict(metadata or {})

    observations = base.get("observacoes")
    if isinstance(observations, list):
        observations = [str(item).strip() for item in observations if str(item).strip()]
    elif observations:
        observations = [str(observations).strip()]
    else:
        observations = []

    level = str(base.get("nivel_confiabilidade") or default_level).strip().lower()
    if level not in {"consolidado", "parcial", "inseguro_para_soma"}:
        level = default_level

    coleta_completa = bool(base.get("coleta_completa"))
    registros_encontrados = int(base.get("registros_encontrados") or 0)
    registros_coletados = int(base.get("registros_coletados") or 0)
    registros_novos = int(base.get("registros_novos") or 0)
    registros_atualizados = int(base.get("registros_atualizados") or 0)
    limite_aplicado = base.get("limite_aplicado")
    status_code = base.get("status_code")

    if isinstance(status_code, str) and status_code.isdigit():
        status_code = int(status_code)
    if status_code is not None:
        try:
            status_code = int(status_code)
        except Exception:
            status_code = None

    if limite_aplicado is not None:
        try:
            limite_aplicado = int(limite_aplicado)
        except Exception:
            limite_aplicado = None

    if level == "consolidado" and not coleta_completa:
        level = "parcial"
        observations.append("coleta sem confirmacao de completude; confiabilidade reduzida para parcial")

    if limite_aplicado is not None and registros_encontrados >= limite_aplicado:
        if level == "consolidado":
            level = "parcial"
        observations.append("limite de coleta aplicado; resultado pode estar parcial")

    if status_code is not None and status_code >= 400:
        level = "inseguro_para_soma"
        observations.append("falha em endpoint de origem durante coleta/consulta")

    if base.get("data_ultima_coleta") is None and level == "consolidado":
        level = "parcial"
        observations.append("sem data de ultima coleta para confirmar consolidacao")

    return {
        "fonte": base.get("fonte"),
        "ano": base.get("ano"),
        "data_ultima_coleta": base.get("data_ultima_coleta"),
        "coleta_completa": coleta_completa,
        "registros_encontrados": registros_encontrados,
        "registros_coletados": registros_coletados,
        "registros_novos": registros_novos,
        "registros_atualizados": registros_atualizados,
        "limite_aplicado": limite_aplicado,
        "endpoint": base.get("endpoint"),
        "parametros_consulta": base.get("parametros_consulta") or {},
        "status_code": status_code,
        "hash_conteudo": base.get("hash_conteudo"),
        "nivel_confiabilidade": level,
        "observacoes": observations,
    }


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
    resumo_secretaria_raw = [
        item for item in despesas
        if item.fonte_documento and item.fonte_documento.tipo_documento == "despesa_secretaria"
    ]
    resumo_secretaria = _dedupe_latest_rows(
        resumo_secretaria_raw,
        lambda item: (item.ano, normalize_text(item.secretaria or "")),
    )
    resumo_dedupe_removed = max(0, len(resumo_secretaria_raw) - len(resumo_secretaria))
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
        metadata = _snapshot_metadata(
            db,
            category="despesa_secretaria",
            ano=ano,
            default_level="inseguro_para_soma",
            fallback_observations=[
                "nao ha valores estruturados suficientes para consolidacao segura nesta consulta"
            ],
        )
        metadata = _normalize_finance_metadata(metadata, default_level="inseguro_para_soma")
        return {
            "secretaria": nome,
            "ano": ano,
            "total_empenhado": None,
            "total_liquidado": None,
            "total_pago": None,
            "valor_empenhado_coletado": None,
            "valor_liquidado_coletado": None,
            "valor_pago_coletado": None,
            "totais_consolidados": False,
            "documentos": [_document_payload(doc) for doc in docs],
            "baseado_em_aproximacao_textual": True,
            "metadados": metadata,
            "observacao": "Nao ha valores monetarios estruturados para esta consulta; foram retornados documentos oficiais relacionados quando encontrados.",
        }

    def total(field):
        return _sum_numeric(getattr(d, field) for d in despesas_para_total)

    docs = [d.fonte_documento for d in despesas_para_total if d.fonte_documento]
    metadata = _snapshot_metadata(
        db,
        category="despesa_secretaria",
        ano=ano,
        default_level="parcial",
    )
    metadata = _normalize_finance_metadata(metadata, default_level="parcial")
    if resumo_dedupe_removed:
        metadata["observacoes"].append(
            f"{resumo_dedupe_removed} registros repetidos por secretaria foram deduplicados usando o registro mais recente"
        )
    if not resumo_secretaria:
        metadata["nivel_confiabilidade"] = "inseguro_para_soma"
        metadata["observacoes"].append(
            "resultado sem linha consolidada de despesa por secretaria; valores podem refletir amostra de contratos"
        )

    valor_empenhado = total("valor_empenhado")
    valor_liquidado = total("valor_liquidado")
    valor_pago = total("valor_pago")
    totais_consolidados = metadata["nivel_confiabilidade"] == "consolidado"
    if not totais_consolidados and (valor_empenhado is not None or valor_liquidado is not None or valor_pago is not None):
        metadata["observacoes"].append(
            "valores retornados como indicador coletado; nao interpretar como total consolidado"
        )

    return {
        "secretaria": nome,
        "ano": ano,
        "total_empenhado": valor_empenhado if totais_consolidados else None,
        "total_liquidado": valor_liquidado if totais_consolidados else None,
        "total_pago": valor_pago if totais_consolidados else None,
        "valor_empenhado_coletado": valor_empenhado,
        "valor_liquidado_coletado": valor_liquidado,
        "valor_pago_coletado": valor_pago,
        "totais_consolidados": totais_consolidados,
        "documentos": [_document_payload(doc) for doc in docs],
        "metadados": metadata,
        "registros": [
            {
                "id": d.id,
                "ano": d.ano,
                "secretaria": d.secretaria,
                "fornecedor": d.fornecedor,
                "cnpj": d.cnpj,
                "objeto": d.objeto,
                "valor_empenhado": _to_float(d.valor_empenhado),
                "valor_liquidado": _to_float(d.valor_liquidado),
                "valor_pago": _to_float(d.valor_pago),
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
                "valor_empenhado": _to_float(d.valor_empenhado),
                "valor_pago": _to_float(d.valor_pago),
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
        "pelas", "pelo", "pelos", "foi", "foram", "em", "de", "da", "do",
        "das", "dos", "na", "nas", "no", "nos", "ao", "aos", "jundiai",
        "municipio", "prefeitura",
    }
    term_groups = _query_term_groups(normalized, ignored)
    all_terms = _flatten_term_groups(term_groups)

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
        if term_groups and all(_matches_term_group(haystack, group) for group in term_groups):
            matches.append(item)

    if not matches and all_terms:
        matches = [
            item for item in candidates
            if item.fonte_documento and item.fonte_documento.tipo_documento != "despesa_secretaria"
            and any(
                term in normalize_text(" ".join(filter(None, [item.objeto, item.fornecedor, item.secretaria])))
                for term in all_terms
            )
        ][:30]

    def total(field):
        return _sum_numeric(getattr(item, field) for item in matches)

    docs = [item.fonte_documento for item in matches if item.fonte_documento]
    metadata = _snapshot_metadata(
        db,
        category="contrato",
        ano=ano,
        default_level="parcial",
        fallback_observations=[
            "consulta baseada em correspondencia textual entre termo e contratos/despesas coletados"
        ],
    )
    metadata = _normalize_finance_metadata(metadata, default_level="parcial")
    if not matches:
        metadata["nivel_confiabilidade"] = "inseguro_para_soma"
    valor_empenhado = total("valor_empenhado")
    valor_liquidado = total("valor_liquidado")
    valor_pago = total("valor_pago")
    metadata["observacoes"].append(
        "resultado por termo representa amostra textual; valores monetarios exibidos como indicador coletado"
    )

    return {
        "termo": termo,
        "ano": ano,
        "termos_busca": all_terms,
        "total_empenhado": None,
        "total_liquidado": None,
        "total_pago": None,
        "valor_empenhado_coletado": valor_empenhado,
        "valor_liquidado_coletado": valor_liquidado,
        "valor_pago_coletado": valor_pago,
        "totais_consolidados": False,
        "documentos": [_document_payload(doc) for doc in docs],
        "metadados": metadata,
        "registros": [
            {
                "id": item.id,
                "ano": item.ano,
                "secretaria": item.secretaria,
                "fornecedor": item.fornecedor,
                "objeto": item.objeto,
                "valor_empenhado": _to_float(item.valor_empenhado),
                "valor_liquidado": _to_float(item.valor_liquidado),
                "valor_pago": _to_float(item.valor_pago),
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

    raw_rows = query.all()
    rows = _dedupe_latest_rows(
        raw_rows,
        lambda row: (row.ano, normalize_text(row.secretaria or "")),
    )
    dedupe_removed = max(0, len(raw_rows) - len(rows))
    result = [
        {
            "secretaria": row.secretaria,
            "ano": row.ano,
            "total_empenhado": _to_float(row.valor_empenhado),
            "total_liquidado": _to_float(row.valor_liquidado),
            "total_pago": _to_float(row.valor_pago),
            "url_origem": row.url_origem,
            "documento_id": row.fonte_documento_id,
        }
        for row in rows
    ]
    result.sort(key=lambda item: item.get("total_pago") or item.get("total_empenhado") or 0, reverse=True)
    metadata = _snapshot_metadata(
        db,
        category="despesa_secretaria",
        ano=ano,
        default_level="parcial",
    )
    metadata = _normalize_finance_metadata(metadata, default_level="parcial")
    if dedupe_removed:
        metadata["observacoes"].append(
            f"{dedupe_removed} registros repetidos por secretaria foram deduplicados usando o registro mais recente"
        )
    if metadata["limite_aplicado"] is not None and int(metadata["registros_encontrados"] or 0) >= int(metadata["limite_aplicado"]):
        metadata["nivel_confiabilidade"] = "parcial"
        metadata["observacoes"].append(
            "limite de coleta aplicado para despesas por secretaria; nao tratar como total consolidado anual"
        )
    return {
        "ano": ano,
        "secretarias": result,
        "metadados": metadata,
        "observacao": "Totais por secretaria vindos do endpoint oficial de despesa por classificacao orcamentaria.",
    }


def receitas_analytics(db, ano=None, termo=None, limit=100):
    query = db.query(models.Receita)
    if ano:
        query = query.filter(models.Receita.ano == ano)

    raw_rows = query.limit(5000).all()
    rows = _dedupe_latest_rows(
        raw_rows,
        lambda row: (
            row.ano,
            _revenue_digits(row.classificacao) or normalize_text(row.classificacao or ""),
            normalize_text(row.descricao or ""),
        ),
    )
    dedupe_removed = max(0, len(raw_rows) - len(rows))
    if termo:
        normalized = normalize_text(termo)
        rows = [
            row for row in rows
            if normalized in normalize_text(row.descricao)
            or normalized in normalize_text(row.classificacao)
        ]
    visible_rows = rows[:limit]

    total_row = next(
        (
            row for row in rows
            if "total geral" in normalize_text(row.descricao)
        ),
        None,
    )
    top_level_rows = [row for row in rows if _is_top_level_revenue_classification(row.classificacao)]
    pattern = _revenue_pattern_recognized(rows)

    indicador_arrecadado = None
    indicador_orcado = None
    total_arrecadado = None
    total_orcado = None
    confidence = "inseguro_para_soma"
    observations = []
    metodo_agregacao = "bloqueado"
    if dedupe_removed:
        observations.append(
            f"{dedupe_removed} registros repetidos de receita foram deduplicados usando o registro mais recente"
        )

    if termo:
        indicador_arrecadado = _sum_numeric(row.valor_arrecadado for row in rows)
        indicador_orcado = _sum_numeric(row.valor_orcado for row in rows)
        confidence = "parcial"
        metodo_agregacao = "filtro_textual"
        observations.append("consulta filtrada por termo; resultado representa somente registros relacionados")
    elif not pattern["recognized"]:
        confidence = "inseguro_para_soma"
        metodo_agregacao = "padrao_nao_reconhecido"
        observations.append(
            pattern["reason"] or "classificacao orcamentaria sem padrao reconhecido; soma bloqueada por seguranca"
        )
    elif total_row:
        indicador_arrecadado = _to_float(total_row.valor_arrecadado)
        indicador_orcado = _to_float(total_row.valor_orcado)
        total_arrecadado = indicador_arrecadado
        total_orcado = indicador_orcado
        confidence = "consolidado"
        metodo_agregacao = "linha_total_geral"
        observations.append("total baseado em linha identificada como Total Geral no endpoint oficial")
    else:
        confidence = "inseguro_para_soma"
        metodo_agregacao = "hierarquia_indefinida"
        observations.append(
            "estrutura hierarquica impede consolidacao segura sem linha de Total Geral oficial; soma bloqueada"
        )

    metadata = _snapshot_metadata(
        db,
        category="receita_classificacao",
        ano=ano,
        default_level=confidence,
        fallback_observations=observations,
    )
    metadata = _normalize_finance_metadata(metadata, default_level=confidence)
    limit_hit = (
        metadata["limite_aplicado"] is not None
        and int(metadata["registros_encontrados"] or 0) >= int(metadata["limite_aplicado"])
    )
    if limit_hit:
        if metadata["nivel_confiabilidade"] == "consolidado":
            metadata["nivel_confiabilidade"] = "parcial"
        metadata["observacoes"].append("limite de coleta ativo para receitas; nao considerar como consolidado anual")
    elif metadata["nivel_confiabilidade"] != "parcial":
        metadata["nivel_confiabilidade"] = confidence

    can_call_total = (
        not termo
        and pattern["recognized"]
        and metadata["coleta_completa"] is True
        and not limit_hit
        and metodo_agregacao == "linha_total_geral"
        and metadata["nivel_confiabilidade"] == "consolidado"
    )
    if not can_call_total and (indicador_arrecadado is not None or indicador_orcado is not None):
        metadata["observacoes"].append(
            "valores monetarios retornados como indicador coletado; nao interpretar como total consolidado"
        )
        total_arrecadado = None
        total_orcado = None

    return {
        "ano": ano,
        "termo": termo,
        "total_orcado": total_orcado if rows else None,
        "total_arrecadado": total_arrecadado if rows else None,
        "valor_orcado_coletado": indicador_orcado if rows else None,
        "valor_arrecadado_coletado": indicador_arrecadado if rows else None,
        "agregacao_receita": {
            "metodo": metodo_agregacao,
            "padrao_classificacao_reconhecido": pattern["recognized"],
            "comprimento_classificacao_dominante": pattern["dominant_length"],
            "cobertura_padrao": pattern["coverage"],
            "soma_segura": can_call_total,
        },
        "metadados": metadata,
        "registros": [
            {
                "id": row.id,
                "ano": row.ano,
                "classificacao": row.classificacao,
                "descricao": row.descricao,
                "valor_orcado": _to_float(row.valor_orcado),
                "valor_arrecadado": _to_float(row.valor_arrecadado),
                "percentual": _to_float(row.percentual),
                "url_origem": row.url_origem,
            }
            for row in visible_rows
        ],
        "observacao": "Receitas baseadas em endpoint publico de classificacao orcamentaria, com metadado de confiabilidade.",
    }


def servidores_remuneracao(db, ano=None, mes=None, secretaria=None, limit=50):
    limit = min(max(int(limit or 50), 1), 200)
    query = db.query(models.ServidorRemuneracao)
    if ano:
        query = query.filter(models.ServidorRemuneracao.ano == ano)
    if mes:
        query = query.filter(models.ServidorRemuneracao.mes == mes)
    if secretaria:
        normalized_secretaria = normalize_text(secretaria)
        candidates = query.all()
        rows = [
            row for row in candidates
            if normalized_secretaria in normalize_text(row.secretaria)
            or any(part in normalize_text(row.secretaria) for part in _meaningful_parts(normalized_secretaria))
        ]
    else:
        rows = query.all()

    def total(field):
        return _sum_numeric(getattr(row, field) for row in rows)

    by_secretaria = {}
    for row in rows:
        key = row.secretaria or "Secretaria nao informada"
        bucket = by_secretaria.setdefault(key, {
            "secretaria": key,
            "servidores": 0,
            "total_remuneracao_mes": 0.0,
            "total_remuneracao_bruta": 0.0,
            "total_salario_base": 0.0,
        })
        bucket["servidores"] += 1
        bucket["total_remuneracao_mes"] += _to_float(row.valor_total_mes) or 0.0
        bucket["total_remuneracao_bruta"] += _to_float(row.valor_total_venc) or 0.0
        bucket["total_salario_base"] += _to_float(row.valor_salario_base) or 0.0

    secretarias = sorted(
        by_secretaria.values(),
        key=lambda item: item["total_remuneracao_mes"],
        reverse=True,
    )
    registros = sorted(rows, key=lambda row: _to_float(row.valor_total_mes) or 0, reverse=True)[:limit]
    documentos = []
    seen_docs = set()
    for row in rows:
        if row.fonte_documento and row.fonte_documento.id not in seen_docs:
            documentos.append(_document_payload(row.fonte_documento))
            seen_docs.add(row.fonte_documento.id)

    metadata = _snapshot_metadata(
        db,
        category="remuneracao_servidores",
        ano=ano,
        default_level="parcial",
    )
    metadata = _normalize_finance_metadata(metadata, default_level="parcial")

    return {
        "ano": ano,
        "mes": mes,
        "secretaria": secretaria,
        "servidores": len(rows),
        "total_remuneracao_bruta": total("valor_total_venc"),
        "total_remuneracao_mes": total("valor_total_mes"),
        "total_salario_base": total("valor_salario_base"),
        "secretarias": secretarias,
        "metadados": metadata,
        "documentos": documentos,
        "registros": [
            {
                "id": row.id,
                "ano": row.ano,
                "mes": row.mes,
                "codigo_funcionario": row.codigo_funcionario,
                "nome_funcionario": row.nome_funcionario,
                "secretaria": row.secretaria,
                "cargo": row.cargo,
                "provimento": row.provimento,
                "carga_horaria": row.carga_horaria,
                "valor_total_venc": _to_float(row.valor_total_venc),
                "valor_total_mes": _to_float(row.valor_total_mes),
                "valor_salario_base": _to_float(row.valor_salario_base),
                "data_atualizacao": row.data_atualizacao,
                "url_origem": row.url_origem,
            }
            for row in registros
        ],
        "observacao": (
            "Valores estruturados a partir do CSV publico de remuneracao mensal. "
            "Codigos mascarados pelo portal permanecem mascarados; totais refletem os CSVs coletados no banco."
        ),
    }


def auditoria_remuneracao_mensal(db, ano, ate_mes=12):
    ate_mes = max(1, min(int(ate_mes or 12), 12))

    rows = db.query(
        models.ServidorRemuneracao.mes.label("mes"),
        func.count(models.ServidorRemuneracao.id).label("registros"),
        func.count(func.distinct(models.ServidorRemuneracao.fonte_documento_id)).label("documentos_fonte"),
        func.sum(models.ServidorRemuneracao.valor_total_venc).label("total_bruto"),
        func.sum(models.ServidorRemuneracao.valor_total_mes).label("total_liquido"),
        func.sum(models.ServidorRemuneracao.valor_salario_base).label("total_base"),
    ).filter(
        models.ServidorRemuneracao.ano == ano,
        models.ServidorRemuneracao.mes.isnot(None),
        models.ServidorRemuneracao.mes >= 1,
        models.ServidorRemuneracao.mes <= ate_mes,
    ).group_by(
        models.ServidorRemuneracao.mes
    ).order_by(
        models.ServidorRemuneracao.mes.asc()
    ).all()

    by_month = {int(row.mes): row for row in rows}
    timeline = []
    inconsistencias = []
    meses_faltantes = []
    acumulado_bruto = 0.0
    acumulado_liquido = 0.0
    acumulado_base = 0.0

    for month in range(1, ate_mes + 1):
        row = by_month.get(month)
        if not row:
            meses_faltantes.append(month)
            timeline.append({
                "mes": month,
                "status": "nao_coletado",
                "registros": 0,
                "documentos_fonte": 0,
                "total_bruto": None,
                "total_liquido": None,
                "total_base": None,
                "acumulado_bruto": acumulado_bruto if acumulado_bruto > 0 else None,
                "acumulado_liquido": acumulado_liquido if acumulado_liquido > 0 else None,
                "acumulado_base": acumulado_base if acumulado_base > 0 else None,
                "variacao_liquido_mes_anterior": None,
            })
            continue

        total_bruto = float(row.total_bruto or 0)
        total_liquido = float(row.total_liquido or 0)
        total_base = float(row.total_base or 0)
        prev = timeline[-1] if timeline else None
        variacao = None
        if prev and prev.get("total_liquido") not in (None, 0):
            variacao = ((total_liquido - float(prev["total_liquido"])) / float(prev["total_liquido"])) * 100.0

        acumulado_bruto += total_bruto
        acumulado_liquido += total_liquido
        acumulado_base += total_base

        month_inconsistencias = []
        if int(row.documentos_fonte or 0) != 1:
            month_inconsistencias.append("quantidade_documentos_fonte_inesperada")
        if int(row.registros or 0) <= 0:
            month_inconsistencias.append("sem_registros")
        if total_liquido <= 0:
            month_inconsistencias.append("total_liquido_nao_positivo")
        if total_bruto < total_liquido:
            month_inconsistencias.append("total_bruto_menor_que_liquido")

        if month_inconsistencias:
            inconsistencias.append({
                "mes": month,
                "regras": month_inconsistencias,
            })

        timeline.append({
            "mes": month,
            "status": "coletado",
            "registros": int(row.registros or 0),
            "documentos_fonte": int(row.documentos_fonte or 0),
            "total_bruto": total_bruto,
            "total_liquido": total_liquido,
            "total_base": total_base,
            "acumulado_bruto": acumulado_bruto,
            "acumulado_liquido": acumulado_liquido,
            "acumulado_base": acumulado_base,
            "variacao_liquido_mes_anterior": variacao,
        })

    metadata = _snapshot_metadata(
        db,
        category="remuneracao_servidores",
        ano=ano,
        default_level="parcial",
        fallback_observations=[
            "auditoria mensal derivada dos dados estruturados de remuneracao coletados no banco"
        ],
    )
    metadata = _normalize_finance_metadata(metadata, default_level="parcial")
    if meses_faltantes:
        metadata["nivel_confiabilidade"] = "parcial"
        metadata["observacoes"].append(
            f"meses_sem_coleta_no_intervalo={','.join(str(m) for m in meses_faltantes)}"
        )

    return {
        "ano": ano,
        "ate_mes": ate_mes,
        "meses_coletados": len(rows),
        "meses_faltantes": meses_faltantes,
        "inconsistencias": inconsistencias,
        "linha_mensal": timeline,
        "total_bruto_periodo": acumulado_bruto if acumulado_bruto > 0 else None,
        "total_liquido_periodo": acumulado_liquido if acumulado_liquido > 0 else None,
        "total_base_periodo": acumulado_base if acumulado_base > 0 else None,
        "aprovado_sem_alertas": len(inconsistencias) == 0,
        "metadados": metadata,
        "observacao": (
            "Auditoria baseada em dados reais coletados no banco. "
            "Meses sem coleta entram como nao_coletado e devem ser preenchidos para auditoria anual completa."
        ),
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

    # Os endpoints atuais da Camara nao expõem total oficial de paginas/itens para
    # confirmacao formal de completude; por seguranca, tratamos como parcial.
    camara_complete = False
    camara_level = "parcial" if (despesa_total or receita_uso) else "inseguro_para_soma"
    camara_observacoes = []
    if erros:
        camara_observacoes.append(f"falhas_em_endpoints={len(erros)}")
    if not receita_total_row and receita_rows:
        camara_observacoes.append("receita sem linha Total Geral; valor agregado a partir dos registros retornados")
    camara_observacoes.append(
        "completude nao confirmada formalmente pelos endpoints da Camara; valores exibidos como observados"
    )

    camara_metadata = _normalize_finance_metadata(
        {
            "fonte": "Portal da Transparencia da Camara Municipal de Jundiai",
            "ano": ano,
            "data_ultima_coleta": datetime.utcnow(),
            "coleta_completa": camara_complete,
            "registros_encontrados": len(despesa_total_rows) + len(despesa_acoes_rows) + len(receita_rows),
            "registros_coletados": len(despesa_total_rows) + len(despesa_acoes_rows) + len(receita_rows),
            "limite_aplicado": None,
            "endpoint": CAMARA_TRANSPARENCIA_BASE_URL,
            "parametros_consulta": {
                "despesa_total": despesa_total_params,
                "despesa_acoes": despesa_acoes_params,
                "receita": receita_params,
            },
            "status_code": None if erros else 200,
            "hash_conteudo": None,
            "nivel_confiabilidade": camara_level,
            "observacoes": camara_observacoes,
        },
        default_level="parcial",
    )

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
        "metadados": camara_metadata,
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
    parsed = _to_float(value)
    if parsed is None:
        return None
    return f"R$ {parsed:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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
        total_parts = []
        if structured.get("total_pago") and structured["total_pago"] > 0:
            total_parts.append(f"pago identificado: {_format_brl(structured['total_pago'])}")
        if structured.get("total_liquidado") and structured["total_liquidado"] > 0:
            total_parts.append(f"liquidado: {_format_brl(structured['total_liquidado'])}")
        if structured.get("total_empenhado") and structured["total_empenhado"] > 0:
            total_parts.append(f"empenhado: {_format_brl(structured['total_empenhado'])}")
        if total_parts:
            totals = " Totais estruturados encontrados: " + "; ".join(total_parts) + "."

    # Gerar a resposta final usando a LLM (Gemini) se disponível
    if evidencias or structured:
        resposta_final = llm_service.generate_answer(q, evidencias, structured)
    else:
        resposta_final = "Nao encontrei documentos oficiais coletados que sustentem uma resposta para essa pergunta."

    return {
        "tipo": "rag_vetorial_local" if not fallback_textual else "rag_textual_fallback",
        "consulta": q,
        "resposta": resposta_final,
        "evidencias": evidencias,
        "analise_estruturada": structured,
        "baseado_em_aproximacao_textual": True,
        "observacao": (
            "Esta resposta foi gerada por uma IA (Gemini 1.5 Flash) com base nos documentos oficiais recuperados. "
            "Sempre confira os trechos originais abaixo para máxima precisão."
        ),
    }


def interpret_question(db, q):
    normalized = normalize_text(q)

    if "fez" in normalized or "vereador" in normalized or "vereadora" in normalized:
        for vereador in db.query(models.Vereador).all():
            if (
                vereador.nome_normalizado in normalized 
                or any(part in normalized for part in _meaningful_parts(vereador.nome_normalizado))
            ):
                analytics = vereador_analytics(db, vereador.nome)
                # Usar a LLM para resumir a atuação do vereador
                resumo_ia = llm_service.generate_answer(q, [], analytics)
                return {
                    "tipo": "analytics_vereador",
                    "consulta": q,
                    "resposta": {**analytics, "resumo_ia": resumo_ia},
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

    if any(term in normalized for term in ["servidor", "servidores", "remuneracao", "salario", "salarios", "folha", "funcionalismo"]):
        secretaria_nome = None
        for secretaria in db.query(models.Secretaria).all():
            if secretaria.nome_normalizado in normalized or any(part in normalized for part in _meaningful_parts(secretaria.nome_normalizado)):
                secretaria_nome = secretaria.nome
                break
        ano_consulta = _extract_year(normalized) or datetime.now().year
        return {
            "tipo": "analytics_servidores_remuneracao",
            "consulta": q,
            "resposta": servidores_remuneracao(
                db,
                ano=ano_consulta,
                mes=_extract_month(normalized),
                secretaria=secretaria_nome,
            ),
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
                resposta = gastos_secretaria(db, secretaria.nome, ano)
                return {
                    "tipo": "analytics_gastos_secretaria",
                    "consulta": q,
                    "resposta": resposta,
                    "baseado_em_aproximacao_textual": resposta.get("baseado_em_aproximacao_textual", True),
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


def _extract_month(text):
    months = {
        "janeiro": 1,
        "fevereiro": 2,
        "marco": 3,
        "abril": 4,
        "maio": 5,
        "junho": 6,
        "julho": 7,
        "agosto": 8,
        "setembro": 9,
        "outubro": 10,
        "novembro": 11,
        "dezembro": 12,
    }
    for name, number in months.items():
        if name in text:
            return number
    return None


def _extract_subject_term(text, ignored):
    import re

    normalized = normalize_text(re.sub(r"\b20\d{2}\b", "", text))
    parts = [part for part in normalized.split() if len(part) > 2 and part not in ignored]
    return " ".join(parts).strip()
