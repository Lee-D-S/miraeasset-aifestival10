"""Render integration/graph.py's compiled LangGraph state machine as Mermaid.

Builds the graph with no-op stage callables (structure only, nothing is
executed) and writes the Mermaid flowchart LangGraph produces via
``StateGraph.get_graph().draw_mermaid()``. Re-run this whenever
integration/graph.py's nodes or edges change, instead of hand-writing the
diagram.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration import StageNodes, build_graph


def _noop_stage(_state: Mapping[str, Any]) -> dict[str, Any]:
    return {}


def build_stub_graph():
    """Build the real graph shape with no-op stage callables for rendering only."""

    return build_graph(
        StageNodes(
            stage1=_noop_stage,
            stage2=_noop_stage,
            stage3=_noop_stage,
            stage4=_noop_stage,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("integration/graph.mmd"), help="Mermaid text output path")
    parser.add_argument("--png", type=Path, default=None, help="also render a PNG (needs network access to mermaid.ink)")
    args = parser.parse_args()

    graph = build_stub_graph().get_graph()
    mermaid = graph.draw_mermaid()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(mermaid, encoding="utf-8")
    print(f"wrote {args.output}")

    if args.png is not None:
        try:
            png_bytes = graph.draw_mermaid_png()
        except Exception as error:  # network/service boundary (mermaid.ink)
            print(f"PNG render skipped: {type(error).__name__}: {error}")
        else:
            args.png.parent.mkdir(parents=True, exist_ok=True)
            args.png.write_bytes(png_bytes)
            print(f"wrote {args.png}")


if __name__ == "__main__":
    main()
