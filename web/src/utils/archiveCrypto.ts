/**
 * 浏览器内解密归档包（S5-6）：与后端 `crypto/archive_crypto.py` 的容器格式一一对应。
 *
 * 格式：magic(16B "STUDYOCR-ARCHIVE") ‖ version(2B "v1") ‖ iterations(4B BE)
 *       ‖ salt(16B) ‖ nonce(12B) ‖ AES-256-GCM(ciphertext ‖ tag)
 * KDF：PBKDF2-HMAC-SHA256（与后端一致，200000 次）；AAD = magic ‖ version ‖ iterations。
 *
 * 这样用户不用命令行：在“归档检索 → 打开本地归档包”里选文件、输口令，浏览器解密后
 * 直接下载标准 ZIP，双击即可用系统解压打开。
 */
const MAGIC = new TextEncoder().encode('STUDYOCR-ARCHIVE')
const VERSION = new TextEncoder().encode('v1')
const HEADER_LEN = MAGIC.length + VERSION.length + 4 + 16 + 12

function bytesEqual(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return false
  }
  return true
}

export class ArchiveDecryptError extends Error {}

export async function decryptArchiveFile(
  buffer: ArrayBuffer,
  passphrase: string,
): Promise<Blob> {
  const bytes = new Uint8Array(buffer)
  if (bytes.length <= HEADER_LEN) {
    throw new ArchiveDecryptError('文件不是有效的归档包（长度不足）')
  }
  if (!bytesEqual(bytes.slice(0, MAGIC.length), MAGIC)) {
    throw new ArchiveDecryptError('文件不是有效的归档包（magic 不匹配）')
  }
  const versionOffset = MAGIC.length
  if (!bytesEqual(bytes.slice(versionOffset, versionOffset + VERSION.length), VERSION)) {
    throw new ArchiveDecryptError('归档包版本不支持')
  }
  const view = new DataView(buffer)
  const iterations = view.getUint32(versionOffset + VERSION.length)
  if (iterations < 200_000) {
    throw new ArchiveDecryptError('归档包 KDF 参数异常')
  }
  const saltOffset = versionOffset + VERSION.length + 4
  const nonceOffset = saltOffset + 16
  const cipherOffset = nonceOffset + 12
  const salt = bytes.slice(saltOffset, nonceOffset)
  const nonce = bytes.slice(nonceOffset, cipherOffset)
  const ciphertext = bytes.slice(cipherOffset)
  // AAD = magic ‖ version ‖ iterations（后端 _aad 同口径）
  const aad = bytes.slice(0, saltOffset)

  try {
    const baseKey = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(passphrase),
      'PBKDF2',
      false,
      ['deriveKey'],
    )
    const key = await crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt, iterations, hash: 'SHA-256' },
      baseKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt'],
    )
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: nonce, additionalData: aad },
      key,
      ciphertext,
    )
    return new Blob([plaintext], { type: 'application/zip' })
  } catch {
    throw new ArchiveDecryptError('口令错误或归档包已损坏')
  }
}

export function zipNameOf(filename: string): string {
  return filename.endsWith('.zip.enc')
    ? filename.slice(0, -'.zip.enc'.length) + '.zip'
    : filename.replace(/\.[^.]+$/, '') + '.zip'
}