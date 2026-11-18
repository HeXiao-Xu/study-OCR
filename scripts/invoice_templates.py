#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""五种票面版式（S1-1）。

4 类票种 + 1 种形态：
- `vat_general`：增值税电子普通发票（横版 A4 比例）
- `vat_general` + `form_factor=portrait_screenshot`：全电发票的手机竖屏形态（对齐真实样本的 9:16）
- `train_ticket`：铁路电子客票（竖条）
- `flight_itinerary`：航空运输电子客票行程单（横版）
- `taxi_ticket`：出租车定额票（小票，测小目标）

字段名与 `invoice_fields.field_name` 完全一致；每处字段都是**独立绘制**的（键与值分开），
这样标注框精确压在值上，S1-5 的抽取器可以直接按框回查。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal

from invoice_layout import Canvas, Drawn
from sample_data import (
    amount_to_chinese_upper,
    date_iso,
    date_text,
    gen_amounts,
    gen_check_code,
    gen_company,
    gen_digital_invoice_no,
    gen_date,
    gen_flight_no,
    gen_id_card,
    gen_invoice_code,
    gen_invoice_no,
    gen_person,
    gen_plate,
    gen_taxi_company,
    gen_train_no,
    gen_uscc,
    money_text,
)


@dataclass
class RenderedTicket:
    canvas: Canvas
    ticket_type: str
    form_factor: str
    fields: dict[str, dict] = field(default_factory=dict)

    @property
    def image(self):
        return self.canvas.image


def _register(canvas: Canvas, pairs: list[tuple[str, Drawn, str]]) -> dict[str, dict]:
    """把 (字段名, 已绘制对象, 归一值) 变成 fields 映射，box_index 指向 canvas.drawn 下标。"""
    index = {id(item): i for i, item in enumerate(canvas.drawn)}
    out: dict[str, dict] = {}
    for name, drawn, value in pairs:
        out[name] = {"text": drawn.text, "value": value, "box_index": index[id(drawn)]}
    return out


_GOODS = ["办公用品", "打印纸 A4", "硒鼓", "差旅服务费", "会议资料印制", "清洁用品"]
_SPECS = ["标准", "70g/500 张", "黑色", "—", "16 开", "500ml"]


def _kv(canvas: Canvas, x: float, y: float, key: str, value: str, *, key_size: int = 20,
        value_size: int = 20, label: str = "", bold_value: bool = False, latin_value: bool = False):
    """画"键 + 值"，返回值的 Drawn（标注只框值）。

    latin_value=True 时值用拉丁字体绘制——金额、代码、号码这类纯数字串在票据上通常是
    等宽无衬线体，且系统 OCR 对拉丁数字比中文全角数字稳得多。
    """
    key_item = canvas.text((x, y), key, key_size, label="label")
    offset = key_item.bbox[2] - key_item.bbox[0] + 6
    return canvas.text((key_item.bbox[0] + offset, y), value, value_size, label=label or "value",
                       bold=bold_value, latin=latin_value)


