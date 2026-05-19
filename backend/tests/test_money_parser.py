import os
import sys
import unittest
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.analytics.entity_extractor import _parse_money


class MoneyParserTestCase(unittest.TestCase):
    def test_parse_brl_string(self):
        self.assertEqual(_parse_money("R$ 1.234,56"), Decimal("1234.56"))

    def test_parse_plain_number(self):
        self.assertEqual(_parse_money("9876.10"), Decimal("9876.10"))

    def test_parse_invalid_value(self):
        self.assertIsNone(_parse_money("sem_valor"))

    def test_parse_none(self):
        self.assertIsNone(_parse_money(None))


if __name__ == "__main__":
    unittest.main()

