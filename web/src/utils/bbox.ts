/** 影像框坐标的唯一换算实现（S3-5）。
 *
 * 后端给的 bbox 是**原图**坐标 `[x1,y1,x2,y2]`；显示时按 `displayWidth / naturalWidth` 缩放。
 * 缩放通过改 img 的 CSS 宽高实现（不是 CSS transform），canvas 始终按像素重绘、不会发虚。
 */
export interface Box {
  x1: number
  y1: number
  x2: number
  y2: number
}

export const MIN_ZOOM = 1
export const MAX_ZOOM = 4

export function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom))
}

/** 原图框 → 展示坐标框；sx/sy 分别是水平/垂直缩放（等比时两者相等，仍分开算以防未来拉伸）。 */
export function toDisplayBox(bbox: number[], sx: number, sy: number): Box {
  return { x1: bbox[0] * sx, y1: bbox[1] * sy, x2: bbox[2] * sx, y2: bbox[3] * sy }
}

/** 框与可视矩形是否相交（culling：只画视口附近的框）。 */
export function intersects(box: Box, view: Box): boolean {
  return box.x2 >= view.x1 && box.x1 <= view.x2 && box.y2 >= view.y1 && box.y1 <= view.y2
}

/** 命中测试：点在哪个框里；多个框重叠时取面积最小的（更具体的那个）。 */
export function hitTest<T extends { id: string; box: Box }>(
  items: T[],
  x: number,
  y: number,
): string | null {
  let best: T | null = null
  let bestArea = Number.POSITIVE_INFINITY
  for (const item of items) {
    const { x1, y1, x2, y2 } = item.box
    if (x < x1 || x > x2 || y < y1 || y > y2) continue
    const area = (x2 - x1) * (y2 - y1)
    if (area < bestArea) {
      best = item
      bestArea = area
    }
  }
  return best ? best.id : null
}