# ---------------------------------------------------------------------------
# 1) 增值税电子普通发票（横版）
# ---------------------------------------------------------------------------
def render_vat_general(rng: random.Random, *, portrait: bool = False) -> RenderedTicket:
    width, height = (1080, 1920) if portrait else (1200, 800)
    canvas = Canvas(width, height)

    amounts = gen_amounts(rng)
    invoice_code = gen_invoice_code(rng)
    invoice_no = gen_invoice_no(rng)
    day = gen_date(rng)
    check_code = gen_check_code(rng)
    buyer = gen_company(rng)
    buyer_tax = gen_uscc(rng)
    seller = gen_company(rng)
    seller_tax = gen_uscc(rng)
    pairs: list[tuple[str, Drawn, str]] = []

    if portrait:
        # 手机竖屏：顶部状态栏 + 票面主体（对齐真实样本形态）
        canvas.rect((0, 0, width, 70), fill=(240, 240, 240), outline=(200, 200, 200))
        canvas.text((40, 24), "9:41", 26, label="status_bar")
        canvas.text((width - 220, 24), "中国税务 5G", 22, label="status_bar")
        margin, top = 48, 150
    else:
        margin, top = 40, 0

    canvas.border(margin=14)
    canvas.text((width / 2, top + 30), "增值税电子普通发票", 34 if not portrait else 40,
                label="title", bold=True, anchor="mm")
    canvas.qrcode_placeholder((width - margin - 150, top + 20, width - margin - 20, top + 150))

    row1 = top + (150 if portrait else 200)
    x = margin + 20
    pairs.append(("invoice_code", _kv(canvas, x, row1, "发票代码：", invoice_code, value_size=22,
                                      label="invoice_code", latin_value=True), invoice_code))
    pairs.append(("invoice_no", _kv(canvas, x + 330, row1, "发票号码：", invoice_no, value_size=22,
                                    label="invoice_no", latin_value=True), invoice_no))
    y2 = row1 + 44
    pairs.append(("invoice_date", _kv(canvas, x, y2, "开票日期：", date_text(day), label="invoice_date"), date_iso(day)))

    canvas.line((margin, y2 + 46), (width - margin, y2 + 46), width=2)

    y3 = y2 + 62
    pairs.append(("buyer_name", _kv(canvas, x, y3, "购买方名称：", buyer, label="buyer_name"), buyer))
    pairs.append(("buyer_tax_no", _kv(canvas, x, y3 + 40, "纳税人识别号：", buyer_tax, label="buyer_tax_no"), buyer_tax))
    y4 = y3 + 96
    pairs.append(("seller_name", _kv(canvas, x, y4, "销售方名称：", seller, label="seller_name"), seller))
    pairs.append(("seller_tax_no", _kv(canvas, x, y4 + 40, "纳税人识别号：", seller_tax, label="seller_tax_no"), seller_tax))

    table_top = y4 + (110 if portrait else 96)
    table_bottom = table_top + 40 * 3
    half = (amounts["amount"] / 2).quantize(Decimal("0.01"))
    rows = [
        [_GOODS[rng.randrange(len(_GOODS))], _SPECS[rng.randrange(len(_SPECS))], "1", money_text(half),
         money_text(half), "6%", money_text((amounts["tax_amount"] / 2).quantize(Decimal("0.01")))],
        [_GOODS[rng.randrange(len(_GOODS))], _SPECS[rng.randrange(len(_SPECS))], "1",
         money_text(amounts["amount"] - half), money_text(amounts["amount"] - half), "6%",
         money_text(amounts["tax_amount"] - (amounts["tax_amount"] / 2).quantize(Decimal("0.01")))],
    ]
    canvas.table((margin + 20, table_top, width - margin - 20, table_bottom),
                 ["项目名称", "规格型号", "数量", "单价", "金额", "税率", "税额"], rows, 18)

    y5 = table_bottom + 26
    canvas.text((margin + 20, y5), "合计", 20, label="label")
    amount_item = canvas.text_right(width - margin - 420, y5, money_text(amounts["amount"]), 23,
                                    label="amount", latin=True)
    tax_item = canvas.text_right(width - margin - 40, y5, money_text(amounts["tax_amount"]), 23,
                                 label="tax_amount", latin=True)
    pairs.append(("amount", amount_item, f"{amounts['amount']:.2f}"))
    pairs.append(("tax_amount", tax_item, f"{amounts['tax_amount']:.2f}"))

    y6 = y5 + 44
    canvas.text((margin + 20, y6), "价税合计（大写）", 20, label="label")
    canvas.text((margin + 240, y6), amount_to_chinese_upper(amounts["total_amount"]), 20, label="total_amount_upper")
    canvas.text((width - margin - 420, y6), "（小写）", 20, label="label")
    total_item = canvas.text_right(width - margin - 40, y6, money_text(amounts["total_amount"]), 24,
                                   label="total_amount", bold=True, latin=True)
    pairs.append(("total_amount", total_item, f"{amounts['total_amount']:.2f}"))

    y7 = y6 + 48
    pairs.append(("check_code", _kv(canvas, margin + 20, y7, "校验码：", check_code, value_size=22,
                                    label="check_code", latin_value=True), check_code))
    canvas.text((width / 2, height - (margin + 40)), "本发票为合成样本，仅用于算法评测", 16,
                label="footer", anchor="mm")

    return RenderedTicket(
        canvas=canvas,
        ticket_type="vat_general",
        form_factor="portrait_screenshot" if portrait else "landscape",
        fields=_register(canvas, pairs),
    )


