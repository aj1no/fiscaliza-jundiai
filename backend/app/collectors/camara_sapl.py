import logging
import re

import requests
from bs4 import BeautifulSoup

from ..database.database import SessionLocal
from ..ingestion.downloader import Deduplicator, Downloader
from ..models import models
from ..tasks.worker import process_document

logger = logging.getLogger(__name__)


class CamaraSAPLCollector:
    # Busca filtrada de materias no SAPL
    BASE_URL = (
        "https://sapl.jundiai.sp.leg.br/consultas/materia/"
        "materia_pesquisar_proc?hdn_cod_autor=617"
    )

    def collect(self):
        logger.info("Iniciando coleta de proposicoes (SAPL)")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        db = SessionLocal()
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            response = requests.get(self.BASE_URL, headers=headers, timeout=20, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            downloader = Downloader()
            new_count = 0
            items = soup.find_all("div", class_="materia-item") or soup.find_all("tr")

            for item in items:
                link = item.find("a", href=re.compile(r"materia_mostrar_proc"))
                if not link:
                    continue

                raw_title = item.get_text().strip().replace("\n", " ")
                titulo = re.sub(r"\s+", " ", raw_title)[:240]
                href = link["href"]
                if not href.startswith("http"):
                    href = f"https://sapl.jundiai.sp.leg.br{href}"

                # Salva o HTML da pagina de detalhe para permitir processamento posterior.
                download_data = downloader.download_file(href, subfolder="camara_sapl")
                if not download_data:
                    logger.warning("Falha ao baixar detalhe SAPL: %s", href)
                    continue

                if Deduplicator.is_duplicate(db, models.DocumentoBruto, download_data["hash"]):
                    continue

                doc_bruto = models.DocumentoBruto(
                    fonte="camara_sapl",
                    tipo_documento="proposicao_legislativa",
                    titulo=titulo,
                    url_origem=href,
                    formato=(download_data.get("formato") or "html").lower(),
                    caminho_arquivo=download_data.get("path"),
                    hash_arquivo=download_data.get("hash"),
                    status_processamento=models.StatusProcessamento.COLETADO.value,
                )
                db.add(doc_bruto)
                db.commit()

                process_document.delay(doc_bruto.id)
                new_count += 1

            return new_count
        except Exception as exc:
            db.rollback()
            logger.error("Erro na coleta SAPL: %s", exc)
            return 0
        finally:
            db.close()
