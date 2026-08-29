import argparse
import os
from pathlib import Path
import unicodedata

def normalize_corpus_names(corpus_dir: str | Path | None = None) -> int:
    """Normalize corpus file and directory names to NFC and return the count."""

    raw_path = str(corpus_dir or os.getenv("CORPUS_DIR", "")).strip()
    if not raw_path:
        raise ValueError("Pass --corpus-dir or set CORPUS_DIR before normalizing a corpus.")
    target_dir = Path(raw_path).expanduser().resolve()
    if not target_dir.is_dir():
        raise FileNotFoundError(f"Corpus directory does not exist: {target_dir}")

    renamed_count = 0
    # 깊은 하위 폴더/파일부터 정규화 처리 (topdown=False 필수)
    for root, dirs, files in os.walk(target_dir, topdown=False):
        # 1. 파일 이름 NFC 정규화
        for name in files:
            nfc_name = unicodedata.normalize("NFC", name)
            if nfc_name != name:
                old_path = os.path.join(root, name)
                new_path = os.path.join(root, nfc_name)
                os.rename(old_path, new_path)
                renamed_count += 1

        # 2. 폴더 이름 NFC 정규화
        for name in dirs:
            nfc_name = unicodedata.normalize("NFC", name)
            if nfc_name != name:
                old_path = os.path.join(root, name)
                new_path = os.path.join(root, nfc_name)
                os.rename(old_path, new_path)
                renamed_count += 1
                print(f"[NFC 복원] {name} -> {nfc_name}")
    return renamed_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART corpus 파일명·폴더명을 NFC로 정규화")
    parser.add_argument("--corpus-dir", default=None, help="정규화할 corpus 경로. 생략하면 CORPUS_DIR 사용")
    args = parser.parse_args()
    print(f"[NFC 정규화] renamed={normalize_corpus_names(args.corpus_dir)}")
