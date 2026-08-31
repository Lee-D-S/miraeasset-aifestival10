"""Create Stage1 metadata files matching a selected smoke document set."""

from __future__ import annotations

import argparse
import csv
import json
import unicodedata
from pathlib import Path


def normalize(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--selection", type=Path, default=Path("data/local_smoke/selected_documents.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    documents = selection.get("documents", [])
    selected_ids = {normalize(item.get("doc_id")) for item in documents}
    if not selected_ids:
        raise ValueError("selection must contain doc_id")

    source_manifest = args.corpus / "manifest.jsonl"
    output_manifest = args.output / "manifest.jsonl"
    output_universe = args.output / "universe.csv"
    args.output.mkdir(parents=True, exist_ok=True)

    kept_manifest = []
    with source_manifest.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if normalize(item.get("doc_id")) in selected_ids:
                kept_manifest.append(item)
    if {normalize(item.get("doc_id")) for item in kept_manifest} != selected_ids:
        raise ValueError("selection contains document IDs absent from source manifest")
    with output_manifest.open("w", encoding="utf-8", newline="\n") as handle:
        for item in kept_manifest:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    with (args.corpus / "universe.csv").open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("universe.csv has no header")
        # Keep the complete company master: stage1/config aliases may refer
        # to companies outside the selected document subset.
        rows = list(reader)
        if not rows:
            raise ValueError("selection companies absent from universe.csv")
        with output_universe.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print(f"documents={len(kept_manifest)}")
    print(f"companies={len(rows)} (full universe retained for aliases)")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
