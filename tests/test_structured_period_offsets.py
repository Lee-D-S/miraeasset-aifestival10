from stage3.parsing.structured import StructuredTable


def test_numeric_cells_infer_period_offsets_from_grouped_columns() -> None:
    table = StructuredTable(
        table_id="table-001",
        rows=[
            ["구분", "2025년 3분기(제58기)", "2024년(제57기)", "2023년(제56기)"],
            ["", "금액", "비중", "금액", "비중", "금액", "비중"],
            ["차량부문 매출액", "109041330", "78.2", "136725011", "78.1", "130149921", "80.0"],
        ],
        source_format="markdown",
    )

    cells = table.numeric_cells()

    assert [cell["period_offset"] for cell in cells] == [0, 0, -1, -1, -2, -2]


def test_numeric_cells_handle_unaligned_measure_header_and_trailing_empty_cell() -> None:
    table = StructuredTable(
        table_id="table-unaligned",
        rows=[
            ["구분", "2025년 3분기(제58기)", "2024년(제57기)", "2023년(제56기)"],
            ["금액", "비중", "금액", "비중", "금액", "비중"],
            [
                "차량부문",
                "매출액",
                "109041330",
                "78.2",
                "136725011",
                "78.1",
                "130149921",
                "80.0",
                "",
            ],
        ],
        unit_label="백만원, %",
        source_format="markdown",
    )

    cells = table.numeric_cells()

    assert [cell["period_offset"] for cell in cells] == [0, 0, -1, -1, -2, -2]
    assert [cell["unit"] for cell in cells] == ["백만원", "%", "백만원", "%", "백만원", "%"]