# ---------------------------------------------------------------------------
# 2) 铁路电子客票（竖条）
# ---------------------------------------------------------------------------
def render_train_ticket(rng: random.Random) -> RenderedTicket:
    width, height = 600, 1000
    canvas = Canvas(width, height)
    canvas.border(margin=14)

    train_no = gen_train_no(rng)
    day = gen_date(rng)
    passenger = gen_person(rng)
    fare = gen_amounts(rng)["total_amount"]
    id_card = gen_id_card(rng)
    pairs: list[tuple[str, Drawn, str]] = []

    canvas.text((width / 2, 50), "铁路电子客票", 28, label="title", bold=True, anchor="mm")
    canvas.qrcode_placeholder((width - 180, 90, width - 40, 230))

    y = 260
    # 车次不属于 12 个抽取字段，只作为版式标签（boxes 里仍有 train_no 框）
    _kv(canvas, 50, y, "车次：", train_no, label="train_no")
    y += 60
    canvas.text((50, y), "北京西", 26, label="station_from")
    canvas.text((190, y - 6), "→", 30, label="station_arrow")
    canvas.text((250, y), "武汉", 26, label="station_to")
    y += 70
    pairs.append(("invoice_date", _kv(canvas, 50, y, "乘车日期：", date_text(day), label="invoice_date"), date_iso(day)))
    y += 60
    canvas.text((50, y), "08:05 开", 22, label="depart_time")
    y += 60
    _kv(canvas, 50, y, "席位：", "二等座", label="seat")
    y += 60
    pairs.append(("passenger_name", _kv(canvas, 50, y, "乘车人：", passenger, label="passenger_name"), passenger))
    y += 56
    _kv(canvas, 50, y, "身份证号：", id_card[:6] + "********" + id_card[-4:], label="id_card_masked")
    y += 70
    pairs.append(("total_amount", _kv(canvas, 50, y, "票价：", money_text(fare), value_size=26,
                                     label="total_amount", bold_value=True, latin_value=True), f"{fare:.2f}"))
    canvas.text((width / 2, height - 60), "本票为合成样本，仅用于算法评测", 16, label="footer", anchor="mm")

    return RenderedTicket(canvas=canvas, ticket_type="train_ticket", form_factor="portrait_strip",
                          fields=_register(canvas, pairs))


# ---------------------------------------------------------------------------
# 3) 航空运输电子客票行程单（横版）
# ---------------------------------------------------------------------------
def render_flight_itinerary(rng: random.Random) -> RenderedTicket:
    width, height = 1200, 700
    canvas = Canvas(width, height)
    canvas.border(margin=14)

    passenger = gen_person(rng)
    day = gen_date(rng)
    flight_no = gen_flight_no(rng)
    carrier = gen_company(rng)
    amounts = gen_amounts(rng)
    pairs: list[tuple[str, Drawn, str]] = []

    canvas.text((width / 2, 50), "航空运输电子客票行程单", 32, label="title", bold=True, anchor="mm")
    canvas.qrcode_placeholder((width - 190, 90, width - 40, 240))

    y = 230
    pairs.append(("passenger_name", _kv(canvas, 60, y, "旅客姓名：", passenger, label="passenger_name"), passenger))
    pairs.append(("invoice_date", _kv(canvas, 640, y, "填开日期：", date_text(day), label="invoice_date"), date_iso(day)))
    y += 56
    _kv(canvas, 60, y, "身份证号：", gen_id_card(rng)[:6] + "********" + "0011", label="id_card_masked")
    _kv(canvas, 640, y, "航班号：", flight_no, label="flight_no")
    y += 56
    pairs.append(("seller_name", _kv(canvas, 60, y, "承运人：", carrier, label="seller_name"), carrier))
    _kv(canvas, 640, y, "舱位：", "经济舱", label="cabin")

    table_top = 400
    canvas.table((60, table_top, width - 60, table_top + 120),
                 ["票价", "民航发展基金", "燃油附加费", "合计"],
                 [[money_text(amounts["amount"]), money_text(amounts["tax_amount"]), "¥0.00",
                   money_text(amounts["total_amount"])]], 20, row_height=42)

    y = table_top + 160
    canvas.text((60, y), "合计金额（大写）", 20, label="label")
    canvas.text((260, y), amount_to_chinese_upper(amounts["total_amount"]), 20, label="total_amount_upper")
    total_item = canvas.text_right(width - 60, y, money_text(amounts["total_amount"]), 26,
                                   label="total_amount", bold=True, latin=True)
    pairs.append(("total_amount", total_item, f"{amounts['total_amount']:.2f}"))

    canvas.text((width / 2, height - 50), "本行程单为合成样本，仅用于算法评测", 16, label="footer", anchor="mm")
    return RenderedTicket(canvas=canvas, ticket_type="flight_itinerary", form_factor="landscape",
                          fields=_register(canvas, pairs))


