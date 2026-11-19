"""单行识别封装（S1-3）。

只做一件事：把一张**裁剪图**识别成文本。S3-3 的"低置信字段局部二次识别"直接调它，
所以这里必须比整图识别更宽松：空裁剪、单通道、异常尺寸都不能抛。

模型实例复用 `detector.OcrEngine` 的单例，不新建第二份（再建一份要多花 5–10s 和一份显存/内存）。
"""
from __future__ import annotations

import numpy as np

from app.ocr.detector import OcrEngine, get_engine

__all__ = ["recognize_line", "recognize_lines"]


def recognize_line(crop: np.ndarray, engine: OcrEngine | None = None) -> tuple[str, float]:
    """识别单行裁剪图，返回 (text, score)；任何异常都降级为 ("", 0.0) 并保留日志。"""
    try:
        return (engine or get_engine()).recognize_line(crop)
    except Exception:  # noqa: BLE001 - 二次识别失败不应中断主流程
        import logging

        logging.getLogger(__name__).warning("单行识别失败，按空结果处理", exc_info=True)
        return "", 0.0


def recognize_lines(crops: list[np.ndarray], engine: OcrEngine | None = None) -> list[tuple[str, float]]:
    """批量识别多张裁剪图（逐个调用，保持与单行完全一致的语义）。"""
    shared = engine or get_engine()
    return [recognize_line(crop, shared) for crop in crops]
