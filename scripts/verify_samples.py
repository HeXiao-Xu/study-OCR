#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成样本集自动验收（S1-1）。

为什么要有这个脚本：在当前开发环境里**无法用眼睛看图**（模型不支持图像输入），
所以"看起来对不对"必须翻译成可判定的断言。五条验收对应 spec §9：

① 渲染一致性：字段框里的"暗像素"显著多于同图随机框（证明框真的压着字）
② 可读性：用系统 OCR 反查合成图，与标注文本比较字符相似度（证明字不是方块）
③ 标注自洽：调 check_annotations.py 的 main()
④ 成像覆盖：合成集的灰度/对比/红偏分布要覆盖真实样本所处的工况
⑤ 划分完整：dev/test 数量、每类覆盖、无重复图

用法：
    python scripts/verify_samples.py
    python scripts/verify_samples.py --ocr-count 10
退出码：0 全过；1 有过不了的项。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANN = ROOT / "backend" / "eval" / "datasets" / "annotations_synth_v1.jsonl"
OCR_SCRIPT = ROOT / "scripts" / "ocr_probe.ps1"

# 真实样本的成像统计（S1-1 实测：1080x1920 手机拍摄）
REAL_SAMPLE = {"gray_mean": 129.0, "gray_std": 64.0, "red_ratio": 33.5}

def norm(text: str) -> str:
    """OCR 友好归一化：全角转半角（NFKC）+ 只保留字母数字与汉字。

    系统 OCR 会把 `¥666.36` 读成 `¥ 666 ． 36`（全角句点、字间插空格），
    只剥半角标点会把它当成 `666．36`，与 `66636` 对不上——这是本脚本最初误判的直接原因。
    """
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if ch.isalnum())


def load_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def image_stats(record: dict) -> tuple[float, float, float]:
    arr = np.asarray(Image.open(ROOT / record["image"]).convert("RGB"), dtype=float)
    gray = arr.mean(axis=2)
    red = (arr[:, :, 0] > 120) & (arr[:, :, 0] - arr[:, :, 1] > 40) & (arr[:, :, 0] - arr[:, :, 2] > 40)
    return float(gray.mean()), float(gray.std()), float(100 * red.mean())


# ---------------------------------------------------------------------------
# ① 渲染一致性
# ---------------------------------------------------------------------------
def _ink_mask(gray: np.ndarray, window: int = 21, delta: float = 22.0) -> np.ndarray:
    """局部自适应"墨迹"掩码：比邻域背景明显更暗的像素算作笔画。

    为什么不用全局阈值：样本里有深色背景、有欠曝、有红偏，
    "绝对暗"在不同图上含义完全不同；用局部背景做参照才等价于"字压在那儿"。
    """
    from PIL import ImageFilter

    blurred = np.asarray(
        Image.fromarray(gray.astype(np.uint8)).filter(ImageFilter.GaussianBlur(window)), dtype=float
    )
    return gray < (blurred - delta)


