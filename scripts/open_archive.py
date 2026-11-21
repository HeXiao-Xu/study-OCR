"""用口令解开 S5-6 归档包（`*.zip.enc`）。

归档包 = 自描述 AES-GCM 容器，内含标准 ZIP；本脚本负责解密并写出 ZIP（可选解压）。
口令从环境变量 `STUDYOCR_ARCHIVE_PASSPHRASE` 读，交互式则 getpass——**不要放命令行参数**。

用法：
    python scripts/open_archive.py backend/eval/results/RPT-202611-0001-archive.zip.enc \
        --out /tmp/archive.zip [--extract /tmp/archive]
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.crypto.archive_crypto import ArchiveOpenError, open_archive  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="解开 Study-OCR 口令加密归档包")
    parser.add_argument("archive", type=Path, help="*.zip.enc 文件")
    parser.add_argument("--out", type=Path, default=None, help="输出 ZIP 路径")
    parser.add_argument("--extract", type=Path, default=None, help="解压目录")
    parser.add_argument("--list", action="store_true", help="只列出 ZIP 成员")
    args = parser.parse_args()

    passphrase = os.environ.get("STUDYOCR_ARCHIVE_PASSPHRASE")
    if not passphrase:
        passphrase = getpass.getpass("归档包口令: ")

    try:
        plaintext = open_archive(args.archive.read_bytes(), passphrase)
    except ArchiveOpenError as exc:
        print(f"打开失败：{exc}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(__import__("io").BytesIO(plaintext)) as zf:
        names = zf.namelist()
        if args.list:
            for name in names:
                print(f"{zf.getinfo(name).file_size:>10}  {name}")
            return 0
        out = args.out or args.archive.with_suffix("")
        out.write_bytes(plaintext)
        print(f"已解密：{out}")
        if args.extract is not None:
            zf.extractall(args.extract)
            print(f"已解压到：{args.extract}（{len(names)} 个成员）")
        else:
            for name in names:
                print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())