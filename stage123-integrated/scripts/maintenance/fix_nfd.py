import os
from pathlib import Path
import unicodedata

# corpus 최상위 경로
TARGET_DIR = Path(
    "/home/user/contest/miraeasset-firstpenguin/data/3.gongsi/corpus"
)

renamed_cnt = 0

# 깊은 하위 폴더/파일부터 정규화 처리 (topdown=False 필수)
for root, dirs, files in os.walk(TARGET_DIR, topdown=False):
    # 1. 파일 이름 NFC 정규화
    for name in files:
        nfc_name = unicodedata.normalize("NFC", name)
        if nfc_name != name:
            old_path = os.path.join(root, name)
            new_path = os.path.join(root, nfc_name)
            os.rename(old_path, new_path)
            renamed_cnt += 1

    # 2. 폴더 이름 NFC 정규화
    for name in dirs:
        nfc_name = unicodedata.normalize("NFC", name)
        if nfc_name != name:
            old_path = os.path.join(root, name)
            new_path = os.path.join(root, nfc_name)
            os.rename(old_path, new_path)
            print(f"[NFC 복원] {name} -> {nfc_name}")
            renamed_cnt += 1

print("\n==========================================")
print(f"총 {renamed_cnt}개 항목의 한글 자모 분리(NFD)를 정상 복원(NFC)했습니다!")
print("==========================================")
