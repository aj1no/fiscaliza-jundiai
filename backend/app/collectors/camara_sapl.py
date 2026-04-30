import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import logging
from ..ingestion.downloader import Downloader, Deduplicator
from ..models import models
from ..tasks.worker import process_document
from ..database.database import SessionLocal

logger = logging.getLogger(__name__)

class CamaraSAPLCollector:
    # URL de pesquisa filtrada pela autora Mariana Janeiro (ID 617)
    BASE_URL = "https://sapl.jundiai.sp.leg.br/consultas/materia/materia_pesquisar_proc?hdn_cod_autor=617"

    def collect(self):
        logger.info("Iniciando coleta de proposições (SAPL)...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        try:
            import urllib3
            import hashlib
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            response = requests.get(self.BASE_URL, headers=headers, timeout=20, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            downloader = Downloader()
            db = SessionLocal()
            
            new_count = 0
            # No SAPL, as matérias costumam estar em uma tabela ou lista de divs
            items = soup.find_all('div', class_='materia-item') or soup.find_all('tr')
            
            for item in items:
                link = item.find('a', href=re.compile(r'materia_mostrar_proc'))
                if not link: continue
                
                titulo = item.get_text().strip().replace('\n', ' ')
                titulo = re.sub(r'\s+', ' ', titulo)[:200]
                href = "https://sapl.jundiai.sp.leg.br" + link['href'] if not link['href'].startswith('http') else link['href']
                
                # Para o SAPL, o "bruto" pode ser o HTML da página da matéria ou o PDF se disponível
                # Vamos salvar o link da página como HTML bruto por enquanto
                download_data = {
                    'hash': hashlib.sha256(href.encode()).hexdigest(),
                    'path': None, # SAPL links are pages
                    'formato': 'HTML'
                }
                
                import hashlib
                h = hashlib.sha256(href.encode()).hexdigest()

                if Deduplicator.is_duplicate(db, models.DocumentoBruto, h):
                    continue

                doc_bruto = models.DocumentoBruto(
                    fonte="Câmara Municipal (SAPL)",
                    tipo_documento="Proposição Legislativa",
                    titulo=titulo,
                    url_origem=href,
                    formato="HTML",
                    hash_arquivo=h,
                    status_processamento=models.StatusProcessamento.COLETADO
                )
                db.add(doc_bruto)
                db.commit()
                
                # Enfileirar processamento
                process_document.delay(doc_bruto.id)
                new_count += 1
                
            db.close()
            return new_count
        except Exception as e:
            logger.error(f"Erro na coleta SAPL: {e}")
            return 0