# ---------------------------------------------------------------------------
# 4) 出租车定额票（小票，测小目标）
# ---------------------------------------------------------------------------
def render_taxi_ticket(rng: random.Random) -> RenderedTicket:
    width, height = 480, 300
    canvas = Canvas(width, height)
    canvas.border(margin=8, width=1)

    pairs: list[tuple[str, Drawn, str]] = []
    company = gen_taxi_company(rng)
    code = gen_invoice_code(rng)
    number = gen_invoice_no(rng)
    day = gen_date(rng)
    amount = gen_amounts(rng)["total_amount"]

    canvas.text((width / 2, 18), "武汉市出租汽车定额发票", 20, label="title", bold=True, anchor="mm")
    pairs.append(("seller_name", canvas.text((24, 50), company, 17, label="seller_name"), company))
    pairs.append(("invoice_code", _kv(canvas, 24, 84, "发票代码：", code, key_size=17, value_size=19,
                                     label="invoice_code", latin_value=True), code))
    pairs.append(("invoice_no", _kv(canvas, 24, 112, "发票号码：", number, key_size=17, value_size=19,
                                   label="invoice_no", latin_value=True), number))
    pairs.append(("invoice_date", _kv(canvas, 24, 140, "日期：", date_text(day), key_size=17, value_size=17,
                                     label="invoice_date"), date_iso(day)))
    _kv(canvas, 24, 168, "车牌号：", gen_plate(rng), key_size=17, value_size=17, label="plate")
    pairs.append(("total_amount", _kv(canvas, 24, 200, "金额：", money_text(amount), key_size=18,
                                     value_size=26, label="total_amount", bold_value=True,
                                     latin_value=True), f"{amount:.2f}"))
    canvas.text((width / 2, height - 26), "合成样本", 13, label="footer", anchor="mm")

    return RenderedTicket(canvas=canvas, ticket_type="taxi_ticket", form_factor="receipt",
                          fields=_register(canvas, pairs))


