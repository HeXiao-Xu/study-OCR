#!/usr/bin/env node
// S4-1 跨语言验收：Node 侧**独立实现** canonical JSON，再算 SM3 / SHA-256。
//
// 用法：cd /e/BiSheXZY/study-OCR && node scripts/canonical_cross_check.mjs
//
// 关键坑（spec §3）：JS 的 Array.prototype.sort() 默认比较 **UTF-16 码元**，
// 而 Unicode 码点序与码元序在"BMP 字符 U+E000–U+FFFF"与"增补平面字符"之间会相反
// （😀 = U+1F600 的首码元是 0xD83D，小于 ～ = U+FF5E）。
// 因此这里显式按码点比较；fixture 专门放了 "～" 与 "😀" 两个键来暴露这个差异。
//
// 依赖 Node ≥ 18（OpenSSL 3 提供 sm3），不引入任何 npm 包。

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const FIXTURE = join(ROOT, "scripts", "canonical_fixture.json");

/** 按 Unicode 码点比较两个字符串（不是 UTF-16 码元）。 */
function compareCodePoints(a, b) {
  const left = Array.from(a);
  const right = Array.from(b);
  const shared = Math.min(left.length, right.length);
  for (let i = 0; i < shared; i += 1) {
    const diff = left[i].codePointAt(0) - right[i].codePointAt(0);
    if (diff !== 0) return diff;
  }
  return left.length - right.length;
}

/** canonical JSON：键按码点升序、无空格分隔符、UTF-8、拒绝 float。 */
function canonicalize(value) {
  if (value === null) return "null";
  switch (typeof value) {
    case "boolean":
      return value ? "true" : "false";
    case "number":
      if (!Number.isInteger(value)) {
        throw new Error("fixture 含 float：canonical JSON 拒绝 float");
      }
      if (!Number.isSafeInteger(value)) {
        throw new Error("整数超出 ±(2^53-1)，跨语言无法精确表示");
      }
      return String(value); // -0 → "0"，与 Python int 一致
    case "string":
      return JSON.stringify(value); // 标准 JSON 转义（短转义 + \u00XX 小写）
    case "object": {
      if (Array.isArray(value)) {
        return `[${value.map(canonicalize).join(",")}]`;
      }
      const keys = Object.keys(value).sort(compareCodePoints);
      return `{${keys
        .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
        .join(",")}}`;
    }
    default:
      throw new Error(`不支持的类型 ${typeof value}`);
  }
}

const payload = JSON.parse(readFileSync(FIXTURE, "utf8"));
const canonical = canonicalize(payload);
const bytes = Buffer.from(canonical, "utf8");

console.log(`canonical_utf8_b64: ${bytes.toString("base64")}`);
console.log(`sm3: ${createHash("sm3").update(bytes).digest("hex")}`);
console.log(`sha256: ${createHash("sha256").update(bytes).digest("hex")}`);