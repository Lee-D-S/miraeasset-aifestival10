from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ValidatorResult:
    status: str
    answer: str
    numeric_check: dict[str, Any] = field(default_factory=dict)
    citation_check: dict[str, Any] = field(default_factory=dict)
    semantic_check: dict[str, Any] = field(default_factory=dict)
    regenerated: bool = False
    warnings: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    provider_status: dict[str, Any] = field(default_factory=dict)
    failure_reason_code: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidatorResult":
        return cls(
            status=str(value.get("status", "error")),
            answer=str(value.get("answer", "")),
            numeric_check=dict(value.get("numeric_check", {})),
            citation_check=dict(value.get("citation_check", {})),
            semantic_check=dict(value.get("semantic_check", {})),
            regenerated=bool(value.get("regenerated", False)),
            warnings=[str(item) for item in value.get("warnings", [])],
            trace=[str(item) for item in value.get("trace", [])],
            provider_status=dict(value.get("provider_status", {})),
            failure_reason_code=(
                str(value["failure_reason_code"])
                if value.get("failure_reason_code") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "answer": self.answer,
            "numeric_check": dict(self.numeric_check),
            "citation_check": dict(self.citation_check),
            "semantic_check": dict(self.semantic_check),
            "regenerated": self.regenerated,
            "warnings": list(self.warnings),
            "trace": list(self.trace),
            "provider_status": dict(self.provider_status),
            "failure_reason_code": self.failure_reason_code,
        }


__all__ = ["ValidatorResult"]
