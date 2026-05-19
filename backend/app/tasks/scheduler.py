"""
Compatibilidade legada.

Este arquivo existia com APScheduler e modelos antigos.
Hoje o agendamento oficial e suportado pelo Celery Beat
(servico `scheduler` no docker-compose), definido em
`app/tasks/worker.py`.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from .worker import run_all_collectors

logger = logging.getLogger(__name__)


def run_collectors():
    logger.warning(
        "scheduler.py (APScheduler) esta em modo legada; use Celery Beat em producao."
    )
    task = run_all_collectors.delay()
    logger.info("Task run_all_collectors enfileirada: %s", task.id)


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(run_collectors, "interval", hours=6)
    run_collectors()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
