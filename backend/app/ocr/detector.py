"""文本检测识别引擎（S1-3）。

封装 PaddleOCR 2.7.3（PP-OCRv4：DBNet 检测 + SVTR_LCNet 识别，CPU）。

三条设计约束：
1. **版本锁死**：2.7.3 的返回是 `[[[四点坐标, (text, score)], ...]]`，3.x 改成 `predict()` 返回
   `rec_texts/rec_scores/dt_polys` 的 dict。结构一对不上就抛 `OcrContractError`，
   而不是让上层拿到空列表还以为"这图上没字"。
2. **进程内单例**：初始化要 5–10s 加载模型，且 PaddleOCR **非线程安全**，所以懒加载 + 互斥锁，
   一次进程只建一个。
3. **引擎层不丢行**：`drop_score=0`，低分行也保留。丢不丢是业务决策（S3-2 的低置信队列要用），
   不是引擎决策——引擎偷偷丢行会让"识别率"虚高、低置信字段无从标记。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import numpy as np

from app.ocr.types import OcrLine, OcrResult

__all__ = ["OcrOptions", "OcrContractError", "OcrEngine", "get_engine"]


class OcrContractError(RuntimeError):
    """PaddleOCR 返回结构与契约不符（多半是版本被升级了）。"""


@dataclass(frozen=True)
class OcrOptions:
    """引擎参数。默认值对齐《S1-3 spec》，全部可由配置覆盖（S6 做对比实验用）。"""

    lang: str = "ch"
    use_angle_cls: bool = True
    det_db_thresh: float = 0.3
    det_db_box_thresh: float = 0.3
    det_db_unclip_ratio: float = 1.6          # 票据文本密集，>1.7 相邻行会粘连
    det_limit_side_len: int = 1600
    drop_score: float = 0.0                   # 引擎层不丢行
    device: str = "cpu"
    model_root: str = ""                      # 空 = 默认 ~/.paddleocr
    show_log: bool = False
    rec_batch_num: int = 6
    extra: dict = field(default_factory=dict)

    def as_det_params(self) -> dict:
        return {
            "lang": self.lang,
            "use_angle_cls": self.use_angle_cls,
            "det_db_thresh": self.det_db_thresh,
            "det_db_box_thresh": self.det_db_box_thresh,
            "det_db_unclip_ratio": self.det_db_unclip_ratio,
            "det_limit_side_len": self.det_limit_side_len,
            "drop_score": self.drop_score,
            "device": self.device,
        }

    @classmethod
    def from_settings(cls, settings) -> "OcrOptions":  # noqa: ANN001 - 避免循环导入
        return cls(
            lang=getattr(settings, "ocr_lang", "ch"),
            use_angle_cls=getattr(settings, "ocr_use_angle_cls", True),
            det_db_thresh=getattr(settings, "ocr_det_db_thresh", 0.3),
            det_db_box_thresh=getattr(settings, "ocr_det_db_box_thresh", 0.3),
            det_db_unclip_ratio=getattr(settings, "ocr_det_db_unclip_ratio", 1.6),
            det_limit_side_len=getattr(settings, "ocr_det_limit_side_len", 1600),
            device=getattr(settings, "ocr_device", "cpu"),
            model_root=getattr(settings, "ocr_model_root", ""),
            show_log=getattr(settings, "ocr_show_log", False),
        )


def _order_bucket(height: float) -> float:
    """行分桶高度：行高的一半（至少 8px）。同一桶内的框视为"同一行"。"""
    return max(8.0, height * 0.5)


def parse_paddle_result(raw: object, image_size: tuple[int, int],
                        det_params: dict | None = None,
                        engine_version: str = "2.7.3") -> OcrResult:
    """把 PaddleOCR 2.7 的原始返回解析成契约结构。

    2.7 的形态：`[[ [box, (text, score)], ... ]]`（外层按图，内层按行），空图可能是 `[None]`。
    3.x 的形态是 dict（`rec_texts` 等）——那种情况**必须报错**，绝不能静默返回空。
    """
    if raw is None:
        return OcrResult(lines=[], image_size=image_size, engine_version=engine_version,
                         det_params=det_params or {})
    is_v3 = isinstance(raw, dict) or (isinstance(raw, list) and raw and isinstance(raw[0], dict))
    if is_v3:
        raise OcrContractError(
            "PaddleOCR 返回的是 3.x 的 dict 结构（rec_texts/rec_scores/dt_polys），"
            "本项目锁定 2.7.3 的 `ocr.ocr(img, cls=True)`。请检查 paddleocr 版本是否被升级。"
        )

    pages = raw if isinstance(raw, list) and raw and isinstance(raw[0], list) else [raw]
    page = pages[0] if pages else []
    if not page:
        return OcrResult(lines=[], image_size=image_size, engine_version=engine_version,
                         det_params=det_params or {})

    provisional: list[dict] = []
    for item in page:
        if item is None:
            continue
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise OcrContractError(f"单行结果应为 [box, (text, score)]，实际拿到 {type(item)}")
        box, payload = item
        if not (isinstance(payload, (list, tuple)) and len(payload) == 2):
            raise OcrContractError(f"识别结果应为 (text, score)，实际拿到 {type(payload)}")
        text, score = payload
        points = [[float(p[0]), float(p[1])] for p in box]
        if len(points) != 4:
            raise OcrContractError(f"检测框应为 4 点，实际 {len(points)} 点")
        ys = [p[1] for p in points]
        xs = [p[0] for p in points]
        provisional.append({
            "text": str(text),
            "score": float(score),
            "box": points,
            "y_center": (min(ys) + max(ys)) / 2.0,
            "x_left": min(xs),
            "height": max(ys) - min(ys),
        })

    # 排序：先按 y 分桶（同一行），桶内按 x；保证 line_no 可复现（S1-5 要按行号回查）
    bucket_height = _order_bucket(median([item["height"] for item in provisional])) if provisional else 8.0
    provisional.sort(key=lambda item: (round(item["y_center"] / bucket_height), item["x_left"]))

    lines = [
        OcrLine(line_no=index, text=item["text"], score=item["score"], box=item["box"],
                y_center=item["y_center"], x_left=item["x_left"], height=item["height"])
        for index, item in enumerate(provisional)
    ]
    return OcrResult(lines=lines, image_size=image_size, engine_version=engine_version,
                     det_params=det_params or {})


class OcrEngine:
    """PaddleOCR 的进程内单例封装。"""

    def __init__(self, options: OcrOptions | None = None) -> None:
        self.options = options or OcrOptions()
        self._ocr = None
        self._lock = threading.Lock()
        self._init_seconds = 0.0

    # ---------------- 懒加载 ----------------
    def _ensure(self):
        if self._ocr is not None:
            return self._ocr
        with self._lock:
            if self._ocr is not None:  # 双检
                return self._ocr
            started = time.perf_counter()
            from paddleocr import PaddleOCR  # 延迟导入：不跑 OCR 的测试不必付 5s 导入代价

            kwargs = dict(
                use_angle_cls=self.options.use_angle_cls,
                lang=self.options.lang,
                show_log=self.options.show_log,
                det_db_thresh=self.options.det_db_thresh,
                det_db_box_thresh=self.options.det_db_box_thresh,
                det_db_unclip_ratio=self.options.det_db_unclip_ratio,
                det_limit_side_len=self.options.det_limit_side_len,
                drop_score=self.options.drop_score,
                use_gpu=self.options.device != "cpu",
            )
            if self.options.model_root:
                root = Path(self.options.model_root)
                kwargs["det_model_dir"] = str(root / "det")
                kwargs["rec_model_dir"] = str(root / "rec")
                kwargs["cls_model_dir"] = str(root / "cls")
            self._ocr = PaddleOCR(**kwargs)
            self._init_seconds = time.perf_counter() - started
        return self._ocr

    @property
    def init_seconds(self) -> float:
        return self._init_seconds

    @property
    def version(self) -> str:
        try:
            import paddleocr
            return getattr(paddleocr, "__version__", "2.7.3")
        except Exception:  # noqa: BLE001
            return "unknown"

    # ---------------- 主接口 ----------------
    def recognize(self, image: np.ndarray | str | Path) -> OcrResult:
        """整图检测 + 识别。空结果返回 `lines=[]`，**不抛异常**。"""
        if isinstance(image, (str, Path)):
            import cv2

            array = cv2.imread(str(image))
            if array is None:
                raise FileNotFoundError(f"图像读取失败：{image}")
        else:
            array = image
        if array.size == 0:
            raise ValueError("输入图像为空")

        height, width = array.shape[:2]
        engine = self._ensure()
        started = time.perf_counter()
        raw = engine.ocr(array, cls=self.options.use_angle_cls)
        elapsed_ms = (time.perf_counter() - started) * 1000

        result = parse_paddle_result(raw, (width, height), self.options.as_det_params(), self.version)
        result.elapsed_ms = round(elapsed_ms, 2)
        return result

    def recognize_line(self, crop: np.ndarray) -> tuple[str, float]:
        """只跑识别（不检测），供 S3-3 的"局部二次识别"复用。空裁剪返回 ("", 0.0)。"""
        if crop is None or crop.size == 0:
            return "", 0.0
        if crop.ndim == 2:
            import cv2

            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        engine = self._ensure()
        raw = engine.ocr(crop, det=False, cls=self.options.use_angle_cls)
        if not raw:
            return "", 0.0
        first = raw[0] if isinstance(raw[0], list) else raw
        if isinstance(first, list) and first and isinstance(first[0], (list, tuple)):
            text, score = first[0][0], first[0][1]
            return str(text), float(score)
        if isinstance(first, (list, tuple)) and len(first) == 2 and isinstance(first[0], str):
            return str(first[0]), float(first[1])
        return "", 0.0


_ENGINE: OcrEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine(options: OcrOptions | None = None) -> OcrEngine:
    """进程级单例。Worker 与 API 各自进程内共享同一个模型实例。"""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = OcrEngine(options)
    return _ENGINE


def reset_engine() -> None:
    """测试与配置热切换用。"""
    global _ENGINE
    with _ENGINE_LOCK:
        _ENGINE = None