from pathlib import Path
import unittest

from mdbs_scraper.adapters.common import generic_record_from_row
from mdbs_scraper.parsers import extract_label_values, parse_tables, table_to_dicts


FIXTURES = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_caf_label_mapping(self):
        html = (FIXTURES / "caf_detail.html").read_text(encoding="utf-8")
        fields = extract_label_values(html)
        fields["project_name"] = "Programa Municipios Sostenibles"
        record = generic_record_from_row(
            fields, default_currency="USD", source_url="https://www.caf.com/es/proyectos/test/",
            source_format="html", day_first=True,
        )
        self.assertEqual(record.project_id, "CFA010689")
        self.assertEqual(record.commitment_year, 2022)
        self.assertEqual(record.loan_type, "Non-sovereign")
        self.assertEqual(str(record.total_project_cost), "120000000")
        self.assertEqual(str(record.loan_amount), "75000000")
        self.assertEqual(str(record.total_disbursement), "31500000.0")
        self.assertEqual(record.conditionality, "Publicar informes semestrales")

    def test_table_to_dicts(self):
        tables = parse_tables("<table><tr><th>Project Name</th><th>Country</th></tr>"
                              "<tr><td>Road</td><td>Kenya</td></tr></table>")
        self.assertEqual(table_to_dicts(tables[0]), [{"project_name": "Road", "country": "Kenya"}])


if __name__ == "__main__":
    unittest.main()
