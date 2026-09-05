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

    def test_parses_markdown_tables_from_local_chunk_index(self):
        parsed = parse_structured_evidence(
            "[삼성전자 | 사업보고서 (2023.12)]\n"
            "| 과목 | 주석 | 제 55 (당) 기 | 제 54 (전) 기 |\n"
            "| --- | --- | --- | --- |\n"
            "| Ⅰ. 매    출    액 | 29 |  | 258,935,494 |  | 302,231,360 |"
        )
        self.assertEqual(parsed.source_format, "markdown")
        values = {cell["value"] for cell in parsed.numeric_cells if "매" in cell["row_label"]}
        self.assertEqual(values, {"258,935,494", "302,231,360"})
        columns = [cell["column_label"] for cell in parsed.numeric_cells if "매" in cell["row_label"]]
        self.assertEqual(columns, ["제 55 (당) 기", "제 54 (전) 기"])

    def test_footnote_marker_in_row_label_is_not_read_as_a_value_cell(self):
        # A row like "매출액이익률(주1) | 15.38% | 8.23%" has a digit only inside
        # the footnote marker "(주1)". Before the fix, that digit made the
        # label cell itself look like a value cell — losing the row label and
        # turning the footnote number "1" into a bogus Fact value.
        parsed = parse_structured_evidence(
            "[한화에어로스페이스 | 사업보고서 (2024.12)]\n"
            "| 주요가정치 | Hanwha AeroEngines Co., Ltd. | Hanwha AerospaceUSA Co., Ltd. |\n"
            "| --- | --- | --- |\n"
            "| 매출액이익률(주1) | 15.38% | 8.23% |\n"
            "| 매출성장률(주2) | 20.48% | 3.87% |"
        )
        row_labels = {cell["row_label"] for cell in parsed.numeric_cells}
        self.assertEqual(row_labels, {"매출액이익률(주1)", "매출성장률(주2)"})
        values = {cell["value"] for cell in parsed.numeric_cells}
        self.assertEqual(values, {"15.38%", "8.23%", "20.48%", "3.87%"})
        self.assertNotIn("1", values)
        self.assertNotIn("2", values)

    def test_single_header_row_chunk_is_not_read_as_a_data_row(self):
        # DART's row-wise table chunking can isolate a header row into its
        # own chunk with no data rows alongside it. "2024년" etc. contain
        # digits, so before the fix this lone header row was misclassified
        # as a data row and its own year labels became bogus values.
        parsed = parse_structured_evidence(
            "[한화에어로스페이스 | 사업보고서 (2024.12)]\n"
            "| 구 분 | 2024년 | 2023년 | 2022년 |"
        )
        self.assertEqual(parsed.numeric_cells, [])


if __name__ == "__main__":
    unittest.main()
