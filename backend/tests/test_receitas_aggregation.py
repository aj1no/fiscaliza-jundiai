import os
import sys
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.analytics.service import receitas_analytics
from app.models import models


class ReceitasAggregationTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_receitas_blocks_unsafe_total_sum(self):
        self.db.add_all([
            models.Receita(
                fonte_documento_id=None,
                ano=2026,
                classificacao="111111111111111",
                descricao="Receita detalhada A",
                valor_orcado=100.0,
                valor_arrecadado=90.0,
                percentual=0.9,
                url_origem="https://exemplo.local/a",
            ),
            models.Receita(
                fonte_documento_id=None,
                ano=2026,
                classificacao="222222222222222",
                descricao="Receita detalhada B",
                valor_orcado=150.0,
                valor_arrecadado=120.0,
                percentual=0.8,
                url_origem="https://exemplo.local/b",
            ),
        ])
        self.db.commit()

        payload = receitas_analytics(self.db, ano=2026, termo=None, limit=20)
        self.assertIsNone(payload["total_arrecadado"])
        self.assertEqual(payload["metadados"]["nivel_confiabilidade"], "inseguro_para_soma")

    def test_receitas_uses_total_geral_when_available(self):
        self.db.add(
            models.Receita(
                fonte_documento_id=None,
                ano=2026,
                classificacao="100000000000000",
                descricao="Total Geral",
                valor_orcado=1000.0,
                valor_arrecadado=850.5,
                percentual=0.8505,
                url_origem="https://exemplo.local/total",
            )
        )
        self.db.commit()

        payload = receitas_analytics(self.db, ano=2026, termo=None, limit=20)
        self.assertIsNone(payload["total_arrecadado"])
        self.assertEqual(payload["valor_arrecadado_coletado"], 850.5)
        self.assertEqual(payload["metadados"]["nivel_confiabilidade"], "parcial")


if __name__ == "__main__":
    unittest.main()
