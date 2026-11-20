# S4-1 SM3 摘要与 canonical JSON 规范化 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-1（★关键，密码链路起点）。
> 阶段 4 规则：只写 `backend/app/crypto/` 与 `backend/tests/test_crypto/`，**不碰业务接口**；
> 算法参数、密钥格式、载荷结构在这里定死，S5 只调用不修改。
> 状态：**已实现（2026-11-20）**，口径 Q1–Q11 全部落地并有测试；实测见 `PROGRESS.md` S4-1 行。

## 1. 目标

同一份业务数据，**任何进程、任何语言**序列化成**唯一字节串**，再算出唯一摘要。

```
业务 dict ──canonical_json──▶ bytes ──sm3_digest──▶ 32B 摘要 ──hex──▶ 签名域/审计域
```

- `canonical_json(obj) -> bytes`：JSON 子集的确定性序列化（本卡核心）；
- `canonical_sha256(obj) -> str`：同口径 SHA-256 十六进制，**仅作对照基准**（跨实现互验、论文对照表），
  不参与国密签名链路；
- `sm3_digest(data) -> bytes(32)`：SM3 唯一入口；`sm3_hex(data) -> str` 仅用于日志/调试。

范围外（不做）：SM2/SM4/信封/密钥/证书/审计链（S4-2 ~ S4-7）；`hmac_sm3` 属 S4-3 的 Encrypt-then-MAC
（AGENTS §10.4 的对外清单里它排在 S4-3 落地，本卡只实现 `sm3_digest/sm3_hex`，不提前写）。

## 2. 已拍板的口径（本卡定死，S5 不再改）

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | `Decimal` 能否进 `canonical_json` | **拒绝**（连带 `float` 一起拒绝） | `100` / `100.00` / `100.0` 会产出三种字节；金额一律由调用方先 `format(x, "f")` 成字符串。字符串是唯一金额载体 |
| Q2 | dict 非 str 键 | **拒绝**，不隐式 `str()` | `{1: "a"}` 与 `{"1": "a"}` 不能撞成同一摘要 |
| Q3 | 整数范围 | **拒绝 `|n| > 2^53-1`** | JS `Number` 只能精确到 2^53-1；放行会让 Python 摘要与 Node 摘要静默分叉。超界请改字符串（业务 id/金额本就不该走裸 int） |
| Q4 | `bool` 与 `int` | `bool` 先判，输出 `true/false`；`True` 与 `1` **不同摘要** | Python 里 `bool` 是 `int` 子类，不先判会把 `True` 写成 `1` |
| Q5 | 其它类型（`bytes`/`tuple`/`set`/自定义对象） | **拒绝** | 不在 JSON 数据模型内，静默转换会制造第二套口径 |
| Q6 | 空容器 | `{}` / `[]` | 与 `json.dumps` 一致 |
| Q7 | 非 ASCII 字符 | `ensure_ascii=False` + UTF-8；控制字符按 JSON 标准转义 | 中文票据字段必须可读且字节稳定 |
| Q8 | `sm3_digest` 入参 | 接受 `bytes`/`bytearray`/`memoryview`，内部转 `bytes`；**`str` 拒绝（TypeError）** | 编码是调用方的责任，`str` 隐式 UTF-8 会掩盖编码 bug |
| Q9 | 摘要的对外形态 | `sm3_digest` 回 `bytes(32)`；`sm3_hex` 回 64 位小写 hex；`canonical_sha256` 回 64 位小写 hex | 二进制进签名域，hex 只进日志 |
| Q10 | 孤立代理项（lone surrogate） | **拒绝**（无法编码成 UTF-8） | Python `str` 可以持有非法码元，先拒比让 `encode` 抛原生 `UnicodeEncodeError` 更可控 |
| Q11 | 循环引用 | **拒绝**，抛 `CanonicalJSONError` | 序列化必须有界；不能进死循环 |

## 3. `crypto/canonical.py` 精确规则

```python
def canonical_json(obj: object) -> bytes: ...
def canonical_sha256(obj: object) -> str: ...   # 64 位小写 hex
class CanonicalJSONError(ValueError): ...
```

序列化规则（逐条可断言）：

