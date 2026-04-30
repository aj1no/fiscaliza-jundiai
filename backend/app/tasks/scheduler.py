from apscheduler.schedulers.blocking import BlockingScheduler
from ..collectors.imprensa_oficial import ImprensaOficialCollector
from ..collectors.camara_sessoes import CamaraSessoesCollector
from ..database.database import SessionLocal
from ..models import models
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_collectors():
    logger.info("Iniciando coleta periódica...")
    db = SessionLocal()
    
    try:
        # Coleta Imprensa Oficial
        imprensa = ImprensaOficialCollector()
        edicoes = imprensa.fetch_latest_editions()
        for ed in edicoes:
            # Verificar se já existe
            exists = db.query(models.EdicaoImprensaOficial).filter(models.EdicaoImprensaOficial.numero == ed['numero']).first()
            if not exists:
                new_ed = models.EdicaoImprensaOficial(
                    numero=ed['numero'],
                    data_edicao=ed['data_edicao'],
                    url_pdf=ed['url_pdf']
                )
                db.add(new_ed)
                logger.info(f"Nova edição coletada: {ed['numero']}")
        
        # Coleta Câmara
        camara = CamaraSessoesCollector()
        sessoes = camara.fetch_session_summaries()
        for sessao in sessoes:
            exists = db.query(models.SessaoCamara).filter(models.SessaoCamara.url_origem == sessao['url_origem']).first()
            if not exists:
                new_sessao = models.SessaoCamara(
                    titulo=sessao['titulo'],
                    data_sessao=sessao['data_sessao'],
                    url_origem=sessao['url_origem']
                )
                db.add(new_sessao)
                logger.info(f"Novo resumo de sessão coletado: {sessao['titulo']}")
        
        db.commit()
        logger.info("Coleta finalizada com sucesso.")
    except Exception as e:
        logger.error(f"Erro durante a coleta: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    scheduler = BlockingScheduler()
    # Executa a cada 6 horas
    scheduler.add_job(run_collectors, 'interval', hours=6)
    
    # Execução imediata na primeira vez
    run_collectors()
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
