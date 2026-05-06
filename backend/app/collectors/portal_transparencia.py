import hashlib
import csv
import io
import json
import logging
import os
import traceback
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from app.database.database import SessionLocal
from app.models import models

logger = logging.getLogger(__name__)


class PortalTransparenciaCollector:
    BASE_URL = "https://transparencia.jundiai.sp.gov.br/"
    MENU_LINKS_URL = "https://web.cijun.sp.gov.br/PMJ/MI/V1/Links?"
    LICITACOES_PAGE_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/Licitacao"
    LICITACOES_API_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesaPorLicitacao"
    DESPESAS_CLASSIFICACAO_PAGE_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/ClassificacaoOrcamentaria"
    DESPESAS_CLASSIFICACAO_API_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesasPorClassificacao"
    CONTRATOS_PAGE_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/Contrato"
    CONTRATOS_API_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Despesas/GetDespesaPorContrato"
    RECEITAS_CLASSIFICACAO_PAGE_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Receitas/ClassificacaoOrcamentaria"
    RECEITAS_CLASSIFICACAO_API_URL = "https://web21.cijun.sp.gov.br/PMJ/YC/Receitas/GetReceitasPorClassificacao"
    STORAGE_PATH = "storage/raw/portal_transparencia"
    DEFAULT_LIMIT = 100
    DEFAULT_PAGE_SIZE = 100
    MAX_PAGE_SIZE = 100
    MAX_LIMIT = 1000
    DEFAULT_FINANCIAL_LIMIT = 150
    MAX_FINANCIAL_LIMIT = 500

    def __init__(self):
        os.makedirs(self.STORAGE_PATH, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        })

    def _fetch(self, url, **kwargs):
        logger.info("Portal Transparencia acessando URL: %s", url)
        response = self.session.get(url, timeout=30, **kwargs)
        logger.info("Portal Transparencia status_code=%s URL=%s", response.status_code, url)
        response.raise_for_status()
        return response

    def _investigate_sources(self):
        details = []
        endpoints_found = 0

        home_response = self._fetch(self.BASE_URL)
        home_soup = BeautifulSoup(home_response.text, "html.parser")
        home_links = [link.get("href") for link in home_soup.select("a[href]")]
        endpoints_found += len([
            href for href in home_links
            if href and any(token in href.lower() for token in ("csv", "xls", "json", "api", "despesas"))
        ])
        details.append(
            f"home_url={self.BASE_URL}; status_code={home_response.status_code}; "
            f"links_encontrados={len(home_links)}"
        )

        menu_response = self._fetch(self.MENU_LINKS_URL)
        menu_links = menu_response.json()
        if not isinstance(menu_links, list):
            menu_links = []

        menu_urls = [item.get("URL") or item.get("url") or "" for item in menu_links if isinstance(item, dict)]
        menu_endpoints = [
            url for url in menu_urls
            if any(token in url.lower() for token in ("csv", "xls", "json", "api", "despesas", "licitacao"))
        ]
        endpoints_found += len(menu_endpoints)
        details.append(
            f"menu_url={self.MENU_LINKS_URL}; status_code={menu_response.status_code}; "
            f"links_encontrados={len(menu_links)}; endpoints_exportacoes_encontrados={len(menu_endpoints)}"
        )

        page_response = self._fetch(self.LICITACOES_PAGE_URL)
        page_soup = BeautifulSoup(page_response.text, "html.parser")
        page_text = page_soup.get_text(" ", strip=True)
        page_links = [link.get("href") for link in page_soup.select("a[href]")]
        if "GetDespesaPorLicitacao" in page_response.text:
            endpoints_found += 1
        details.append(
            f"pagina_licitacoes={self.LICITACOES_PAGE_URL}; status_code={page_response.status_code}; "
            f"links_encontrados={len(page_links)}; contem_endpoint_json="
            f"{'sim' if 'GetDespesaPorLicitacao' in page_response.text else 'nao'}; "
            f"titulo_pagina={page_text[:120]}"
        )

        details.append(f"total_endpoints_exportacoes_encontrados={endpoints_found}")
        return details

    def _env_int(self, name, default, minimum=None, maximum=None):
        raw_value = os.getenv(name)
        if raw_value is None or raw_value.strip() == "":
            value = default
        else:
            try:
                value = int(raw_value)
            except ValueError:
                logger.warning(
                    "Valor inválido para %s=%s. Usando padrão %s.",
                    name,
                    raw_value,
                    default,
                )
                value = default

        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _collect_config(self):
        return {
            "ano": self._env_int("PORTAL_TRANSPARENCIA_ANO", datetime.utcnow().year, 2000, 2100),
            "limit": self._env_int("PORTAL_TRANSPARENCIA_LIMIT", self.DEFAULT_LIMIT, 1, self.MAX_LIMIT),
            "page_size": self._env_int(
                "PORTAL_TRANSPARENCIA_PAGE_SIZE",
                self.DEFAULT_PAGE_SIZE,
                1,
                self.MAX_PAGE_SIZE,
            ),
            "despesas_secretaria_limit": self._env_int(
                "PORTAL_TRANSPARENCIA_DESPESAS_SECRETARIA_LIMIT",
                self.DEFAULT_FINANCIAL_LIMIT,
                1,
                self.MAX_FINANCIAL_LIMIT,
            ),
            "contratos_limit": self._env_int(
                "PORTAL_TRANSPARENCIA_CONTRATOS_LIMIT",
                self.DEFAULT_FINANCIAL_LIMIT,
                1,
                self.MAX_FINANCIAL_LIMIT,
            ),
            "receitas_limit": self._env_int(
                "PORTAL_TRANSPARENCIA_RECEITAS_LIMIT",
                self.DEFAULT_FINANCIAL_LIMIT,
                1,
                self.MAX_FINANCIAL_LIMIT,
            ),
        }

    def _licitacoes_params(self, year, page, per_page):
        return {
            "ano": str(year),
            "licitacao": "",
            "modalidade": "0",
            "objeto": "",
            "data_inicial": "",
            "data_final": "",
            "page": str(page),
            "per_page": str(per_page),
        }

    def _build_public_url(self, record):
        params = {
            "ano": record.get("exercicio") or datetime.utcnow().year,
            "licitacao": record.get("licitacao") or "",
            "modalidade": record.get("codigo_modalidade") or "0",
            "objeto": "",
            "data_inicial": "",
            "data_final": "",
        }
        return f"{self.LICITACOES_PAGE_URL}?{urlencode(params)}"

    def _build_title(self, record):
        number = record.get("licitacao") or "sem numero"
        year = record.get("exercicio") or datetime.utcnow().year
        modalidade = (record.get("modalidade") or "Licitação").strip()
        descricao = (record.get("descricao") or "Sem descrição").strip()
        title = f"Licitação {number}/{year} - {modalidade} - {descricao}"
        return title[:255]

    def _normalize_record(self, record, title, public_url, content_hash=None):
        year = record.get("exercicio")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None

        return {
            "fonte": "portal_transparencia",
            "tipo_documento": "licitacao",
            "titulo": title,
            "numero_licitacao": record.get("licitacao"),
            "modalidade": record.get("modalidade"),
            "objeto": record.get("descricao"),
            "data_publicacao": None,
            "data_referencia": None,
            "ano": year,
            "orgao": None,
            "valor": None,
            "registro_preco": record.get("registro_preco"),
            "codigo_modalidade": record.get("codigo_modalidade"),
            "url_origem": public_url,
            "hash_arquivo": content_hash,
            "data_coleta": None,
            "status_processamento": "coletado",
        }

    def _raw_payload(self, record, params, response_url, status_code, normalized):
        payload = {
            "fonte": "portal_transparencia",
            "categoria": "licitacao",
            "rastreabilidade": {
                "endpoint": self.LICITACOES_API_URL,
                "url_consulta": response_url,
                "parametros_consulta": params,
                "status_code": status_code,
            },
            "normalizado": normalized,
            "registro_bruto": record,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    def _save_raw_file(self, content_hash, raw_bytes, extension="json"):
        file_path = os.path.join(self.STORAGE_PATH, f"{content_hash}.{extension}")
        if not os.path.exists(file_path):
            with open(file_path, "wb") as file:
                file.write(raw_bytes)
        return file_path

    def _duplicate_reason(self, db, content_hash, url, title, publication_date, tipo_documento="licitacao"):
        hash_duplicate = db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.hash_arquivo == content_hash
        ).first()
        if hash_duplicate:
            return "hash"

        url_duplicate = db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.url_origem == url
        ).first()
        if url_duplicate:
            return "chave_composta"

        query = db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.fonte == "portal_transparencia",
            models.DocumentoBruto.tipo_documento == tipo_documento,
            models.DocumentoBruto.url_origem == url,
            models.DocumentoBruto.titulo == title,
        )
        if publication_date is None:
            query = query.filter(models.DocumentoBruto.data_publicacao.is_(None))
        else:
            query = query.filter(models.DocumentoBruto.data_publicacao == publication_date)
        if query.first() is not None:
            return "chave_composta"
        return None

    def _fetch_licitacoes_pages(self, config):
        fetched = []
        requests_info = []
        total_items = None
        page = 0

        while len(fetched) < config["limit"]:
            per_page = config["page_size"]
            params = self._licitacoes_params(config["ano"], page, per_page)
            response = self._fetch(self.LICITACOES_API_URL, params=params)
            data = response.json()
            records = data.get("licitacoes", []) if isinstance(data, dict) else []
            total_items = data.get("total_itens", total_items) if isinstance(data, dict) else total_items
            requests_info.append({
                "page": page,
                "per_page": per_page,
                "params": params,
                "url": response.url,
                "status_code": response.status_code,
                "records": len(records),
            })

            if not records:
                break

            for record in records:
                if len(fetched) >= config["limit"]:
                    break
                fetched.append({
                    "record": record,
                    "params": params.copy(),
                    "response_url": response.url,
                    "status_code": response.status_code,
                })

            if total_items is not None and len(fetched) >= int(total_items):
                break
            if len(records) < per_page:
                break

            page += 1

        return fetched, total_items, requests_info

    def _fetch_expense_summary_csv(self, config):
        params = {
            "ano": str(config["ano"]),
            "data_inicial": "1",
            "data_final": "12",
            "tipo": "1",
            "executaConsulta": "true",
            "per_page": "1000000",
            "tipo_download": "CSV",
            "page": "1",
        }
        response = self._fetch(self.DESPESAS_CLASSIFICACAO_PAGE_URL, params=params)
        csv_text = response.content.decode("utf-8-sig", errors="ignore")
        rows = list(csv.DictReader(io.StringIO(csv_text), delimiter=";"))
        return rows, params, response

    def _csv_row_bytes(self, row):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(row.keys()), delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
        return output.getvalue().encode("utf-8")

    def _raw_payload_generic(self, category, endpoint, record, params, response_url, status_code, normalized):
        payload = {
            "fonte": "portal_transparencia",
            "categoria": category,
            "rastreabilidade": {
                "endpoint": endpoint,
                "url_consulta": response_url,
                "parametros_consulta": params,
                "status_code": status_code,
            },
            "normalizado": normalized,
            "registro_bruto": record,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    def _save_portal_document(
        self,
        db,
        category,
        tipo_documento,
        title,
        public_url,
        normalized,
        record,
        params,
        response_url,
        status_code,
        endpoint,
        publication_date=None,
    ):
        normalized_for_hash = dict(normalized)
        normalized_for_hash["hash_arquivo"] = None
        normalized_for_hash["data_coleta"] = None
        raw_bytes = self._raw_payload_generic(
            category,
            endpoint,
            record,
            params,
            response_url,
            status_code,
            normalized_for_hash,
        )
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        duplicate_reason = self._duplicate_reason(
            db,
            content_hash,
            public_url,
            title,
            publication_date,
            tipo_documento=tipo_documento,
        )
        if duplicate_reason:
            logger.info(
                "[DUPLICADO] Portal Transparencia %s por %s: %s | endpoint=%s | params=%s | hash=%s",
                tipo_documento,
                duplicate_reason,
                title,
                endpoint,
                params,
                content_hash,
            )
            return duplicate_reason

        normalized["hash_arquivo"] = content_hash
        file_path = self._save_raw_file(content_hash, raw_bytes)
        db.add(models.DocumentoBruto(
            fonte="portal_transparencia",
            tipo_documento=tipo_documento,
            titulo=title[:255],
            url_origem=public_url,
            data_publicacao=publication_date,
            data_coleta=datetime.utcnow(),
            formato="json",
            caminho_arquivo=file_path,
            hash_arquivo=content_hash,
            hash_texto=None,
            status_processamento="coletado",
            erro_processamento=None,
        ))
        db.commit()
        logger.info(
            "[SALVO] Portal Transparencia %s: %s | endpoint=%s | params=%s | status_code=%s | hash=%s",
            tipo_documento,
            title,
            endpoint,
            params,
            status_code,
            content_hash,
        )
        return "salvo"

    def _save_portal_csv_row_document(
        self,
        db,
        tipo_documento,
        title,
        public_url,
        normalized,
        row,
        params,
        response_url,
        status_code,
        endpoint,
        publication_date=None,
    ):
        raw_bytes = self._csv_row_bytes(row)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        duplicate_reason = self._duplicate_reason(
            db,
            content_hash,
            public_url,
            title,
            publication_date,
            tipo_documento=tipo_documento,
        )
        if duplicate_reason:
            logger.info(
                "[DUPLICADO] Portal Transparencia CSV %s por %s: %s | endpoint=%s | params=%s | hash=%s",
                tipo_documento,
                duplicate_reason,
                title,
                endpoint,
                params,
                content_hash,
            )
            return duplicate_reason

        file_path = self._save_raw_file(content_hash, raw_bytes, extension="csv")
        db.add(models.DocumentoBruto(
            fonte="portal_transparencia",
            tipo_documento=tipo_documento,
            titulo=title[:255],
            url_origem=public_url,
            data_publicacao=publication_date,
            data_coleta=datetime.utcnow(),
            formato="csv",
            caminho_arquivo=file_path,
            hash_arquivo=content_hash,
            hash_texto=None,
            status_processamento="coletado",
            erro_processamento=None,
        ))
        db.commit()
        logger.info(
            "[SALVO] Portal Transparencia CSV %s: %s | endpoint=%s | params=%s | status_code=%s | hash=%s",
            tipo_documento,
            title,
            endpoint,
            params,
            status_code,
            content_hash,
        )
        return "salvo"

    def _normalize_expense_summary(self, record, year, title, public_url):
        return {
            "fonte": "portal_transparencia",
            "tipo_documento": "despesa_secretaria",
            "titulo": title,
            "ano": year,
            "codigo_secretaria": record.get("secretaria") or record.get("codigo_secretaria"),
            "secretaria": record.get("descricao") or record.get("descricao_secretaria"),
            "objeto": "Despesa por classificacao orcamentaria agrupada por secretaria",
            "valor_inicial": record.get("valor_inicial") or record.get("dotacao_inicial"),
            "creditos": record.get("creditos") or record.get("credito_adicional"),
            "dotacao": record.get("dotacao") or record.get("dotacao_atual"),
            "valor_empenhado": record.get("empenhado"),
            "valor_liquidado": record.get("liquidado"),
            "valor_pago": record.get("pago"),
            "data_inicio": record.get("data_inicio"),
            "data_fim": record.get("data_fim"),
            "url_origem": public_url,
            "hash_arquivo": None,
            "data_coleta": None,
            "status_processamento": "coletado",
        }

    def _normalize_contract(self, record, year, title, public_url):
        return {
            "fonte": "portal_transparencia",
            "tipo_documento": "contrato",
            "titulo": title,
            "ano": int(record.get("ano_contrato") or year),
            "codigo_secretaria": record.get("codigo_Secretaria"),
            "secretaria": record.get("desc_dotacao"),
            "fornecedor": record.get("nome_fornecedor"),
            "cnpj": record.get("cnpj") or record.get("cpf_cnpj"),
            "objeto": record.get("desc_resu_contrato"),
            "numero_contrato": record.get("numero_contrato"),
            "tipo_contrato": record.get("tipo_contrato"),
            "data_assinatura": record.get("data_ass_contrato"),
            "data_fim_prevista": record.get("data_prev_final"),
            "valor_original_contrato": record.get("valor_original_contrato"),
            "valor_aditado_contrato": record.get("valor_aditado_contrato"),
            "valor_atual_contrato": record.get("valor_atual_contrato"),
            "numero_empenho": record.get("numero_empenho"),
            "data_empenho": record.get("data_empenho"),
            "valor_empenhado": record.get("valor_empenho"),
            "saldo_empenho": record.get("saldo_empenho"),
            "url_origem": public_url,
            "hash_arquivo": None,
            "data_coleta": None,
            "status_processamento": "coletado",
        }

    def _normalize_revenue(self, record, year, title, public_url):
        return {
            "fonte": "portal_transparencia",
            "tipo_documento": "receita_classificacao",
            "titulo": title,
            "ano": year,
            "classificacao": record.get("rubrica_receita"),
            "descricao": record.get("descricao"),
            "situacao": record.get("situacao"),
            "valor_orcado": record.get("orcado"),
            "valor_arrecadado": record.get("arrecadado"),
            "percentual": record.get("percentual"),
            "url_origem": public_url,
            "hash_arquivo": None,
            "data_coleta": None,
            "status_processamento": "coletado",
        }

    def _collect_expense_summaries(self, db, config):
        stats = {"salvo": 0, "hash": 0, "chave_composta": 0, "erro": 0, "encontrado": 0}
        try:
            try:
                records, params, response = self._fetch_expense_summary_csv(config)
                endpoint = self.DESPESAS_CLASSIFICACAO_PAGE_URL
                using_csv = True
                logger.info(
                    "Portal Transparencia usando CSV oficial para despesas por secretaria: url=%s registros=%s",
                    response.url,
                    len(records),
                )
            except Exception as csv_error:
                logger.warning(
                    "Falha ao baixar CSV de despesas por secretaria; usando JSON como fallback: %s",
                    csv_error,
                )
                params = {
                    "ano": str(config["ano"]),
                    "data_inicial": "1",
                    "data_final": "12",
                    "tipo": "1",
                    "page": "1",
                }
                response = self._fetch(self.DESPESAS_CLASSIFICACAO_API_URL, params=params)
                data = response.json()
                records = data.get("retorno", []) if isinstance(data, dict) else []
                endpoint = self.DESPESAS_CLASSIFICACAO_API_URL
                using_csv = False

            stats["encontrado"] = len(records)
            for record in records[:config["despesas_secretaria_limit"]]:
                secretaria = record.get("descricao") or record.get("descricao_secretaria") or "Secretaria nao informada"
                code = record.get("secretaria") or record.get("codigo_secretaria") or "0"
                title = f"Despesa {config['ano']} - {secretaria}"
                public_url = (
                    f"{self.DESPESAS_CLASSIFICACAO_PAGE_URL}?"
                    f"{urlencode({'ano': config['ano'], 'data_inicial': 1, 'data_final': 12, 'tipo': 1, 'secretaria': code})}"
                )
                normalized = self._normalize_expense_summary(record, config["ano"], title, public_url)
                if using_csv:
                    result = self._save_portal_csv_row_document(
                        db,
                        "despesa_secretaria",
                        title,
                        public_url,
                        normalized,
                        record,
                        params.copy(),
                        response.url,
                        response.status_code,
                        endpoint,
                    )
                else:
                    result = self._save_portal_document(
                        db,
                        "despesa_secretaria",
                        "despesa_secretaria",
                        title,
                        public_url,
                        normalized,
                        record,
                        params.copy(),
                        response.url,
                        response.status_code,
                        endpoint,
                    )
                stats[result] = stats.get(result, 0) + 1
        except Exception as error:
            stats["erro"] += 1
            logger.error("Erro ao coletar despesas por secretaria: %s\n%s", error, traceback.format_exc())
        return stats

    def _collect_contracts(self, db, config):
        stats = {"salvo": 0, "hash": 0, "chave_composta": 0, "erro": 0, "encontrado": 0}
        params = {
            "tipo": "C",
            "ano": str(config["ano"]),
            "secretaria": "0",
            "nome_fornecedor": "",
            "codigo_fornecedor": "",
            "objeto": "",
            "contrato": "",
            "tipo_contrato": "0",
            "page": "0",
            "per_page": str(config["contratos_limit"]),
        }
        try:
            response = self._fetch(self.CONTRATOS_API_URL, params=params)
            data = response.json()
            records = data.get("contratos", []) if isinstance(data, dict) else []
            stats["encontrado"] = len(records)
            for record in records[:config["contratos_limit"]]:
                number = record.get("numero_contrato") or "sem-numero"
                year = record.get("ano_contrato") or config["ano"]
                fornecedor = (record.get("nome_fornecedor") or "Fornecedor nao informado").strip()
                objeto = (record.get("desc_resu_contrato") or "Objeto nao informado").strip()
                title = f"Contrato {number}/{year} - {fornecedor} - {objeto}"[:255]
                public_url = (
                    f"{self.CONTRATOS_PAGE_URL}?"
                    f"{urlencode({'tipo': 'C', 'ano': year, 'secretaria': record.get('codigo_Secretaria') or 0, 'contrato': number, 'tipo_contrato': record.get('tipo_contrato') or 0, 'empenho': record.get('numero_empenho') or ''})}"
                )
                normalized = self._normalize_contract(record, config["ano"], title, public_url)
                result = self._save_portal_document(
                    db,
                    "contrato",
                    "contrato",
                    title,
                    public_url,
                    normalized,
                    record,
                    params.copy(),
                    response.url,
                    response.status_code,
                    self.CONTRATOS_API_URL,
                )
                stats[result] = stats.get(result, 0) + 1
        except Exception as error:
            stats["erro"] += 1
            logger.error("Erro ao coletar contratos: %s\n%s", error, traceback.format_exc())
        return stats

    def _collect_revenues(self, db, config):
        stats = {"salvo": 0, "hash": 0, "chave_composta": 0, "erro": 0, "encontrado": 0}
        params = {
            "ano": str(config["ano"]),
            "mes_inicial": "1",
            "mes_final": "12",
        }
        try:
            response = self._fetch(self.RECEITAS_CLASSIFICACAO_API_URL, params=params)
            records = response.json()
            if not isinstance(records, list):
                records = []
            stats["encontrado"] = len(records)
            for record in records[:config["receitas_limit"]]:
                rubric = record.get("rubrica_receita") or "sem-rubrica"
                descricao = (record.get("descricao") or "Receita sem descricao").strip()
                title = f"Receita {config['ano']} - {descricao}"[:255]
                public_url = (
                    f"{self.RECEITAS_CLASSIFICACAO_PAGE_URL}?"
                    f"{urlencode({'ano': config['ano'], 'mes_inicial': 1, 'mes_final': 12, 'rubrica': rubric})}"
                )
                normalized = self._normalize_revenue(record, config["ano"], title, public_url)
                result = self._save_portal_document(
                    db,
                    "receita_classificacao",
                    "receita_classificacao",
                    title,
                    public_url,
                    normalized,
                    record,
                    params.copy(),
                    response.url,
                    response.status_code,
                    self.RECEITAS_CLASSIFICACAO_API_URL,
                )
                stats[result] = stats.get(result, 0) + 1
        except Exception as error:
            stats["erro"] += 1
            logger.error("Erro ao coletar receitas por classificacao: %s\n%s", error, traceback.format_exc())
        return stats

    def _collect_financial_categories(self, db, config):
        return {
            "despesa_secretaria": self._collect_expense_summaries(db, config),
            "contrato": self._collect_contracts(db, config),
            "receita_classificacao": self._collect_revenues(db, config),
        }

    def collect(self):
        logger.info("Iniciando coleta real do Portal da Transparência...")
        started_at = time.monotonic()
        db = SessionLocal()
        new_count = 0
        duplicate_hash_count = 0
        duplicate_composite_count = 0
        ignored_count = 0
        error_count = 0
        details = []

        log_entry = models.LogColeta(
            fonte="Portal Transparência",
            status="iniciado",
            mensagem="Coleta real iniciada: investigando HTML, links e endpoints públicos.",
        )
        db.add(log_entry)
        db.commit()

        try:
            details.extend(self._investigate_sources())

            config = self._collect_config()
            fetched_records, total_items, requests_info = self._fetch_licitacoes_pages(config)

            details.append(
                f"categoria_coletada=licitacao; ano_consultado={config['ano']}; "
                f"limite_configurado={config['limit']}; tamanho_pagina={config['page_size']}; "
                f"paginas_consultadas={len(requests_info)}; total_itens_informado={total_items}; "
                f"registros_encontrados={len(fetched_records)}"
            )
            logger.info(
                "Portal Transparencia categoria=licitacao ano=%s limite=%s paginas=%s registros_encontrados=%s total_itens=%s",
                config["ano"],
                config["limit"],
                len(requests_info),
                len(fetched_records),
                total_items,
            )

            if not fetched_records:
                message = "Coleta finalizada sem registros reais encontrados. " + " | ".join(details)
                logger.warning(message)
                log_entry.status = "sucesso"
                log_entry.mensagem = message
                db.commit()
                return 0

            for item in fetched_records:
                try:
                    record = item["record"]
                    params = item["params"]
                    title = self._build_title(record)
                    public_url = self._build_public_url(record)
                    publication_date = None
                    normalized = self._normalize_record(record, title, public_url)
                    raw_bytes = self._raw_payload(
                        record,
                        params,
                        item["response_url"],
                        item["status_code"],
                        normalized,
                    )
                    content_hash = hashlib.sha256(raw_bytes).hexdigest()

                    duplicate_reason = self._duplicate_reason(
                        db,
                        content_hash,
                        public_url,
                        title,
                        publication_date,
                        tipo_documento="licitacao",
                    )
                    if duplicate_reason:
                        if duplicate_reason == "hash":
                            duplicate_hash_count += 1
                        else:
                            duplicate_composite_count += 1
                        logger.info(
                            "[DUPLICADO] Licitação já cadastrada por %s: %s | endpoint=%s | params=%s | hash=%s",
                            duplicate_reason,
                            title,
                            self.LICITACOES_API_URL,
                            params,
                            content_hash,
                        )
                        continue

                    file_path = self._save_raw_file(content_hash, raw_bytes)
                    doc_bruto = models.DocumentoBruto(
                        fonte="portal_transparencia",
                        tipo_documento="licitacao",
                        titulo=title,
                        url_origem=public_url,
                        data_publicacao=publication_date,
                        data_coleta=datetime.utcnow(),
                        formato="json",
                        caminho_arquivo=file_path,
                        hash_arquivo=content_hash,
                        hash_texto=None,
                        status_processamento="coletado",
                        erro_processamento=None,
                    )
                    db.add(doc_bruto)
                    db.commit()
                    new_count += 1
                    logger.info(
                        "[SALVO] Licitação salva: %s | endpoint=%s | params=%s | status_code=%s | hash=%s | arquivo=%s",
                        title,
                        self.LICITACOES_API_URL,
                        params,
                        item["status_code"],
                        content_hash,
                        file_path,
                    )
                except Exception as record_error:
                    error_count += 1
                    db.rollback()
                    logger.error(
                        "Erro ao salvar registro do Portal da Transparência: %s\n%s",
                        record_error,
                        traceback.format_exc(),
                    )

            financial_stats = self._collect_financial_categories(db, config)
            for category, stats in financial_stats.items():
                new_count += stats.get("salvo", 0)
                duplicate_hash_count += stats.get("hash", 0)
                duplicate_composite_count += stats.get("chave_composta", 0)
                error_count += stats.get("erro", 0)
                details.append(
                    f"categoria={category}; registros_encontrados={stats.get('encontrado', 0)}; "
                    f"registros_salvos={stats.get('salvo', 0)}; duplicados_por_hash={stats.get('hash', 0)}; "
                    f"duplicados_por_chave_composta={stats.get('chave_composta', 0)}; erros={stats.get('erro', 0)}"
                )

            elapsed_seconds = time.monotonic() - started_at
            details.append(
                f"registros_salvos={new_count}; duplicados_por_hash={duplicate_hash_count}; "
                f"duplicados_por_chave_composta={duplicate_composite_count}; ignorados={ignored_count}; "
                f"erros={error_count}; tempo_total_segundos={elapsed_seconds:.2f}"
            )
            log_entry.status = "sucesso" if error_count == 0 else "parcial"
            log_entry.mensagem = "Coleta do Portal da Transparência finalizada. " + " | ".join(details)
            db.commit()
            logger.info(
                "Coleta Portal da Transparência finalizada: ano consultado=%s; registros encontrados=%s; "
                "registros novos salvos=%s; duplicados por hash=%s; duplicados por chave composta=%s; "
                "erros=%s; tempo total=%.2fs",
                config["ano"],
                len(fetched_records),
                new_count,
                duplicate_hash_count,
                duplicate_composite_count,
                error_count,
                elapsed_seconds,
            )
            return new_count
        except Exception as error:
            db.rollback()
            full_error = traceback.format_exc()
            logger.error("Erro na coleta do Portal da Transparência: %s\n%s", error, full_error)
            log_entry.status = "erro"
            log_entry.mensagem = (
                f"Erro na coleta do Portal da Transparência: {error}. "
                f"Detalhes anteriores: {' | '.join(details)}. Traceback: {full_error}"
            )
            db.commit()
            return 0
        finally:
            db.close()
