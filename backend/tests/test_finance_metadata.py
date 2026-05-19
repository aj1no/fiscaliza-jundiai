import os
import sys
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.analytics.service import _is_top_level_revenue_classification, _snapshot_metadata
from app.models import models


class FinanceMetadataTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_top_level_revenue_classification(self):
        self.assertTrue(_is_top_level_revenue_classification("100000000000000"))
        self.assertFalse(_is_top_level_revenue_classification("111111111111111"))

    def test_snapshot_metadata_fallback(self):
        metadata = _snapshot_metadata(self.db, category="receita_classificacao", ano=2026)
        self.assertEqual(metadata["nivel_confiabilidade"], "parcial")
        self.assertFalse(metadata["coleta_completa"])

    def test_snapshot_metadata_from_latest_snapshot(self):
        snapshot = models.ColetaSnapshot(
            fonte="portal_transparencia",
            categoria="receita_classificacao",
            tipo_documento="receita_classificacao",
            ano=2026,
            endpoint="https://endpoint.local",
            parametros='{"ano":2026}',
            status_code=200,
            coleta_completa=True,
            registros_encontrados=120,
            registros_coletados=120,
            limite_aplicado=None,
            total_itens_informado=120,
            hash_conteudo="abc123",
            nivel_confiabilidade="consolidado",
            observacoes='["ok"]',
            criado_em=datetime.utcnow(),
        )
        self.db.add(snapshot)
        self.db.commit()

        metadata = _snapshot_metadata(self.db, category="receita_classificacao", ano=2026)
        self.assertEqual(metadata["nivel_confiabilidade"], "consolidado")
        self.assertEqual(metadata["registros_encontrados"], 120)
        self.assertEqual(metadata["status_code"], 200)


if __name__ == "__main__":
    unittest.main()

