from __future__ import annotations

import logging

import pytest

from integration.readiness import PARTIAL_INDEX_ISSUES, raise_if_invalid


def test_expected_partial_index_issues_can_be_downgraded(caplog):
    with caplog.at_level(logging.WARNING):
        raise_if_invalid(
            ["Chroma is missing SQLite chunk IDs"],
            tolerate=PARTIAL_INDEX_ISSUES,
        )

    assert "tolerating partial-index issue" in caplog.text


def test_partial_index_issues_fail_without_explicit_tolerance():
    with pytest.raises(RuntimeError, match="Chroma is missing SQLite chunk IDs"):
        raise_if_invalid(["Chroma is missing SQLite chunk IDs"])


def test_structural_issues_are_never_hidden_by_partial_tolerance():
    with pytest.raises(RuntimeError, match="Chroma collection is empty"):
        raise_if_invalid(
            ["Chroma collection is empty", "Chroma is missing SQLite chunk IDs"],
            tolerate=PARTIAL_INDEX_ISSUES,
        )
