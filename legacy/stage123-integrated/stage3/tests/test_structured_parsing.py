from __future__ import annotations

from pathlib import Path
import unittest

from stage3.parsing.structured import parse_structured_evidence


FIXTURES = Path(__file__).parent / "fixtures"


class StructuredParsingTests(unittest.TestCase):
    def test_parses_dart_xml_units_and_delta_values(self):
        parsed = parse_structured_evidence((FIXTURES / "samsung_dart.xml").read_text(encoding="utf-8"))
        self.assertEqual(parsed.source_format, "xml")
        self.assertEqual(parsed.tables[0].unit_label, "억원, %")
        cells = parsed.numeric_cells
        self.assertTrue(any(cell["row_label"] == "연결조정 후" and cell["column_label"] == "매출액" for cell in cells))
        self.assertIn("△120", parsed.text)

    def test_parses_html_in_xml_extension_and_colspan_rowspan(self):
        parsed = parse_structured_evidence((FIXTURES / "hd_hyundai_electric.html.xml").read_text(encoding="utf-8"))
        self.assertEqual(parsed.source_format, "html")
        self.assertEqual(parsed.tables[0].rows[0][1], "2500kVA 배전변압기 등 3,500대")
        self.assertEqual(parsed.tables[0].rows[2][1], "2023-01-30")
        self.assertTrue(any(cell["row_label"] == "계약금액(원)" for cell in parsed.numeric_cells))

    def test_parses_html_table_and_preserves_connection_adjustment_rows(self):
        parsed = parse_structured_evidence((FIXTURES / "hanwha_aerospace.html").read_text(encoding="utf-8"))
        self.assertEqual(parsed.source_format, "html")
        self.assertEqual(parsed.tables[0].basis_label, "연결조정")
        self.assertTrue(any("연결조정 후" in cell["row_label"] for cell in parsed.numeric_cells))

    def test_plain_text_and_malformed_markup_have_explicit_fallback(self):
        plain = parse_structured_evidence("매출액 100억원")
        self.assertEqual(plain.source_format, "text")
        malformed = parse_structured_evidence("<DOCUMENT><TABLE><TR><TD>매출액")
        self.assertTrue(malformed.warnings)


if __name__ == "__main__":
    unittest.main()
