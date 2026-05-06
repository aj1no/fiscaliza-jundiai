import json
import csv
import re
import unicodedata
from datetime import datetime

from sqlalchemy import or_

from app.models import models


def normalize_text(value):
    text = str(value or "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


VEREADORES = [
    {"nome": "Juninho Adilson", "aliases": ["juninho adilson", "adilson roberto pereira junior"], "partido": None},
    {"nome": "Dika Xique Xique", "aliases": ["dika xique xique", "adriano santana dos santos"], "partido": None},
    {"nome": "Faouaz Taha", "aliases": ["faouaz taha"], "partido": None},
    {"nome": "Carla Basilio", "aliases": ["carla basilio", "carla basilico", "carla basilio cristiano lopes"], "partido": None},
    {"nome": "Cristiano Lopes", "aliases": ["cristiano lopes", "cristiano vecchi castro lopes"], "partido": None},
    {"nome": "Edicarlos Vieira", "aliases": ["edicarlos vieira"], "partido": None},
    {"nome": "Daniel Lemos", "aliases": ["daniel lemos", "daniel lemos dias pereira"], "partido": None},
    {"nome": "Henrique do Cardume", "aliases": ["henrique do cardume", "henrique carlos parra parra filho"], "partido": None},
    {"nome": "Joao Victor", "aliases": ["joao victor", "joao victor ramos"], "partido": None},
    {"nome": "Dr. Kachan Jr.", "aliases": ["dr kachan", "kachan junior", "jose antonio kachan junior"], "partido": None},
    {"nome": "Ze Dias", "aliases": ["ze dias", "jose carlos ferreira dias"], "partido": None},
    {"nome": "Leandro Basson", "aliases": ["leandro basson", "leandro jeronimo basson"], "partido": None},
    {"nome": "Madson Henrique", "aliases": ["madson henrique", "madson henrique do nascimento santos"], "partido": None},
    {"nome": "Mariana Janeiro", "aliases": ["mariana janeiro", "mariana cergoli janeiro"], "partido": "PT"},
    {"nome": "Paulo Sergio - Delegado", "aliases": ["paulo sergio", "delegado paulo sergio", "paulo sergio martins"], "partido": None},
    {"nome": "Quezia de Lucca", "aliases": ["quezia de lucca", "quezia doane de lucca"], "partido": None},
    {"nome": "Rodrigo Albino", "aliases": ["rodrigo albino", "rodrigo guarnieri albino"], "partido": None},
    {"nome": "Romildo Antonio", "aliases": ["romildo antonio", "romildo antonio da silva"], "partido": None},
    {"nome": "Tiago da El Elion", "aliases": ["tiago da el elion", "tiago leandro"], "partido": None},
]


SECRETARIAS = [
    {"nome": "Saude", "aliases": ["saude", "sms"]},
    {"nome": "Educacao", "aliases": ["educacao", "sme"]},
    {"nome": "Cultura", "aliases": ["cultura", "smcult"]},
    {"nome": "Mobilidade e Transporte", "aliases": ["mobilidade", "transporte", "transito", "smmt"]},
    {"nome": "Obras", "aliases": ["obras", "infraestrutura", "smisp"]},
    {"nome": "Seguranca", "aliases": ["seguranca", "guarda municipal"]},
    {"nome": "Assistencia Social", "aliases": ["assistencia social", "smads"]},
    {"nome": "Meio Ambiente", "aliases": ["meio ambiente", "smaat"]},
    {"nome": "Administracao e Gestao", "aliases": ["administracao", "gestao publica", "smagp"]},
    {"nome": "Casa Civil", "aliases": ["casa civil", "smcc"]},
    {"nome": "Desenvolvimento Economico", "aliases": ["desenvolvimento economico", "smdect"]},
    {"nome": "Planejamento Urbano", "aliases": ["planejamento urbano", "smpuma"]},
    {"nome": "Promocao da Saude", "aliases": ["promocao da saude", "smps"]},
    {"nome": "Servicos Publicos", "aliases": ["servicos publicos", "smsp"]},
]


BAIRROS = [
    "Agapeama", "Anhangabau", "Caxambu", "Centro", "Cecap", "Colonia",
    "Eloy Chaves", "Engordadouro", "Fazenda Grande", "Horto Florestal",
    "Ivoturucaia", "Jardim do Lago", "Jardim Samambaia", "Medeiros",
    "Morada das Vinhas", "Ponte Sao Joao", "Retiro", "Tulipas",
    "Vianelo", "Vila Arens", "Vila Hortolandia", "Vila Rami",
]


TEMAS = {
    "saude": ["saude", "hospital", "ubs", "upa", "medico", "vacina", "medicamento"],
    "educacao": ["educacao", "escola", "creche", "ensino", "professor", "aluno"],
    "cultura": ["cultura", "teatro", "museu", "evento cultural", "biblioteca"],
    "obras": ["obras", "pavimentacao", "asfalto", "reforma", "infraestrutura"],
    "transporte": ["transporte", "mobilidade", "onibus", "transito", "terminal"],
    "seguranca": ["seguranca", "guarda municipal", "policia", "violencia"],
}


TECHNICAL_KEYS = {"id", "hash", "hash_arquivo", "hash_texto", "checksum", "metadata", "metadados"}


def seed_reference_data(db):
    for item in VEREADORES:
        normalized = normalize_text(item["nome"])
        vereador = db.query(models.Vereador).filter(models.Vereador.nome_normalizado == normalized).first()
        if not vereador:
            db.add(models.Vereador(
                nome=item["nome"],
                nome_normalizado=normalized,
                partido=item.get("partido"),
                ativo=True,
            ))

    for item in SECRETARIAS:
        normalized = normalize_text(item["nome"])
        secretaria = db.query(models.Secretaria).filter(models.Secretaria.nome_normalizado == normalized).first()
        if not secretaria:
            db.add(models.Secretaria(nome=item["nome"], nome_normalizado=normalized))

    db.commit()


def _context(original_text, needle, size=180):
    searchable_text = (original_text or "")[:50000]
    normalized = normalize_text(searchable_text)
    pos = normalized.find(normalize_text(needle))
    if pos < 0:
        return searchable_text[:size].strip()
    start = max(0, pos - size // 2)
    end = min(len(searchable_text), pos + size // 2)
    return re.sub(r"\s+", " ", searchable_text[start:end]).strip()


def _contains_alias(normalized_text, aliases):
    for alias in aliases:
        pattern = r"\b" + re.escape(normalize_text(alias)) + r"\b"
        if re.search(pattern, normalized_text):
            return alias
    return None


def _extract_values(text):
    return list(dict.fromkeys(re.findall(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?", text)))


def _extract_dates(text):
    return list(dict.fromkeys(re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", text)))


def _load_json(path):
    if not path:
        return None


def _load_csv_row(path):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as file:
            sample = file.read(4096)
            file.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,")
                reader = csv.DictReader(file, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(file, delimiter=";")
            return next(reader, None)
    except Exception:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            return json.load(file)
    except Exception:
        return None


def _read_nested(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _parse_year(doc, raw_json):
    value = _read_nested(raw_json, "normalizado", "ano")
    if value:
        try:
            return int(value)
        except Exception:
            pass
    source = " ".join(filter(None, [doc.titulo, doc.url_origem]))
    match = re.search(r"\b(20\d{2})\b", source)
    return int(match.group(1)) if match else None


def _parse_money(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    text = re.sub(r"[^\d,.-]", "", text)
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def _first_detected_secretaria(normalized_text):
    for item in SECRETARIAS:
        if _contains_alias(normalized_text, item["aliases"]):
            return item["nome"]
    return None


def _first_detected_theme(normalized_text):
    for tema, terms in TEMAS.items():
        if _contains_alias(normalized_text, terms):
            return tema
    return None


def _first_detected_bairro(normalized_text):
    for bairro in BAIRROS:
        if re.search(r"\b" + re.escape(normalize_text(bairro)) + r"\b", normalized_text):
            return bairro
    return None


def _add_entity(db, processed_id, tipo, valor, contexto, confianca):
    db.add(models.EntidadeExtraida(
        documento_processado_id=processed_id,
        tipo_entidade=tipo,
        valor=valor,
        contexto=contexto,
        confianca=confianca,
    ))


def extract_entities_for_document(db, processed_doc):
    seed_reference_data(db)

    doc = processed_doc.documento_bruto
    text = processed_doc.texto_limpo or processed_doc.texto_extraido or ""
    normalized = normalize_text(text)

    db.query(models.EntidadeExtraida).filter(
        models.EntidadeExtraida.documento_processado_id == processed_doc.id
    ).delete()
    if doc:
        db.query(models.AtuacaoVereador).filter(
            models.AtuacaoVereador.documento_bruto_id == doc.id
        ).delete()
        db.query(models.Despesa).filter(
            models.Despesa.fonte_documento_id == doc.id
        ).delete()
        db.query(models.Receita).filter(
            models.Receita.fonte_documento_id == doc.id
        ).delete()

    found_themes = []
    found_bairros = []

    for tema, terms in TEMAS.items():
        alias = _contains_alias(normalized, terms)
        if alias:
            found_themes.append(tema)
            _add_entity(db, processed_doc.id, "tema", tema, _context(text, alias), 0.82)

    for bairro in BAIRROS:
        if re.search(r"\b" + re.escape(normalize_text(bairro)) + r"\b", normalized):
            found_bairros.append(bairro)
            _add_entity(db, processed_doc.id, "bairro", bairro, _context(text, bairro), 0.78)

    for item in SECRETARIAS:
        alias = _contains_alias(normalized, item["aliases"])
        if alias:
            _add_entity(db, processed_doc.id, "secretaria", item["nome"], _context(text, alias), 0.76)

    for value in _extract_values(text)[:80]:
        _add_entity(db, processed_doc.id, "valor_monetario", value, _context(text, value), 0.70)

    for value in _extract_dates(text)[:80]:
        _add_entity(db, processed_doc.id, "data", value, _context(text, value), 0.70)

    for item in VEREADORES:
        alias = _contains_alias(normalized, item["aliases"])
        if not alias:
            continue

        vereador = db.query(models.Vereador).filter(
            models.Vereador.nome_normalizado == normalize_text(item["nome"])
        ).first()
        if not vereador:
            continue

        contexto = _context(text, alias, size=260)
        _add_entity(db, processed_doc.id, "vereador", item["nome"], contexto, 0.86)

        if doc:
            db.add(models.AtuacaoVereador(
                vereador_id=vereador.id,
                documento_bruto_id=doc.id,
                tipo_atuacao=doc.tipo_documento or "documento",
                titulo=doc.titulo,
                descricao=contexto,
                data_atuacao=doc.data_publicacao or doc.data_coleta,
                tema=found_themes[0] if found_themes else _first_detected_theme(normalized),
                bairro=found_bairros[0] if found_bairros else _first_detected_bairro(normalized),
                url_origem=doc.url_origem,
                confianca=0.78,
            ))

    if doc and doc.fonte == "portal_transparencia":
        if (doc.formato or "").lower() == "csv":
            registro = _load_csv_row(doc.caminho_arquivo) or {}
            raw_json = {}
            normalizado = {
                "ano": _parse_year(doc, {}),
                "secretaria": registro.get("descricao_secretaria"),
                "codigo_secretaria": registro.get("codigo_secretaria"),
                "objeto": "Despesa por classificacao orcamentaria agrupada por secretaria",
                "valor_empenhado": registro.get("empenhado"),
                "valor_liquidado": registro.get("liquidado"),
                "valor_pago": registro.get("pago"),
            }
        else:
            raw_json = _load_json(doc.caminho_arquivo)
            normalizado = raw_json.get("normalizado", {}) if isinstance(raw_json, dict) else {}
            registro = raw_json.get("registro_bruto", {}) if isinstance(raw_json, dict) else {}
        tipo_documento = doc.tipo_documento or normalizado.get("tipo_documento")

        if tipo_documento == "receita_classificacao":
            db.add(models.Receita(
                fonte_documento_id=doc.id,
                ano=_parse_year(doc, raw_json),
                classificacao=str(normalizado.get("classificacao") or registro.get("rubrica_receita") or ""),
                descricao=normalizado.get("descricao") or registro.get("descricao") or doc.titulo,
                valor_orcado=_parse_money(normalizado.get("valor_orcado") or registro.get("orcado")),
                valor_arrecadado=_parse_money(normalizado.get("valor_arrecadado") or registro.get("arrecadado")),
                percentual=_parse_money(normalizado.get("percentual") or registro.get("percentual")),
                data_referencia=doc.data_publicacao,
                url_origem=doc.url_origem,
            ))
        elif tipo_documento == "despesa_secretaria":
            db.add(models.Despesa(
                fonte_documento_id=doc.id,
                ano=_parse_year(doc, raw_json),
                secretaria=normalizado.get("secretaria") or registro.get("descricao") or registro.get("descricao_secretaria"),
                fornecedor=None,
                cnpj=None,
                objeto=normalizado.get("objeto") or doc.titulo,
                valor_empenhado=_parse_money(normalizado.get("valor_empenhado") or registro.get("empenhado")),
                valor_liquidado=_parse_money(normalizado.get("valor_liquidado") or registro.get("liquidado")),
                valor_pago=_parse_money(normalizado.get("valor_pago") or registro.get("pago")),
                data_referencia=doc.data_publicacao,
                url_origem=doc.url_origem,
            ))
        elif tipo_documento == "contrato":
            db.add(models.Despesa(
                fonte_documento_id=doc.id,
                ano=_parse_year(doc, raw_json),
                secretaria=normalizado.get("secretaria") or registro.get("desc_dotacao"),
                fornecedor=normalizado.get("fornecedor") or registro.get("nome_fornecedor"),
                cnpj=normalizado.get("cnpj") or registro.get("cnpj") or registro.get("cpf_cnpj"),
                objeto=normalizado.get("objeto") or registro.get("desc_resu_contrato") or doc.titulo,
                valor_empenhado=_parse_money(normalizado.get("valor_empenhado") or registro.get("valor_empenho")),
                valor_liquidado=_parse_money(normalizado.get("valor_liquidado") or registro.get("valor_liquidado")),
                valor_pago=_parse_money(normalizado.get("valor_pago") or registro.get("valor_pago")),
                data_referencia=doc.data_publicacao,
                url_origem=doc.url_origem,
            ))

    db.commit()
    return True


def extract_entities_for_all(db, limit=None):
    query = db.query(models.DocumentoProcessado).join(models.DocumentoBruto)
    if limit:
        query = query.limit(limit)
    count = 0
    for processed_doc in query.all():
        extract_entities_for_document(db, processed_doc)
        count += 1
    return count
