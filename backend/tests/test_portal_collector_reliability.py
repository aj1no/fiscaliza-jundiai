import os
import sys
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.collectors.portal_transparencia import PortalTransparenciaCollector
from app.models import models


class PortalCollectorReliabilityTestCase(unittest.TestCase):
    def setUp(self):
        self.collector = PortalTransparenciaCollector()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.collector.STORAGE_PATH = self.temp_dir.name
        os.makedirs(self.collector.STORAGE_PATH, exist_ok=True)
        self.engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_validate_records_shape_rejects_invalid_payload(self):
        with self.assertRaises(ValueError):
            self.collector._validate_records_shape("licitacao", [{"ano": 2026}])

    def test_dynamic_document_is_updated_when_hash_changes(self):
        doc = models.DocumentoBruto(
            fonte="portal_transparencia",
            tipo_documento="despesa_secretaria",
            titulo="Despesa 2026 - Secretaria X",
            url_origem="https://exemplo.local/despesa?ano=2026&secretaria=1",
            data_publicacao=None,
            data_coleta=datetime.utcnow(),
            formato="json",
            caminho_arquivo="old.json",
            hash_arquivo="oldhash",
            status_processamento="processado",
        )
        self.db.add(doc)
        self.db.commit()

        result = self.collector._upsert_dynamic_document(
            self.db,
            tipo_documento="despesa_secretaria",
            title="Despesa 2026 - Secretaria X",
            public_url="https://exemplo.local/despesa?ano=2026&secretaria=1",
            publication_date=None,
            content_hash="newhash",
            file_path=os.path.join(tempfile.gettempdir(), "new.csv"),
            formato="csv",
            endpoint="https://endpoint.local",
            params={"ano": "2026"},
            status_code=200,
        )
        self.assertEqual(result, "salvo")

        updated = self.db.query(models.DocumentoBruto).filter_by(id=doc.id).first()
        self.assertEqual(updated.hash_arquivo, "newhash")
        self.assertEqual(updated.formato, "csv")
        self.assertEqual(updated.status_processamento, "coletado")

    def test_static_document_deduplication_uses_hash_and_composite_key(self):
        doc = models.DocumentoBruto(
            fonte="portal_transparencia",
            tipo_documento="licitacao",
            titulo="Licitacao 01/2026",
            url_origem="https://exemplo.local/licitacao?id=1",
            data_publicacao=None,
            data_coleta=datetime.utcnow(),
            formato="json",
            caminho_arquivo="licitacao.json",
            hash_arquivo="hash-licit-1",
            status_processamento="coletado",
        )
        self.db.add(doc)
        self.db.commit()

        duplicate_hash = self.collector._duplicate_reason(
            self.db,
            content_hash="hash-licit-1",
            url="https://exemplo.local/licitacao?id=1",
            title="Licitacao 01/2026",
            publication_date=None,
            tipo_documento="licitacao",
        )
        self.assertEqual(duplicate_hash, "hash")

        duplicate_composite = self.collector._duplicate_reason(
            self.db,
            content_hash="hash-licit-2",
            url="https://exemplo.local/licitacao?id=1",
            title="Licitacao 01/2026",
            publication_date=None,
            tipo_documento="licitacao",
        )
        self.assertEqual(duplicate_composite, "chave_composta")

    def test_dynamic_financial_same_url_new_value_updates_existing_document(self):
        public_url = "https://exemplo.local/despesa?ano=2026&secretaria=1"
        title = "Despesa 2026 - Secretaria X"
        base_payload = {
            "fonte": "portal_transparencia",
            "tipo_documento": "despesa_secretaria",
            "url_origem": public_url,
            "titulo": title,
            "ano": 2026,
            "secretaria": "Secretaria X",
            "status_processamento": "coletado",
        }

        first_result = self.collector._save_portal_document(
            self.db,
            "despesa_secretaria",
            "despesa_secretaria",
            title,
            public_url,
            dict(base_payload, valor="100.00"),
            {"descricao": "Secretaria X", "pago": "100.00"},
            {"ano": "2026"},
            "https://endpoint.local/despesa?ano=2026",
            200,
            "https://endpoint.local/despesa",
        )
        self.assertEqual(first_result, "salvo")
        first_doc = self.db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.fonte == "portal_transparencia",
            models.DocumentoBruto.tipo_documento == "despesa_secretaria",
            models.DocumentoBruto.url_origem == public_url,
        ).first()
        self.assertIsNotNone(first_doc)
        first_hash = first_doc.hash_arquivo

        second_result = self.collector._save_portal_document(
            self.db,
            "despesa_secretaria",
            "despesa_secretaria",
            title,
            public_url,
            dict(base_payload, valor="200.00"),
            {"descricao": "Secretaria X", "pago": "200.00"},
            {"ano": "2026"},
            "https://endpoint.local/despesa?ano=2026",
            200,
            "https://endpoint.local/despesa",
        )
        self.assertEqual(second_result, "salvo")

        docs = self.db.query(models.DocumentoBruto).filter(
            models.DocumentoBruto.fonte == "portal_transparencia",
            models.DocumentoBruto.tipo_documento == "despesa_secretaria",
            models.DocumentoBruto.url_origem == public_url,
        ).all()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].status_processamento, "coletado")
        self.assertNotEqual(docs[0].hash_arquivo, first_hash)

    def test_record_snapshot_is_append_only_for_traceability(self):
        self.collector._record_snapshot(
            self.db,
            categoria="despesa_secretaria",
            tipo_documento="despesa_secretaria",
            ano=2026,
            endpoint="https://endpoint.local/despesa",
            params={"ano": "2026", "page": "1"},
            status_code=200,
            registros_encontrados=18,
            registros_coletados=18,
            registros_novos=0,
            registros_atualizados=0,
            limite_aplicado=150,
            total_itens_informado=18,
            hash_conteudo="hash-1",
            coleta_completa=True,
            nivel_confiabilidade="consolidado",
            observacoes=["primeira coleta"],
        )
        self.collector._record_snapshot(
            self.db,
            categoria="despesa_secretaria",
            tipo_documento="despesa_secretaria",
            ano=2026,
            endpoint="https://endpoint.local/despesa",
            params={"ano": "2026", "page": "1"},
            status_code=200,
            registros_encontrados=18,
            registros_coletados=18,
            registros_novos=1,
            registros_atualizados=0,
            limite_aplicado=150,
            total_itens_informado=18,
            hash_conteudo="hash-2",
            coleta_completa=True,
            nivel_confiabilidade="consolidado",
            observacoes=["segunda coleta"],
        )

        snapshots = self.db.query(models.ColetaSnapshot).filter(
            models.ColetaSnapshot.fonte == "portal_transparencia",
            models.ColetaSnapshot.categoria == "despesa_secretaria",
            models.ColetaSnapshot.ano == 2026,
        ).all()
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0].registros_encontrados, 18)
        self.assertEqual(snapshots[0].registros_coletados, 18)
        self.assertEqual(snapshots[0].registros_novos, 0)


if __name__ == "__main__":
    unittest.main()
