from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any


class ResponseCache:
    def __init__(self, max_size: int = 256) -> None:
        self.max_size = max(1, max_size)
        self._values: OrderedDict[str, Any] = OrderedDict()

    @staticmethod
    def key(messages: list[dict[str, Any]], profile: Any, schema: dict[str, Any] | None = None) -> str:
        payload = {"messages": messages, "profile": getattr(profile, "__dict__", str(profile)), "schema": schema}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        value = self._values.get(key)
        if value is not None:
            self._values.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        self._values[key] = value
        self._values.move_to_end(key)
        while len(self._values) > self.max_size:
            self._values.popitem(last=False)
