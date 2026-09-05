from stage3.grounding import period_matches


def test_period_matches_korean_quarter_label_to_normalized_month() -> None:
    assert period_matches("2025년 3분기", "2025-09")
    assert period_matches("2025년 3분기(제58기)", "2025-09")


def test_period_matches_korean_year_label_to_year() -> None:
    assert period_matches("2024년", "2024")
    assert period_matches("2024년", "2024-12")
