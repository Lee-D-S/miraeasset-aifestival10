from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from integration.cache import canonical_json, safe_cache_get, safe_cache_put, sha256_text


_CELL_TAGS = {"td", "th", "tu"}
_UNIT_RE = re.compile(r"단위\s*[:：]\s*([^()\n]+)")
_PERIOD_RE = re.compile(r"20\d{2}\s*년(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일?)?|\s*(?:[1-4]\s*분기|상반기|하반기|연간))?")
_NUMBER_RE = re.compile(r"(?:△|▲|\-)?\s*\d[\d,]*(?:\.\d+)?")
_CURRENCY_UNITS = ("조원", "십억원", "억원", "백만원", "천만원", "만원", "천원", "원")
STRUCTURED_PARSER_VERSION = "structured-parser-v2"


@dataclass(frozen=True)
class StructuredTable:
    """A table flattened into a grid while retaining disclosure context."""

    table_id: str
    rows: list[list[str]]
    unit_label: str | None = None
    basis_label: str | None = None
    period_label: str | None = None
    source_format: str = "text"

    def row_text(self, row: list[str]) -> str:
        return " | ".join(value for value in row if value.strip())

    @property
    def rendered_text(self) -> str:
        return "\n".join(self.row_text(row) for row in self.rows if self.row_text(row))

    def numeric_cells(self) -> list[dict[str, Any]]:
        """Return numeric cells with row/column/unit/basis context.

        This is intentionally structural rather than semantic: metric names
        are resolved by the Stage3 metric registry in the extraction layer.
        """

        if not self.rows:
            return []
        column_labels = _column_labels(self.rows)
        header_index = _column_header_index(self.rows)
        table_scope = " ".join(self.row_text(row) for row in self.rows[:2])
        period_numbers = [
            int(match.group(1))
            for label in column_labels
            if (match := re.search(r"제\s*(\d+)\s*기", label))
        ]
        current_period_number = max(period_numbers) if period_numbers else None
        results: list[dict[str, Any]] = []
        for row_index, row in enumerate(self.rows):
            if row_index == header_index:
                continue
            row_label = _row_label(row)
            for column_index, value in enumerate(row):
                number_match = _NUMBER_RE.search(value)
                if not number_match:
                    continue
                column_label = column_labels[column_index] if column_index < len(column_labels) else ""
                if re.sub(r"\s+", "", column_label) in {"주석", "주"}:
                    continue
                period_offset = None
                compact_column_label = re.sub(r"\s+", "", column_label)
                if "당" in compact_column_label:
                    period_offset = 0
                elif "전전" in compact_column_label:
                    period_offset = -2
                elif "전" in compact_column_label:
                    period_offset = -1
                elif current_period_number is not None:
                    period_match = re.search(r"제\s*(\d+)\s*기", column_label)
                    if period_match:
                        period_offset = int(period_match.group(1)) - current_period_number
                unit = _unit_for_column(self.unit_label, column_label, row_label, value)
                currency = "USD" if re.search(r"\bUSD\b|\$", value) else ("KRW" if unit in _CURRENCY_UNITS else None)
                basis = (
                    "연결"
                    if "연결" in row_label
                    else "별도"
                    if "별도" in row_label
                    else self.basis_label
                )
                results.append(
                    {
                        "table_id": self.table_id,
                        "row_index": row_index,
                        "column_index": column_index,
                        "row_label": row_label,
                        "column_label": column_label,
                        "value": value,
                        "unit": unit,
                        "currency": currency,
                        "unit_label": self.unit_label,
                        "basis_label": basis,
                        "basis": basis,
                        "period_label": self.period_label,
                        "table_scope": table_scope,
                        "period_offset": period_offset,
                        "source_format": self.source_format,
                    }
                )
        return results