1. 输出 UTF-8 字节，**无 BOM、无尾随换行**；
2. dict：键必须是 `str`；按键的 **Unicode 码点升序**排序；成员间 `,`、键值间 `:`，**无空格**；
3. list：保持顺序，元素间 `,`；
4. `None` → `null`；`True/False` → `true/false`；
5. int → 十进制（负数带 `-`，无 `+`、无小数点、无指数、无前导零）；
6. str → 双引号 JSON 字符串，`ensure_ascii=False`（中文原样输出），
   `"`/`\` 转义，`\b \f \n \r \t` 用短转义，其余 `U+0000..U+001F` 用 `\u00XX`（小写 hex）；
7. 递归下降，非法类型/键/超界整数/代理项/循环 → `CanonicalJSONError`（`ValueError` 子类）；
8. 同一对象重复调用结果**逐字节相同**（无随机性、无时间戳）。

实现策略：先用**校验遍历**（`_validate`）把非法输入挡在 `json.dumps` 之前，
再用 `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`
做真正的转义与拼装，最后 `.encode("utf-8")`。理由：转义细节（短转义、`\u00XX`、代理项、
循环检测）由标准库保证，比手写更不容易与 JSON 标准分叉；校验遍历负责补上标准库**不做**的事
（拒 float/Decimal/非 str 键/超界 int/非法类型）。

> ⚠️ 跨语言坑（写进论文"实现"章节）：JS 的 `Array.prototype.sort()` 默认比较 **UTF-16 码元**，
> 遇到 BMP 外字符（emoji、部分生僻字）键序会与 Python 的**码点序**不同。
> Node 侧参考实现必须显式按码点比较（`[...key]` 逐码点比），否则"跨语言同摘要"不成立。
> 本卡用 `scripts/canonical_cross_check.{py,mjs}` 把这个坑测出来（fixture 里含一个 emoji 键）。

## 4. `crypto/sm/sm3.py` 精确规则

```python
def sm3_digest(data: bytes | bytearray | memoryview) -> bytes: ...  # 长度恒为 32
def sm3_hex(data: bytes | bytearray | memoryview) -> str: ...        # 64 位小写 hex，仅日志
```

- 唯一实现：`gmssl.sm3.sm3_hash`；**这是全项目唯一允许 import gmssl.sm3 的地方**（AGENTS §6）；
- `sm3_hash` 收 `list[int]` 且**会原地修改入参**（内部做填充），因此必须传 `list(bytes)` 的**副本**，
  绝不把调用方的 `bytearray` 直接透传；返回的 hex 再 `bytes.fromhex()` 成 32 字节；
- 返回长度不是 64 就抛 `RuntimeError`（库契约被破坏，宁可炸也不返回半截摘要）；
- 空输入合法（`sm3_digest(b"") == 1ab21d…2aa2b`）；
- 不提供 `hmac_sm3`（S4-3）；不提供"直接调 gmssl"的逃生口。

**随机源**：本卡没有任何随机数（摘要不需要）；S4-2 起才用 `secrets`，AGENTS §6 的高压线本卡不触碰。

## 5. 测试（`backend/tests/test_crypto/`）

新目录：`__init__.py` + `test_canonical.py` + `test_sm3.py`（默认跑，不加 `slow`/`integration` 标记）。

`test_canonical.py`（每条对应 §3 一条规则）：

| 用例 | 断言 |
|---|---|
| 键序不同同摘要 | `{"b":1,"a":2}` 与 `{"a":2,"b":1}` → 同 `canonical_json` 字节、同 `canonical_sha256` |
| 精确字节 | `{"a":2,"b":1}` → `b'{"a":2,"b":1}'`（无空格、排序后） |
| 中文不转义 | `{"name":"发票"}` → `b'{"name":"\xe5\x8f\x91\xe7\xa5\xa8"}'`，`b"\\u"` 不出现 |
| 负数与嵌套 list | `{"amount":"-100.00","items":[1,-2,[3,None]]}` 精确字节 |
| None | `{"a":None}` → `b'{"a":null}'` |
| 空容器 | `{}` → `b"{}"`；`[]` → `b"[]"` |
| `100` vs `"100.00"` vs `"100"` | 两两字节/摘要不同；`float 100.0` 与 `Decimal("100.00")` 均抛 `CanonicalJSONError` |
| float/NaN/Inf 拒绝 | `1.5`、`float("nan")`、`float("inf")`、`Decimal("1")` 全抛 |
| 非 str 键拒绝 | `{1:"a"}`、`{None:"a"}`、`{(1,2):"a"}` 全抛 |
| 超界 int 拒绝 | `2**53`、`-(2**53)` 抛；`2**53-1` 通过 |
| bool 不与 1 撞车 | `{"a":True}` vs `{"a":1}` 字节不同 |
| 控制字符转义 | `{"a":"x\ny\t\u0001"}` → `b'{"a":"x\\ny\\t\\u0001"}'` |
| 码点序 | `{"Z":1,"a":2,"中":3,"😀":4}` 顺序为 `Z,a,中,😀` |
| 非法容器/对象 | `b"x"`、`(1,2)`、`{1,2}`、`object()` 抛 |
| 循环引用 | 自引用 list/dict 抛 `CanonicalJSONError` 且不挂死 |
| 孤立代理项 | `"\ud800"` 抛 `CanonicalJSONError` |
| 可重复性 | 同一对象连续 100 次字节相同 |

`test_sm3.py`：

| 用例 | 断言 |
|---|---|
| 国标向量 `abc` | `66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0` |
| 国标向量 64 字节 | `b"abcd"*16` → `debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732` |
| 空输入 | `b""` → `1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b` |
| 返回类型/长度 | `isinstance(bytes)`、`len == 32` |
| 1 bit 篡改 | `sm3_digest(b"abc")` ≠ `sm3_digest(b"abd")` |
| 入参不被修改 | `bytearray` 前后相同（防 gmssl 原地改） |
| 入参类型 | `bytearray`/`memoryview` 可用；`str` 抛 `TypeError` |
| `sm3_hex` | 等于 `sm3_digest(...).hex()`，64 位小写 |
| 分段一致性 | 同一数据的 `bytes` 与 `bytearray`/`memoryview` 三种形态摘要相同 |

## 6. 跨语言验收（命令行脚本）

- `scripts/canonical_fixture.json`：一份含中文、负数、嵌套 list、null、emoji 键、控制字符的载荷；
- `scripts/canonical_cross_check.py`：读 fixture → 打印 `{canonical_b64, sm3, sha256}`；
- `scripts/canonical_cross_check.mjs`：Node 侧**独立实现**同口径规范化（按码点排序）→ 打印同一结构；
- 断言：两侧 `canonical_b64`/`sm3`/`sha256` 三个字段**完全相等**。

Node 侧依赖 Node ≥ 18 的 OpenSSL 3 SM3（本机 `node v25.2.1` 实测 `createHash("sm3")` 可用）；
脚本不新增 npm 依赖，不进 pytest（避免测试套件依赖 Node 运行时）。

## 7. 依赖与环境

- 新增运行时依赖 **`gmssl==3.2.2`**（PyPI 纯 Python 版；AGENTS §2.2 指定版本）。已按用户
  "开始 S4-1 + 不再逐步确认"的指令直接安装，安装后**立即写进 `requirements.txt` 与
  `requirements.lock.txt`**，并记 `D-073`。装完必须回归 `pytest`（确认 numpy/paddle 未被顶版本）。
- 不装 `gmssl-python`（ctypes 版，需编译 libgmssl，AGENTS §2.2 明确不进主线）。
- 无新环境变量、无新表/迁移、不动 `.env.example`（本卡纯计算，无配置）。

## 8. 验收标准（任务卡原文 + 本卡补充）

1. `pytest backend/tests/test_crypto -q` **全绿**，且含 SM3 国标向量；
2. 键序不同的两个 dict 摘要相同；含中文/负数/嵌套 list 的用例通过；
3. `100` 与 `100.00` 的差异有明确用例（一个是 int、一个必须走字符串；float/Decimal 被拒）；
4. `canonical_cross_check.py` 与 `.mjs` 三字段完全一致；
5. 全量 `pytest` 无回归（默认跳过 integration）；`alembic check` 零差异（本卡无迁移）；
6. 文档收口：`PROGRESS`、`DECISIONS`（D-073 依赖 + D-074 口径）、AGENTS §10.8 锚点行、`HANDOFF`。