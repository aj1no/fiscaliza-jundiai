import os
import sys
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.sqltypes import Float, Numeric

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.analytics.service import receitas_analytics
from app.models import models


class FinancialReliabilityRulesTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_receita_total_geral(self, valor_arrecadado=850.5):
        self.db.add(
            models.Receita(
                fonte_documento_id=None,
                ano=2026,
                classificacao="100000000000000",
                descricao="Total Geral",
                valor_orcado=1000.0,
                valor_arrecadado=valor_arrecadado,
                percentual=0.8505,
                url_origem="https://exemplo.local/receita-total",
            )
        )
        self.db.commit()

    def test_partial_snapshot_marks_coleta_completa_false(self):
        self._add_receita_total_geral(850.5)
        self.db.add(
            models.ColetaSnapshot(
                fonte="portal_transparencia",
                categoria="receita_classificacao",
                tipo_documento="receita_classificacao",
                ano=2026,
                endpoint="https://endpoint.local/receitas",
                parametros='{"ano":"2026"}',
                status_code=200,
                coleta_completa=False,
                registros_encontrados=150,
                registros_coletados=150,
                limite_aplicado=150,
                total_itens_informado=150,
                hash_conteudo="hash-parcial",
                nivel_confiabilidade="consolidado",
                observacoes='["coleta parcial"]',
                criado_em=datetime.utcnow(),
            )
        )
        self.db.commit()

        payload = receitas_analytics(self.db, ano=2026, termo=None, limit=50)
        self.assertFalse(payload["metadados"]["coleta_completa"])
        self.assertIn(payload["metadados"]["nivel_confiabilidade"], {"parcial", "inseguro_para_soma"})

    def test_partial_financial_result_does_not_expose_total(self):
        self._add_receita_total_geral(850.5)
        self.db.add(
            models.ColetaSnapshot(
                fonte="portal_transparencia",
                categoria="receita_classificacao",
                tipo_documento="receita_classificacao",
                ano=2026,
                endpoint="https://endpoint.local/receitas",
                parametros='{"ano":"2026"}',
                status_code=200,
                coleta_completa=False,
                registros_encontrados=120,
                registros_coletados=120,
                limite_aplicado=120,
                total_itens_informado=120,
                hash_conteudo="hash-parcial-2",
                nivel_confiabilidade="consolidado",
                observacoes='["sem completude"]',
                criado_em=datetime.utcnow(),
            )
        )
        self.db.commit()

        payload = receitas_analytics(self.db, ano=2026, termo=None, limit=50)
        self.assertIsNone(payload["total_arrecadado"])
        self.assertEqual(payload["valor_arrecadado_coletado"], 850.5)
        self.assertFalse(payload["agregacao_receita"]["soma_segura"])

    def test_hierarchical_revenue_without_safe_pattern_blocks_sum(self):
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
        self.assertFalse(payload["agregacao_receita"]["soma_segura"])
        self.assertEqual(payload["agregacao_receita"]["metodo"], "hierarquia_indefinida")
        self.assertEqual(payload["metadados"]["nivel_confiabilidade"], "inseguro_para_soma")

    def test_financial_models_use_numeric_not_float(self):
        monetary_columns = [
            models.Despesa.__table__.c.valor_empenhado,
            models.Despesa.__table__.c.valor_liquidado,
            models.Despesa.__table__.c.valor_pago,
            models.Receita.__table__.c.valor_orcado,
            models.Receita.__table__.c.valor_arrecadado,
            models.Receita.__table__.c.percentual,
            models.ServidorRemuneracao.__table__.c.valor_total_venc,
            models.ServidorRemuneracao.__table__.c.valor_total_mes,
            models.ServidorRemuneracao.__table__.c.valor_salario_base,
        ]
        for column in monetary_columns:
            self.assertIsInstance(column.type, Numeric)
            self.assertNotIsInstance(column.type, Float)


if __name__ == "__main__":
    unittest.main()