@dataclass(frozen=True)
class StructuredEvidence:
    """Visible evidence plus parsed tables and parser warnings."""

    text: str
    source_format: str
    tables: list[StructuredTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_structured_tables(self) -> bool:
        return bool(self.tables)

    @property
    def numeric_cells(self) -> list[dict[str, Any]]:
        return [cell for table in self.tables for cell in table.numeric_cells()]


@dataclass
class _CellToken:
    text: str
    colspan: int = 1
    rowspan: int = 1


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _attr(node: Any, name: str) -> str | None:
    attributes = getattr(node, "attrib", {})
    for key, value in attributes.items():
        if _local_name(key) == name.lower():
            return str(value)
    return None


def _span_count(value: str | None) -> int:
    try:
        return max(1, int(value or "1"))
    except (TypeError, ValueError):
        return 1


def _node_text(node: ET.Element) -> str:
    return _clean_text(" ".join(node.itertext()))


def _direct_cells(row: ET.Element) -> list[ET.Element]:
    cells: list[ET.Element] = []

    def visit(node: ET.Element) -> None:
        for child in list(node):
            name = _local_name(child.tag)
            if name in _CELL_TAGS:
                cells.append(child)
            elif name != "tr":
                visit(child)

    visit(row)
    return cells


def _xml_rows(table: ET.Element) -> list[list[_CellToken]]:
    rows: list[list[_CellToken]] = []
    for row in table.iter():
        if _local_name(row.tag) != "tr":
            continue
        tokens = []
        for cell in _direct_cells(row):
            tokens.append(_CellToken(_node_text(cell), _span_count(_attr(cell, "colspan")), _span_count(_attr(cell, "rowspan"))))
        if tokens:
            rows.append(tokens)
    return rows


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self.tables: list[list[list[_CellToken]]] = []
        self._table_rows: list[list[_CellToken]] | None = None
        self._row: list[_CellToken] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_colspan = 1
        self._cell_rowspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        attr_map = {key.lower(): value for key, value in attrs}
        if name == "table":
            self._table_rows = []
        elif name == "tr" and self._table_rows is not None:
            self._row = []
        elif name in _CELL_TAGS and self._row is not None:
            self._cell_parts = []
            self._cell_colspan = _span_count(attr_map.get("colspan"))
            self._cell_rowspan = _span_count(attr_map.get("rowspan"))

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _CELL_TAGS and self._cell_parts is not None and self._row is not None:
            self._row.append(_CellToken(_clean_text(" ".join(self._cell_parts)), self._cell_colspan, self._cell_rowspan))
            self._cell_parts = None
        elif name == "tr" and self._row is not None and self._table_rows is not None:
            if self._row:
                self._table_rows.append(self._row)
            self._row = None
        elif name == "table" and self._table_rows is not None:
            if self._table_rows:
                self.tables.append(self._table_rows)
            self._table_rows = None

    def handle_data(self, data: str) -> None:
        cleaned = _clean_text(data)
        if not cleaned:
            return
        self.visible_parts.append(cleaned)
        if self._cell_parts is not None:
            self._cell_parts.append(cleaned)


def _expand_rows(rows: Iterable[Iterable[_CellToken]]) -> list[list[str]]:
    occupied: dict[tuple[int, int], str] = {}
    expanded: list[list[str]] = []
    for row_index, tokens in enumerate(rows):
        row_values: dict[int, str] = {}
        column_index = 0
        for token in tokens:
            while (row_index, column_index) in occupied:
                row_values[column_index] = occupied[(row_index, column_index)]
                column_index += 1
            for offset in range(token.colspan):
                current_column = column_index + offset
                row_values[current_column] = token.text
                for future_row in range(row_index + 1, row_index + token.rowspan):
                    occupied[(future_row, current_column)] = token.text
            column_index += token.colspan
        while (row_index, column_index) in occupied:
            row_values[column_index] = occupied[(row_index, column_index)]
            column_index += 1
        if row_values:
            max_column = max(row_values)
            expanded.append([row_values.get(index, "") for index in range(max_column + 1)])
    return expanded


def _table_metadata(rows: list[list[str]]) -> tuple[str | None, str | None, str | None]:
    all_text = "\n".join(" | ".join(row) for row in rows)
    unit_label = None
    for row in rows:
        row_text = " | ".join(row)
        unit_match = _UNIT_RE.search(row_text)
        # A unit embedded in a metric row (for example basic EPS ``(단위:
        # 원)``) applies only to that row, not to the complete table.
        if unit_match and not any(_NUMBER_RE.search(value) for value in row):
            unit_label = _clean_text(unit_match.group(1))
            break
    basis_label = next((value for row in rows for value in row if "연결" in value or "별도" in value), None)
    period_match = _PERIOD_RE.search(all_text)
    period_label = _clean_text(period_match.group(0)) if period_match else None
    return unit_label, basis_label, period_label


def _make_tables(raw_tables: Iterable[Iterable[Iterable[_CellToken]]], source_format: str) -> list[StructuredTable]:
    tables: list[StructuredTable] = []
    for index, raw_rows in enumerate(raw_tables, start=1):
        rows = _expand_rows(raw_rows)
        if not rows:
            continue
        unit_label, basis_label, period_label = _table_metadata(rows)
        tables.append(StructuredTable(f"table-{index:03d}", rows, unit_label, basis_label, period_label, source_format))
    return tables


def _column_labels(rows: list[list[str]]) -> list[str]:
    header_index = _column_header_index(rows)
    if header_index is not None:
        row = rows[header_index]
        max_columns = max(len(item) for item in rows)
        labels = list(row)
        # DART-to-markdown conversion sometimes inserts an empty cell
        # before each period value. Expand the two period labels over
        # those value columns so current/prior facts remain distinguishable.
        if len(labels) == 4 and max_columns >= 6 and re.sub(r"\s+", "", labels[1]) in {"주석", "주"}:
            labels = [labels[0], labels[1], labels[2], labels[2], labels[3], labels[3]]
        # Some DART tables use a second header row for amount/ratio columns:
        # ``2025년 3분기 | 2025년 3분기`` followed by ``금액 | 비중``.
        # Keep both pieces so unit detection can exclude percentage cells
        # while period detection still sees the fiscal column.
        if header_index + 1 < len(rows):
            sub_labels = rows[header_index + 1]
            if any(value.strip() in {"금액", "비중", "수량", "단가"} for value in sub_labels):
                labels = [
                    f"{base} {sub}".strip() if sub.strip() else base
                    for base, sub in zip(
                        labels,
                        sub_labels + [""] * (len(labels) - len(sub_labels)),
                    )
                ]
        return labels + [""] * (max_columns - len(labels))
    return rows[0] if rows else []


def _column_header_index(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows):
        nonnumeric = [value for value in row if value and not _NUMBER_RE.search(value)]
        if len(nonnumeric) >= 2 and not any("단위" in value for value in nonnumeric):
            return index
    return None


def _row_label(row: list[str]) -> str:
    # DART tables commonly use multiple leading text cells as a hierarchical
    # row header, for example ``차량부문 | 매출액 | 109,041,330``.  Preserve
    # all leading labels so Fact extraction can still see the metric label.
    labels: list[str] = []
    for value in row:
        if not value.strip():
            continue
        if _NUMBER_RE.search(value):
            break
        labels.append(value.strip())
    return " ".join(labels)


def _unit_for_column(unit_label: str | None, column_label: str, row_label: str = "", value: str = "") -> str:
    if re.search(r"\bUSD\b|\$", value):
        return "USD"
    if not unit_label:
        if "원" in row_label or "원" in column_label:
            return "원"
        if (
            "%" in row_label
            or "%" in column_label
            or "%" in value
            or "비중" in row_label
            or "비중" in column_label
            or "비율" in row_label
            or "비율" in column_label
        ):
            return "%"
        return ""
    if "%" in column_label or "비중" in column_label or "율" in column_label:
        return "%"
    for unit in _CURRENCY_UNITS:
        if unit in unit_label:
            return unit
    return ""


def _markdown_rows(value: str) -> list[list[_CellToken]]:
    rows: list[list[_CellToken]] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [_clean_text(cell) for cell in stripped[1:-1].split("|")]
        if len(cells) < 2:
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        rows.append([_CellToken(cell) for cell in cells])
    return rows


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<\s*html(?:\s|>)", value, re.IGNORECASE))


def parse_structured_evidence(
    value: str,
    *,
    cache: Any | None = None,
    document_id: str = "",
) -> StructuredEvidence:
    """Parse DART XML or HTML table evidence without third-party packages."""

    raw = str(value or "")
    cache_key = "structured-document:" + canonical_json(
        {
            "document_id": str(document_id),
            "text_sha256": sha256_text(raw),
            "parser_version": STRUCTURED_PARSER_VERSION,
        }
    )
    cached = safe_cache_get(
        getattr(cache, "structured_documents", None),
        cache_key,
    )
    if cached is not None:
        return cached

    if not raw.lstrip().startswith("<"):
        markdown_rows = _markdown_rows(raw)
        if markdown_rows:
            result = StructuredEvidence(raw, "markdown", _make_tables((markdown_rows,), "markdown"))
        else:
            result = StructuredEvidence(raw, "text")
        safe_cache_put(
            getattr(cache, "structured_documents", None),
            cache_key,
            result,
        )
        return result

    if _looks_like_html(raw):
        parser = _HTMLTableParser()
        try:
            parser.feed(raw)
            parser.close()
        except Exception as error:  # noqa: BLE001 - malformed upstream markup
            result = StructuredEvidence(" ".join(parser.visible_parts), "html", warnings=[f"html_parse_error: {error}"])
        else:
            tables = _make_tables(parser.tables, "html")
            result = StructuredEvidence(" ".join(parser.visible_parts), "html", tables)
        safe_cache_put(
            getattr(cache, "structured_documents", None),
            cache_key,
            result,
        )
        return result

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        result = StructuredEvidence(re.sub(r"<[^>]+>", " ", raw), "xml", warnings=[f"xml_parse_error: {error}"])
    else:
        raw_tables = [_xml_rows(table) for table in root.iter() if _local_name(table.tag) == "table"]
        tables = _make_tables(raw_tables, "xml")
        visible_text = _clean_text(" ".join(root.itertext()))
        result = StructuredEvidence(visible_text, "xml", tables)
    safe_cache_put(
        getattr(cache, "structured_documents", None),
        cache_key,
        result,
    )
    return result


__all__ = [
    "STRUCTURED_PARSER_VERSION",
    "StructuredEvidence",
    "StructuredTable",
    "parse_structured_evidence",
]
