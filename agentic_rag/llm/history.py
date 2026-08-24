from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HistoryPolicy:
    mode: str = "minimal"
    max_messages: int = 8
    max_chars: int = 12000

    def select(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected = messages if self.mode == "full" else messages[-self.max_messages:]
        result: list[dict[str, Any]] = []
        total = 0
        for message in reversed(selected):
            content = str(message.get("content", ""))
            if total + len(content) > self.max_chars:
                break
            result.append(message)
            total += len(content)
        return list(reversed(result))

