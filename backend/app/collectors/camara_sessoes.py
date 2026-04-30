import hashlib
import logging
import os
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.database.database import SessionLocal
from app.models import models

logger = logging.getLogger(__name__)


class CamaraSessoesCollector:
    LIST_URL = "https://sapl.jundiai.sp.leg.br/consultas/sessao_plenaria/sessao_plenaria_index_html?iframe=1"
    STORAGE_PATH = "storage/raw/camara_sessoes"
    MAX_SESSIONS = 10
    MONTHS_PT = {
        "janeiro": 1,
        "fevereiro": 2,
        "março": 3,
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

    def __init__(self):
        os.makedirs(self.STORAGE_PATH, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        })

    def _parse_publication_date(self, text):
        match = re.search(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", text, re.I)
        if not match:
            return None

        day, month_name, year = match.groups()
        month = self.MONTHS_PT.get(month_name.lower())
        if not month:
            return None

        return datetime(int(year), month, int(day))

    def _clean_text(self, text):
        return re.sub(r"\s+", " ", text or "").strip()

    def _extract_sessions(self, html):
        soup = BeautifulSoup(html, "html.parser")
        sessions = []

        for item in soup.select("li.list-group-item"):
            title_tag = item.select_one("p.card-title")
            if not title_tag:
                continue

            link_tag = title_tag.find_parent("a")
            if not link_tag or not link_tag.get("href"):
                logger.info("Sessão sem página oficial detalhada ignorada: %s", self._clean_text(title_tag.get_text()))
                continue

            title = self._clean_text(title_tag.get_text()).title()
            detail_url = urljoin(self.LIST_URL, link_tag["href"])
            item_text = self._clean_text(item.get_text(" ", strip=True))
            publication_date = self._parse_publication_date(item_text)

            sessions.append({
                "title": title,
                "url": detail_url,
                "date": publication_date,
                "fallback_content": item_text,
            })

        return sessions

    def _next_page_url(self, html):
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.select("ul.pagination a.page-link"):
            label = self._clean_text(link.get_text())
            if label in {">>", "Próxima", "Proxima"} and link.get("href"):
                return urljoin(self.LIST_URL, link["href"])
        return None

    def _download_session_html(self, url):
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def _save_html(self, content_hash, html):
        file_path = os.path.join(self.STORAGE_PATH, f"{content_hash}.html")
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html)
        return file_path

    def collect(self):
        logger.info("Iniciando coleta real da Câmara (Sessões SAPL)...")
        db = SessionLocal()
        new_count = 0
        log_entry = models.LogColeta(
            fonte="Câmara Sessões",
            status="iniciado",
            mensagem="Coleta real de sessões plenárias iniciada",
        )
        db.add(log_entry)
        db.commit()

        try:
            sessions = []
            page_url = self.LIST_URL
            visited_pages = set()

            while page_url and len(sessions) < self.MAX_SESSIONS and page_url not in visited_pages:
                visited_pages.add(page_url)
                logger.info("Acessando listagem de sessões: %s", page_url)
                response = self.session.get(page_url, timeout=30)
                response.raise_for_status()

                for session_data in self._extract_sessions(response.text):
                    if len(sessions) >= self.MAX_SESSIONS:
                        break
                    if any(existing["url"] == session_data["url"] for existing in sessions):
                        continue
                    sessions.append(session_data)

                page_url = self._next_page_url(response.text)

            logger.info("Sessões com página oficial encontradas no MVP: %s", len(sessions))

            for session_data in sessions:
                title = session_data["title"]
                url = session_data["url"]
                publication_date = session_data["date"]

                existing = db.query(models.DocumentoBruto).filter(
                    models.DocumentoBruto.url_origem == url
                ).first()
                if existing:
                    logger.info("[PULANDO] Sessão já cadastrada: %s", title)
                    continue

                try:
                    html = self._download_session_html(url)
                    hash_source = html
                except Exception as download_error:
                    logger.warning(
                        "Não foi possível baixar detalhe da sessão %s (%s). Salvando metadados mínimos.",
                        url,
                        download_error,
                    )
                    html = session_data["fallback_content"]
                    hash_source = f"{url}|{title}|{publication_date or ''}"

                content_hash = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()
                duplicate_hash = db.query(models.DocumentoBruto).filter(
                    models.DocumentoBruto.hash_arquivo == content_hash
                ).first()
                if duplicate_hash:
                    logger.info("[PULANDO] Hash já cadastrado para sessão: %s", title)
                    continue

                file_path = self._save_html(content_hash, html)
                doc_bruto = models.DocumentoBruto(
                    fonte="camara_sessoes",
                    tipo_documento="sessao_plenaria",
                    titulo=title,
                    url_origem=url,
                    data_publicacao=publication_date,
                    formato="html",
                    caminho_arquivo=file_path,
                    hash_arquivo=content_hash,
                    hash_texto=content_hash,
                    status_processamento="coletado",
                    data_coleta=datetime.utcnow(),
                )
                db.add(doc_bruto)
                db.commit()
                new_count += 1
                logger.info("[SUCESSO] Sessão salva: %s (%s)", title, url)

                from app.tasks.worker import process_document
                process_document.delay(doc_bruto.id)

            log_entry.status = "sucesso"
            log_entry.mensagem = f"Coleta finalizada. {new_count} novas sessões salvas."
            db.commit()
            logger.info("Coleta da Câmara finalizada: %s novas sessões", new_count)
            return new_count
        except Exception as e:
            logger.error("Erro na coleta da Câmara Sessões: %s", e)
            db.rollback()
            log_entry.status = "erro"
            log_entry.mensagem = f"Erro: {str(e)}"
            db.commit()
            return 0
        finally:
            db.close()
