import hashlib
import os
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Downloader:
    STORAGE_PATH = "storage/raw"
    
    def __init__(self):
        os.makedirs(self.STORAGE_PATH, exist_ok=True)

    def download_file(self, url, subfolder=""):
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            content = response.content
            file_hash = hashlib.sha256(content).hexdigest()
            
            # Determine extension
            ext = url.split('.')[-1].split('?')[0] if '.' in url else 'html'
            filename = f"{file_hash}.{ext}"
            
            dest_dir = os.path.join(self.STORAGE_PATH, subfolder)
            os.makedirs(dest_dir, exist_ok=True)
            
            file_path = os.path.join(dest_dir, filename)
            
            with open(file_path, 'wb') as f:
                f.write(content)
                
            return {
                'path': file_path,
                'hash': file_hash,
                'formato': ext.upper(),
                'tamanho': len(content)
            }
        except Exception as e:
            logger.error(f"Erro ao baixar arquivo {url}: {e}")
            return None

class Deduplicator:
    @staticmethod
    def is_duplicate(db_session, model, file_hash):
        from ..models import models
        exists = db_session.query(model).filter(model.hash_arquivo == file_hash).first()
        return exists is not None
