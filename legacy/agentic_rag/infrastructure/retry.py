from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_call(operation: Callable[[], T], *, attempts: int = 3, base_delay: float = 0.2, sleep: Callable[[float], None] = time.sleep) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:  # external API failures are intentionally retried
            last_error = error
            if attempt + 1 < attempts:
                sleep(base_delay * (2**attempt))
    assert last_error is not None
    raise last_error