# ---------------------------------------------------------------------------
# 5) 电子发票（普通发票）——数电票，2024-12 起全国推广后的主流票种
#
# 版面几何来自一份真实数电票 PDF 的**坐标量测**（只取相对位置，未读取任何字段值）：
#   页面 210×140mm；标题居中 y≈6%；发票号码 x≈74% y≈8%；开票日期同列 y≈12%；
#   购销方信息 y≈23%–33%；表头 y≈38%；合计 y≈66%；价税合计 y≈70%；开票人 y≈93%。
# 与旧版 `vat_general` 的关键差异：**没有发票代码、没有校验码**，发票号码是 20 位。
# ---------------------------------------------------------------------------
def render_vat_digital(rng: random.Random) -> RenderedTicket:
    width, height = 1240, 827  # 210×140mm @150dpi
    canvas = Canvas(width, height)
    canvas.border(margin=10, width=2)

    amounts = gen_amounts(rng)
    invoice_no = gen_digital_invoice_no(rng)
    day = gen_date(rng)
    buyer = gen_company(rng)
    buyer_tax = gen_uscc(rng)
    seller = gen_company(rng)
    seller_tax = gen_uscc(rng)
    issuer = gen_person(rng)
    pairs: list[tuple[str, Drawn, str]] = []

    canvas.text((width / 2, 48), "电子发票（普通发票）", 40, label="title", bold=True, anchor="mm")

    # 右上角：发票号码 / 开票日期（真实版面在此列）
    right_x = 912
    pairs.append(("invoice_no", _kv(canvas, right_x, 60, "发票号码：", invoice_no, key_size=18,
                                    value_size=19, label="invoice_no", latin_value=True), invoice_no))
    pairs.append(("invoice_date", _kv(canvas, right_x, 98, "开票日期：", date_text(day), key_size=18,
                                     value_size=19, label="invoice_date"), date_iso(day)))

    # 购销方信息（左右两栏）
    left_x, mid_x = 35, 700
    canvas.text((left_x, 186), "购买方信息", 20, label="label", bold=True)
    canvas.text((mid_x, 186), "销售方信息", 20, label="label", bold=True)
    tax_key = "统一社会信用代码/纳税人识别号："
    pairs.append(("buyer_name", _kv(canvas, left_x, 216, "名称：", buyer, key_size=18,
                                    value_size=20, label="buyer_name"), buyer))
    pairs.append(("buyer_tax_no", _kv(canvas, left_x, 246, tax_key, buyer_tax, key_size=16,
                                     value_size=19, label="buyer_tax_no", latin_value=True), buyer_tax))
    pairs.append(("seller_name", _kv(canvas, mid_x, 216, "名称：", seller, key_size=18,
                                     value_size=20, label="seller_name"), seller))
    pairs.append(("seller_tax_no", _kv(canvas, mid_x, 246, tax_key, seller_tax, key_size=16,
                                      value_size=19, label="seller_tax_no", latin_value=True), seller_tax))

    # 明细表（真实版面：表头 y≈38%，表格一直延伸到 y≈64% 附近）
    headers = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"]
    half = (amounts["amount"] / 2).quantize(Decimal("0.01"))
    rows = [
        [_GOODS[rng.randrange(len(_GOODS))], _SPECS[rng.randrange(len(_SPECS))], "件", "1",
         money_text(half), money_text(half), "6%",
         money_text((amounts["tax_amount"] / 2).quantize(Decimal("0.01")))],
        [_GOODS[rng.randrange(len(_GOODS))], _SPECS[rng.randrange(len(_SPECS))], "件", "1",
         money_text(amounts["amount"] - half), money_text(amounts["amount"] - half), "6%",
         money_text(amounts["tax_amount"] - (amounts["tax_amount"] / 2).quantize(Decimal("0.01")))],
    ]
    table_top = 300
    canvas.table((left_x, table_top, width - 35, 520), headers, rows, 17, row_height=36)

    # 合计（金额/税额）
    y_total = 545
    canvas.text((left_x + 8, y_total), "合计", 19, label="label")
    amount_item = canvas.text_right(width - 300, y_total, money_text(amounts["amount"]), 21,
                                    label="amount", latin=True)
    tax_item = canvas.text_right(width - 40, y_total, money_text(amounts["tax_amount"]), 21,
                                 label="tax_amount", latin=True)
    pairs.append(("amount", amount_item, f"{amounts['amount']:.2f}"))
    pairs.append(("tax_amount", tax_item, f"{amounts['tax_amount']:.2f}"))

    # 价税合计（大写 + 小写）
    y_sum = 580
    canvas.text((left_x + 8, y_sum), "价税合计（大写）", 19, label="label")
    canvas.text((left_x + 230, y_sum), amount_to_chinese_upper(amounts["total_amount"]), 19,
                label="total_amount_upper")
    canvas.text((width - 330, y_sum), "（小写）", 19, label="label")
    total_item = canvas.text_right(width - 40, y_sum, money_text(amounts["total_amount"]), 23,
                                   label="total_amount", bold=True, latin=True)
    pairs.append(("total_amount", total_item, f"{amounts['total_amount']:.2f}"))

    # 备注 / 开票人 / 二维码占位
    canvas.text((left_x + 8, 620), "备注：本发票为合成样本，仅用于算法评测", 16, label="footer")
    canvas.qrcode_placeholder((width - 170, 610, width - 50, 730))
    _kv(canvas, left_x + 8, 762, "开票人：", issuer, key_size=17, value_size=18, label="issuer")

    return RenderedTicket(canvas=canvas, ticket_type="vat_digital", form_factor="landscape",
                          fields=_register(canvas, pairs))


RENDERERS = {
    ("vat_digital", "landscape"): render_vat_digital,
    ("vat_general", "landscape"): lambda rng: render_vat_general(rng, portrait=False),
    ("vat_general", "portrait_screenshot"): lambda rng: render_vat_general(rng, portrait=True),
    ("train_ticket", "portrait_strip"): render_train_ticket,
    ("flight_itinerary", "landscape"): render_flight_itinerary,
    ("taxi_ticket", "receipt"): render_taxi_ticket,
}


def render(ticket_type: str, form_factor: str, rng: random.Random) -> RenderedTicket:
    try:
        renderer = RENDERERS[(ticket_type, form_factor)]
    except KeyError as exc:
        raise KeyError(f"未实现的版式：{ticket_type}/{form_factor}（可选：{sorted(RENDERERS)}）") from exc
    return renderer(rng)