def check_render_alignment(records: list[dict]) -> tuple[bool, str]:
    inside_ratios: list[float] = []
    outside_ratios: list[float] = []
    rng = np.random.default_rng(7)

    for record in records:
        gray = np.asarray(Image.open(ROOT / record["image"]).convert("L"), dtype=float)
        height, width = gray.shape
        ink = _ink_mask(gray)
        for _name, meta in record["fields"].items():
            x1, y1, x2, y2 = meta["bbox"]
            patch = ink[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
            if patch.size:
                inside_ratios.append(float(patch.mean()))
            bw, bh = max(8, x2 - x1), max(8, y2 - y1)
            # 同尺寸随机框：作为"非文本区域"的对照
            for _ in range(3):
                rx = int(rng.integers(0, max(1, width - bw)))
                ry = int(rng.integers(0, max(1, height - bh)))
                rp = ink[ry:ry + bh, rx:rx + bw]
                if rp.size:
                    outside_ratios.append(float(rp.mean()))

    inside = float(np.mean(inside_ratios))
    outside = float(np.mean(outside_ratios))
    ratio = inside / max(outside, 1e-6)
    ok = inside >= 0.02 and ratio >= 3.0
    detail = f"字段框墨迹率 {inside:.3f} vs 随机框 {outside:.3f}（{ratio:.1f}×），阈值 ≥0.02 且 ≥3×"
    return ok, detail


# ---------------------------------------------------------------------------
# ② 可读性（系统 OCR 反查）
# ---------------------------------------------------------------------------
def ocr_text(image_path: Path) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ocr.json"
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
               "-Path", str(image_path), "-OutFile", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0 or not out.exists():
            return "", (proc.stderr or proc.stdout or "OCR 失败")[:200]
        data = json.loads(out.read_text(encoding="utf-8-sig"))
        if not data.get("ok"):
            return "", str(data.get("error", "OCR 引擎不可用"))
        return "\n".join(line["text"] for line in data.get("lines", [])), ""


def field_hit_rate(record: dict, ocr: str) -> tuple[int, int, list[str]]:
    """字段值命中率：每个字段的归一值要么是 OCR 文本的子串，要么其 bigram 覆盖 ≥70%。

    用"字段值"而不是"整页字符"，因为下游真正需要的是**字段能被读出来**；
    键名、表头、页脚读错不影响抽取任务。命中判据为"值是 OCR 文本子串"或"最长公共子串
    覆盖值本身 ≥80%"——后者吸收 OCR 常见的尾巴丢零/多一位，但仍要求八成字符正确。
    """
    normalised = norm(ocr)
    hits, total, missed = 0, 0, []
    matcher = SequenceMatcher(None, autojunk=False)
    for name, meta in record["fields"].items():
        # 两个候选：归一值（2026-11-18 / 1234.56）与票面原样（2026年11月18日 / ¥1,234.56）。
        # 票面原样才是"图上真正画着的东西"，OCR 当然按它来读；两者任一命中即算读出。
        candidates = [norm(str(meta.get(k, ""))) for k in ("value", "text")]
        candidates = [c for c in dict.fromkeys(candidates) if c]
        if not candidates:
            continue
        total += 1
        best = 0.0
        for candidate in candidates:
            if candidate in normalised:
                best = 1.0
                break
            matcher.set_seqs(candidate, normalised)
            match = matcher.find_longest_match(0, len(candidate), 0, len(normalised))
            best = max(best, match.size / max(1, len(candidate)))
        if best >= 0.8:
            hits += 1
        else:
            missed.append(f"{name}({best:.0%})")
    return hits, total, missed


def check_readability(records: list[dict], ocr_count: int) -> tuple[bool, str]:
    # 对照组按"扰动项数量"挑：没有几何/光度扰动的才是真正的可读性基准；
    # 只用灰度挑会把"转过的图"当基准，测的是 OCR 的抗旋转能力，不是"字有没有画出来"。
    ordered = sorted(records, key=lambda r: (len(r.get("perturbations", [])), -image_stats(r)[0]))
    cleanest = ordered[: ocr_count - ocr_count // 2]
    darkest = sorted(records, key=lambda r: image_stats(r)[0])[: ocr_count // 2]
    hardest = [r for r in darkest if r not in cleanest]
    picked = hardest + cleanest

    lines = []
    summary: dict[str, list[float]] = {"hard": [], "clean": []}
    for record in picked:
        text, error = ocr_text(ROOT / record["image"])
        if error:
            return False, f"OCR 调用失败：{error}"
        hits, total, missed = field_hit_rate(record, text)
        rate = hits / max(1, total)
        group = "hard" if record in hardest else "clean"
        summary[group].append(rate)
        suffix = "" if not missed else " 缺:" + ",".join(missed[:3])
        lines.append(f"{Path(record['image']).name}({group})={rate:.2f}{suffix}")

    clean_avg = float(np.mean(summary["clean"])) if summary["clean"] else 0.0
    hard_avg = float(np.mean(summary["hard"])) if summary["hard"] else 0.0
    ok = clean_avg >= 0.85 and hard_avg >= 0.50
    detail = f"字段命中率 干净组 {clean_avg:.2f}（≥0.85） 困难组 {hard_avg:.2f}（≥0.50）｜ " + " ".join(lines)
    return ok, detail


# ---------------------------------------------------------------------------
# ③ 标注自洽
# ---------------------------------------------------------------------------
def check_annotation_contract(ann_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_annotations.py"), "--file", str(ann_path), "--strict"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    return proc.returncode == 0, tail or (proc.stderr or "")[:200]


# ---------------------------------------------------------------------------
# ④ 成像覆盖
# ---------------------------------------------------------------------------
def check_imaging_coverage(records: list[dict]) -> tuple[bool, str]:
    values = np.array([image_stats(record) for record in records])  # mean, std, red
    mean_p5, mean_p95 = np.percentile(values[:, 0], [5, 95])
    std_p95 = float(np.percentile(values[:, 1], 95))
    red_max = float(values[:, 2].max())
    red_hard_count = int((values[:, 2] >= 15).sum())

    conditions = {
        "真实样本灰度均值落在合成集 P5–P95": mean_p5 <= REAL_SAMPLE["gray_mean"] <= mean_p95,
        "真实样本 σ 落在合成集 P5–P95": float(np.percentile(values[:, 1], 5)) <= REAL_SAMPLE["gray_std"] <= std_p95,
        "至少 3 张红偏 ≥15%（红背景工况被覆盖）": red_hard_count >= 3,
        "红偏最大 ≥25%（接近真实样本 33.5%）": red_max >= 25.0,
    }
    ok = all(conditions.values())
    detail = (
        f"mean {np.percentile(values[:, 0], 50):.0f}[P5={mean_p5:.0f},P95={mean_p95:.0f}] ｜ "
        f"σ P95={std_p95:.0f} ｜ red 最大 {red_max:.1f}%、≥15% 的有 {red_hard_count} 张 ｜ "
        + "；".join(f"{k}={'✓' if v else '✗'}" for k, v in conditions.items())
    )
    return ok, detail


# ---------------------------------------------------------------------------
# ⑤ 划分完整
# ---------------------------------------------------------------------------
def check_split(records: list[dict]) -> tuple[bool, str]:
    dev = [r for r in records if r["split"] == "dev"]
    test = [r for r in records if r["split"] == "test"]
    types = {r["ticket_type"] for r in records}
    dev_types = {r["ticket_type"] for r in dev}
    test_types = {r["ticket_type"] for r in test}
    ok = (
        len(dev) >= 20 and len(test) >= 12
        and dev_types == types and test_types == types
    )
    detail = f"dev={len(dev)}（{len(dev_types)} 类） test={len(test)}（{len(test_types)} 类） 共 {len(records)} 张"
    return ok, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="合成样本集自动验收（S1-1）")
    parser.add_argument("--file", type=Path, default=DEFAULT_ANN)
    parser.add_argument("--ocr-count", type=int, default=8, help="OCR 抽查张数（越难与最干净各一半）")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if not args.file.exists():
        print(f"[FAIL] 标注文件不存在：{args.file}，先运行 scripts/make_samples.py")
        return 1

    records = load_records(args.file)
    print("=" * 78)
    print(f" S1-1 合成样本集验收：{len(records)} 张 ｜ {args.file.relative_to(ROOT)}")
    print("=" * 78)

    checks = [
        ("① 渲染一致性", lambda: check_render_alignment(records)),
        ("② OCR 可读性", lambda: check_readability(records, args.ocr_count)),
        ("③ 标注自洽", lambda: check_annotation_contract(args.file)),
        ("④ 成像覆盖", lambda: check_imaging_coverage(records)),
        ("⑤ 划分完整", lambda: check_split(records)),
    ]

    failed = 0
    for name, runner in checks:
        try:
            ok, detail = runner()
        except Exception as exc:  # noqa: BLE001 - 验收脚本自身也不许吐 traceback
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:160]}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        failed += 0 if ok else 1

    print("-" * 78)
    if failed:
        print(f"结论：{failed} 项未通过。修生成器/模板，不要改验收阈值来凑绿。")
        return 1
    print("结论：5 项全部通过，合成集可用于 S1-2 起的开发与 S6 的实验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())