import hashlib
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
    STORAGE_PATH = "storage/raw/portal_transparencia"
    DEFAULT_LIMIT = 100
    DEFAULT_PAGE_SIZE = 100
    MAX_PAGE_SIZE = 100
    MAX_LIMIT = 1000

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

    def _save_raw_file(self, content_hash, raw_bytes):
        file_path = os.path.join(self.STORAGE_PATH, f"{content_hash}.json")
        if not os.path.exists(file_path):
            with open(file_path, "wb") as file:
                file.write(raw_bytes)
        return file_path

    def _duplicate_reason(self, db, content_hash, url, title, publication_date):
        hash_duplicate = db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.hash_arquivo == content_hash
        ).first()
        if hash_duplicate:
            return "hash"

        query = db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.fonte == "portal_transparencia",
            models.DocumentoBruto.tipo_documento == "licitacao",
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

                    duplicate_reason = self._duplicate_reason(db, content_hash, public_url, title, publication_date)
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
