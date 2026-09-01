"""Run offline deployment preflight checks for the active composition."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.composition import build_pipeline


def main() -> int:
    try:
        build_pipeline()
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"NOT READY: {error}")
        return 1
    print("READY: offline deployment checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
