import pdfplumber
import logging

logger = logging.getLogger(__name__)

class PDFExtractor:
    @staticmethod
    def extract_text(file_path):
        """Extrai texto de um arquivo PDF local."""
        full_text = ""
        try:
            import fitz

            with fitz.open(file_path) as pdf:
                for page in pdf:
                    full_text += page.get_text("text") + "\n"
        except Exception as e:
            logger.error(f"Erro ao extrair texto com PyMuPDF de {file_path}: {e}")

        if full_text.strip():
            return full_text

        try:
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        full_text += page_text + "\n"
        except Exception as e:
            logger.error(f"Erro no fallback pdfplumber para {file_path}: {e}")

        return full_text
