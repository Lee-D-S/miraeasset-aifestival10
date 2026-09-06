"""① 전처리 — 원문은 보존하고, 매칭용 표현만 정규화한다."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..index.corpus_index import squash_with_map

_WS = re.compile(r"\s+")
_TILDE = re.compile(r"[〜～∼]")
_QUOTES = re.compile(r"[“”„‟]")
_APOST = re.compile(r"[‘’‚‛]")


@dataclass
class PreprocessResult:
    raw: str
    text: str
    squashed: str
    positions: list[int]

    def original_slice(self, start: int, end: int) -> str:
        """squashed 좌표 구간을 원문(text) 구간으로 되돌린다."""
        if not self.positions or start >= end:
            return ""
        first = self.positions[start]
        last = self.positions[min(end, len(self.positions)) - 1]
        return self.text[first : last + 1]


def preprocess(question: str) -> PreprocessResult:
    text = unicodedata.normalize("NFKC", question or "")
    text = _TILDE.sub("~", text)
    text = _QUOTES.sub('"', text)
    text = _APOST.sub("'", text)
    text = _WS.sub(" ", text).strip()

    squashed, positions = squash_with_map(text)
    return PreprocessResult(raw=question or "", text=text, squashed=squashed, positions=positions)
