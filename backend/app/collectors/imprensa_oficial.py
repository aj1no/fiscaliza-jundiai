import requests
from bs4 import BeautifulSoup
import re
import logging
import traceback
from urllib.parse import urljoin
from datetime import datetime
from app.ingestion.downloader import Downloader
from app.models import models
from app.database.database import SessionLocal

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class ImprensaOficialCollector:
    BASE_URL = "https://imprensaoficial.jundiai.sp.gov.br/"
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

    def _parse_publication_date(self, text):
        match = re.search(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", text, re.I)
        if not match:
            return None

        day, month_name, year = match.groups()
        month = self.MONTHS_PT.get(month_name.lower())
        if not month:
            return None

        return datetime(int(year), month, int(day))

    def _extract_edition_title(self, text):
        match = re.search(r"Edição\s+(Extra\s+)?(\d+)", text, re.I)
        if not match:
            return None, "Edição Imprensa Oficial"

        extra_label = "Extra " if match.group(1) else ""
        edition_number = match.group(2)
        return edition_number, f"Imprensa Oficial - Edição {extra_label}{edition_number}"

    def collect(self):
        logger.info("=== [INICIANDO] COLETOR ROBUSTO DA IMPRENSA OFICIAL ===")
        db = SessionLocal()
        new_count = 0
        log_entry = models.LogColeta(
            fonte="Imprensa Oficial", 
            status="iniciado", 
            mensagem="Iniciando coleta com lógica de deep scraping v3"
        )
        db.add(log_entry)
        db.commit()

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            requests.packages.urllib3.disable_warnings()
            
            # 1. Acessar a Home
            logger.info(f"Acessando Home: {self.BASE_URL}")
            response = requests.get(self.BASE_URL, headers=headers, timeout=25, verify=False)
            logger.info(f"Status Home: {response.status_code}")
            
            if response.status_code != 200:
                raise Exception(f"Erro ao acessar home: Status {response.status_code}")

            soup = BeautifulSoup(response.text, 'html.parser')
            all_links = soup.find_all('a')
            logger.info(f"Total de links encontrados na home: {len(all_links)}")

            # 2. Filtrar links de Edições (Páginas intermédias)
            edition_pages = []
            for a in all_links:
                href = a.get('href')
                text = a.get_text().strip()
                
                # Critérios: conter /edicao- ou ter "Edição" no texto
                if href and ('/edicao-' in href or 'Edição' in text):
                    full_url = urljoin(self.BASE_URL, href)
                    if full_url not in edition_pages:
                        edition_pages.append(full_url)

            logger.info(f"Links de edições identificados: {len(edition_pages)}")
            
            # 3. Limitar às 10 edições mais recentes
            target_pages = edition_pages[:10]
            downloader = Downloader()

            # 4. Processar cada página de edição
            for ed_url in target_pages:
                try:
                    logger.info(f"--- Visitando Edição: {ed_url} ---")
                    ed_res = requests.get(ed_url, headers=headers, timeout=20, verify=False)
                    logger.info(f"  Status Edição: {ed_res.status_code}")
                    
                    if ed_res.status_code != 200:
                        continue

                    ed_soup = BeautifulSoup(ed_res.text, 'html.parser')
                    
                    # Extrair título, número e data da edição a partir do texto completo da página.
                    page_text = ed_soup.get_text(" ", strip=True)
                    _, final_title = self._extract_edition_title(page_text)
                    publication_date = self._parse_publication_date(page_text)

                    # 5. Procurar o PDF (Seletores flexíveis)
                    # a) Link terminando em .pdf
                    # b) Texto contendo "Baixe"
                    # c) href contendo wp-content/uploads
                    pdf_link_tag = (
                        ed_soup.find('a', href=re.compile(r'\.pdf$', re.I)) or
                        ed_soup.find('a', string=re.compile(r'Baixe', re.I)) or
                        ed_soup.find('a', href=re.compile(r'wp-content/uploads', re.I))
                    )

                    if not pdf_link_tag:
                        logger.warning(f"  [AVISO] PDF não encontrado na página {ed_url}")
                        continue

                    pdf_href = pdf_link_tag.get('href')
                    pdf_url = urljoin(ed_url, pdf_href) # Normalização robusta
                    
                    logger.info(f"  [PDF] Encontrado: {pdf_url}")

                    # 6. Download e Verificação de Duplicidade
                    download_data = downloader.download_file(pdf_url, "imprensa_oficial")
                    if not download_data:
                        continue

                    existing_doc = db.query(models.DocumentoBruto).filter(
                        models.DocumentoBruto.hash_arquivo == download_data['hash']
                    ).first()
                    if existing_doc:
                        if final_title and existing_doc.titulo != final_title:
                            existing_doc.titulo = final_title
                        if publication_date and not existing_doc.data_publicacao:
                            existing_doc.data_publicacao = publication_date
                        if download_data.get('formato') and existing_doc.formato != download_data['formato']:
                            existing_doc.formato = download_data['formato']
                        db.commit()
                        logger.info(f"  [PULANDO] Documento já processado anteriormente.")
                        continue

                    # 7. Salvar Documento Bruto
                    try:
                        doc_bruto = models.DocumentoBruto(
                            fonte="imprensa_oficial",
                            tipo_documento="edicao_imprensa_oficial",
                            titulo=final_title,
                            url_origem=ed_url, # URL da página da edição
                            data_publicacao=publication_date,
                            formato=download_data.get('formato', 'PDF'),
                            caminho_arquivo=download_data['path'],
                            hash_arquivo=download_data['hash'],
                            status_processamento="coletado",
                            data_coleta=datetime.utcnow()
                        )
                        db.add(doc_bruto)
                        db.commit()
                        logger.info(f"  [SUCESSO] Salvo no banco: {final_title}")
                        
                        # Enfileirar processamento de texto
                        from app.tasks.worker import process_document
                        process_document.delay(doc_bruto.id)
                        new_count += 1
                        
                    except Exception as db_err:
                        db.rollback()
                        logger.error(f"  [ERRO BANCO] Falha ao inserir: {db_err}")
                        logger.error(traceback.format_exc())

                except Exception as ed_err:
                    logger.error(f"  [ERRO EDIÇÃO] Falha ao processar {ed_url}: {ed_err}")
                    continue

            # 8. Finalizar Log Geral
            log_entry.status = "sucesso"
            log_entry.mensagem = f"Coleta finalizada. {new_count} novos documentos salvos."
            db.commit()
            logger.info(f"=== [FINALIZADO] {new_count} NOVOS DOCUMENTOS ===")
            return new_count

        except Exception as e:
            error_msg = f"Erro crítico no coletor: {str(e)}"
            logger.error(error_msg)
            log_entry.status = "erro"
            log_entry.mensagem = error_msg
            db.commit()
            return 0
        finally:
            db.close()
