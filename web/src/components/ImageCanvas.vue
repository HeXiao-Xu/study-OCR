<script setup lang="ts">
/**
 * 票据影像画布（S3-5）：`<img>` + canvas 叠层，不改原图、不在图上烧字。
 *
 * 坐标：后端给的字段框/行框都是**原图**坐标；这里只在 `utils/bbox.ts` 一处做缩放换算。
 * 缩放通过改 img 的 CSS 宽高实现（不是 CSS transform），canvas 每次按像素重绘，缩放后不发虚、不错位。
 * 只画与可视矩形相交的框（culling），并暴露 `data-*` 调试属性供 `ui_smoke --eval` 做数值断言。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { clampZoom, hitTest, intersects, toDisplayBox, type Box } from '../utils/bbox'

interface FieldBoxInput {
  fieldName: string
  label: string
  bbox: number[]
  level: string | null
  conflict: boolean
}
interface LineBoxInput {
  lineNo: number
  bbox: number[]
  score: number | null
  fieldName: string | null
}

const props = defineProps<{
  src: string
  fieldBoxes: FieldBoxInput[]
  lineBoxes: LineBoxInput[]
  activeField: string | null
  hoveredField: string | null
  page: number
  pageCount: number
}>()

const emit = defineEmits<{
  'select-field': [fieldName: string | null]
  'hover-field': [fieldName: string | null]
  'update:page': [page: number]
}>()

const wrap = ref<HTMLDivElement | null>(null)
const imageEl = ref<HTMLImageElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const zoom = ref(1)
const naturalW = ref(0)
const naturalH = ref(0)
const baseWidth = ref(0)
const drawMs = ref(0)
const visibleLines = ref(0)
let frame = 0
let dragging = false
let dragMoved = false
let hoveredLocal: string | null = null
const last = { x: 0, y: 0 }

const displayWidth = computed(() => Math.max(1, Math.round(baseWidth.value * zoom.value)))
const displayHeight = computed(() =>
  naturalW.value
    ? Math.max(1, Math.round((displayWidth.value * naturalH.value) / naturalW.value))
    : 1,
)
const scaleX = computed(() => (naturalW.value ? displayWidth.value / naturalW.value : 1))
const scaleY = computed(() => (naturalH.value ? displayHeight.value / naturalH.value : 1))

const fieldDisplayBoxes = computed(() =>
  props.fieldBoxes
    .filter((item) => item.bbox.length === 4)
    .map((item) => ({
      fieldName: item.fieldName,
      level: item.level,
      conflict: item.conflict,
      box: toDisplayBox(item.bbox, scaleX.value, scaleY.value),
    })),
)
const lineDisplayBoxes = computed(() =>
  props.lineBoxes
    .filter((item) => item.bbox.length === 4)
    .map((item) => ({
      lineNo: item.lineNo,
      fieldName: item.fieldName,
      box: toDisplayBox(item.bbox, scaleX.value, scaleY.value),
    })),
)

function scheduleDraw(): void {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    draw()
  })
}

function draw(): void {
  const element = canvas.value
  if (!element || !displayWidth.value) return
  const started = performance.now()
  const dpr = window.devicePixelRatio || 1
  element.width = Math.round(displayWidth.value * dpr)
  element.height = Math.round(displayHeight.value * dpr)
  element.style.width = `${displayWidth.value}px`
  element.style.height = `${displayHeight.value}px`
  const ctx = element.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, displayWidth.value, displayHeight.value)

  const left = wrap.value?.scrollLeft ?? 0
  const top = wrap.value?.scrollTop ?? 0
  const view: Box = {
    x1: left,
    y1: top,
    x2: left + (wrap.value?.clientWidth ?? 0),
    y2: top + (wrap.value?.clientHeight ?? 0),
  }

  let visible = 0
  for (const item of lineDisplayBoxes.value) {
    if (!intersects(item.box, view)) continue
    visible += 1
    const hot =
      Boolean(item.fieldName) &&
      (item.fieldName === props.activeField || item.fieldName === props.hoveredField)
    ctx.strokeStyle = hot ? 'rgba(64,158,255,0.95)' : 'rgba(120,150,190,0.45)'
    ctx.lineWidth = hot ? 2 : 1
    ctx.strokeRect(item.box.x1, item.box.y1, item.box.x2 - item.box.x1, item.box.y2 - item.box.y1)
  }
  visibleLines.value = visible

  for (const item of fieldDisplayBoxes.value) {
    if (!intersects(item.box, view)) continue
    const active = item.fieldName === props.activeField
    const hovered = item.fieldName === props.hoveredField
    const color =
      item.conflict || item.level === 'manual'
        ? '#f56c6c'
        : item.level === 'review'
          ? '#e6a23c'
          : '#67c23a'
    if (active) {
      ctx.fillStyle = 'rgba(230,162,60,0.16)'
      ctx.fillRect(item.box.x1, item.box.y1, item.box.x2 - item.box.x1, item.box.y2 - item.box.y1)
    }
    ctx.strokeStyle = color
    ctx.lineWidth = active ? 3 : hovered ? 2.5 : 1.6
    ctx.setLineDash(item.conflict ? [6, 3] : [])
    ctx.strokeRect(item.box.x1, item.box.y1, item.box.x2 - item.box.x1, item.box.y2 - item.box.y1)
    ctx.setLineDash([])
  }

  const spent = performance.now() - started
  drawMs.value = Math.round((drawMs.value * 0.7 + spent * 0.3) * 100) / 100
}

function canvasPoint(event: PointerEvent): { x: number; y: number } | null {
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return null
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}

type DisplayLine = { lineNo: number; fieldName: string | null; box: Box }

function boxAt(event: PointerEvent): { field: string | null; line: DisplayLine | null } {
  const point = canvasPoint(event)
  if (!point) return { field: null, line: null }
  const field = hitTest(
    fieldDisplayBoxes.value.map((item) => ({ id: item.fieldName, box: item.box })),
    point.x,
    point.y,
  )
  const lineId = hitTest(
    lineDisplayBoxes.value.map((item) => ({ id: `L${item.lineNo}`, box: item.box })),
    point.x,
    point.y,
  )
  const line = lineId
    ? lineDisplayBoxes.value.find((item) => `L${item.lineNo}` === lineId) ?? null
    : null
  return { field, line }
}

function onWheel(event: WheelEvent): void {
  event.preventDefault()
  const before = zoom.value
  const next = clampZoom(before * (event.deltaY < 0 ? 1.2 : 1 / 1.2))
  if (next === before) return
  const rect = canvas.value?.getBoundingClientRect()
  const ratioX = rect && rect.width ? (event.clientX - rect.left) / rect.width : 0.5
  const ratioY = rect && rect.height ? (event.clientY - rect.top) / rect.height : 0.5
  zoom.value = next
  void nextTick(() => {
    const container = wrap.value
    if (!container) return
    const containerRect = container.getBoundingClientRect()
    container.scrollLeft = displayWidth.value * ratioX - (event.clientX - containerRect.left)
    container.scrollTop = displayHeight.value * ratioY - (event.clientY - containerRect.top)
    scheduleDraw()
  })
}

function onPointerDown(event: PointerEvent): void {
  dragging = true
  dragMoved = false
  last.x = event.clientX
  last.y = event.clientY
  canvas.value?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  if (dragging && wrap.value) {
    const dx = event.clientX - last.x
    const dy = event.clientY - last.y
    if (Math.abs(dx) + Math.abs(dy) > 3) dragMoved = true
    wrap.value.scrollLeft -= dx
    wrap.value.scrollTop -= dy
    last.x = event.clientX
    last.y = event.clientY
    scheduleDraw()
    return
  }
  const hit = boxAt(event)
  const nextHover = hit.field ?? hit.line?.fieldName ?? null
  if (nextHover !== hoveredLocal) {
    hoveredLocal = nextHover
    emit('hover-field', nextHover)
  }
}

function onPointerUp(event: PointerEvent): void {
  dragging = false
  canvas.value?.releasePointerCapture?.(event.pointerId)
}

function handleLeave(): void {
  dragging = false
  if (hoveredLocal !== null) {
    hoveredLocal = null
    emit('hover-field', null)
  }
}

function onCanvasClick(event: MouseEvent): void {
  if (dragMoved) {
    dragMoved = false
    return
  }
  const hit = boxAt(event as unknown as PointerEvent)
  emit('select-field', hit.field ?? hit.line?.fieldName ?? null)
}

function onImageLoad(): void {
  const element = imageEl.value
  if (element) {
    naturalW.value = element.naturalWidth
    naturalH.value = element.naturalHeight
  }
  baseWidth.value = wrap.value?.clientWidth ?? 0
  scheduleDraw()
}

function scrollToField(fieldName: string): void {
  const item = fieldDisplayBoxes.value.find((entry) => entry.fieldName === fieldName)
  const container = wrap.value
  if (!item || !container) return
  container.scrollTo({
    left: Math.max(0, item.box.x1 - container.clientWidth / 2),
    top: Math.max(0, item.box.y1 - container.clientHeight / 2),
    behavior: 'smooth',
  })
}

let observer: ResizeObserver | null = null
onMounted(() => {
  if (wrap.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => {
      baseWidth.value = wrap.value?.clientWidth ?? 0
      scheduleDraw()
    })
    observer.observe(wrap.value)
  }
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
})

watch(
  [
    () => props.fieldBoxes,
    () => props.lineBoxes,
    () => props.activeField,
    () => props.hoveredField,
    zoom,
    () => props.src,
  ],
  scheduleDraw,
  { deep: true },
)

defineExpose({ scrollToField, draw })
</script>

<template>
  <div class="canvas-shell">
    <div class="canvas-toolbar">
      <el-button-group size="small">
        <el-button @click="zoom = clampZoom(zoom / 1.2)">缩小</el-button>
        <el-button @click="zoom = 1">适应</el-button>
        <el-button @click="zoom = clampZoom(zoom * 1.2)">放大</el-button>
      </el-button-group>
      <span class="zoom-label">{{ Math.round(zoom * 100) }}%</span>
      <span v-if="pageCount > 1" class="pager">
        <el-button size="small" :disabled="page <= 1" @click="emit('update:page', page - 1)">上一页</el-button>
        <span>{{ page }} / {{ pageCount }}</span>
        <el-button size="small" :disabled="page >= pageCount" @click="emit('update:page', page + 1)">下一页</el-button>
      </span>
      <span class="hint">滚轮缩放 · 拖拽平移</span>
    </div>
    <div
      ref="wrap"
      class="canvas-scroll"
      @wheel="onWheel"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointerleave="handleLeave"
    >
      <div class="canvas-stage" :style="{ width: `${displayWidth}px`, height: `${displayHeight}px` }">
        <img
          ref="imageEl"
          class="invoice-image"
          :src="src"
          alt="票据影像"
          draggable="false"
          @load="onImageLoad"
        />
        <canvas
          ref="canvas"
          class="box-canvas"
          @click="onCanvasClick"
          :data-image-size="`${naturalW}×${naturalH}`"
          :data-scale="scaleX.toFixed(6)"
          :data-field-boxes="JSON.stringify(fieldDisplayBoxes.map((item) => ({
            field: item.fieldName,
            box: [Math.round(item.box.x1), Math.round(item.box.y1),
                  Math.round(item.box.x2), Math.round(item.box.y2)],
          })))"
          :data-visible-lines="String(visibleLines)"
          :data-draw-ms="String(drawMs)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.canvas-shell {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.canvas-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.canvas-toolbar .hint,
.canvas-toolbar .zoom-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.canvas-scroll {
  position: relative;
  max-height: 72vh;
  overflow: auto;
  background: #f5f7fa;
  border: 1px solid var(--el-border-color-lighter);
  cursor: grab;
}
.canvas-scroll:active {
  cursor: grabbing;
}
.canvas-stage {
  position: relative;
}
.invoice-image {
  display: block;
  width: 100%;
  height: 100%;
  user-select: none;
  pointer-events: none;
}
.box-canvas {
  position: absolute;
  inset: 0;
}
</style>