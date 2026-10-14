// Pure layout logic ported from docs/mockup.html. Unit-tested in geometry.test.ts.

export const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v))

export interface Box {
  left: number
  top: number
  width: number
  height: number
}

/**
 * Where to put a floating dialog for a selection on the slide (mockup `place`):
 * right of the selection if it fits, else left, else below (or above if no room below).
 * Always kept inside the viewport and below the top bar.
 */
export function placePopup(
  stage: Box, // stage rect in viewport px
  r: { x: number; y: number; w: number; h: number }, // selection, 0–1 of stage
  boxWidth: number,
  boxHeight: number,
  viewport: { width: number; height: number },
  topLimit: number, // bottom of the top bar + gap
): { left: number; top: number; width: number } {
  const W = viewport.width
  const H = viewport.height
  const bw = Math.min(boxWidth, W - 16)
  const rx = stage.left + r.x * stage.width
  const ry = stage.top + r.y * stage.height
  const rw = r.w * stage.width
  const rh = r.h * stage.height
  let left: number
  let top: number
  if (rx + rw + 14 + bw <= W - 8) {
    left = rx + rw + 14
    top = ry
  } else if (rx - 14 - bw >= 8) {
    left = rx - 14 - bw
    top = ry
  } else {
    left = clamp(rx, 8, W - bw - 8)
    top = ry + rh + 14 + boxHeight <= H - 8 ? ry + rh + 14 : ry - 14 - boxHeight
  }
  top = clamp(top, topLimit, Math.max(topLimit, H - boxHeight - 8))
  return { left, top, width: bw }
}

// --- workspace layout (mockup applyLayout) ---

export interface Layout {
  mode: 'stack' | 'side'
  showExplain: boolean
  showNotes: boolean
  stack: { a: number; b: number }
  side: { a: number; b: number }
}

export const DEFAULT_LAYOUT: Layout = {
  mode: 'stack',
  showExplain: true,
  showNotes: true,
  stack: { a: 0.6, b: 0.58 },
  side: { a: 0.6, b: 0.55 },
}

export const SPLIT_MIN = 0.2
export const SPLIT_MAX = 0.85

/** Accept whatever is stored in ui_state, falling back to defaults field by field. */
export function normalizeLayout(v: unknown): Layout {
  const d = DEFAULT_LAYOUT
  if (!v || typeof v !== 'object') return structuredClone(d)
  const o = v as Partial<Layout>
  const frac = (x: unknown, fb: number) => (typeof x === 'number' && isFinite(x) ? clamp(x, SPLIT_MIN, SPLIT_MAX) : fb)
  const pair = (p: unknown, fb: { a: number; b: number }) => {
    const q = (p && typeof p === 'object' ? p : {}) as { a?: unknown; b?: unknown }
    return { a: frac(q.a, fb.a), b: frac(q.b, fb.b) }
  }
  return {
    mode: o.mode === 'side' ? 'side' : 'stack',
    showExplain: typeof o.showExplain === 'boolean' ? o.showExplain : d.showExplain,
    showNotes: typeof o.showNotes === 'boolean' ? o.showNotes : d.showNotes,
    stack: pair(o.stack, d.stack),
    side: pair(o.side, d.side),
  }
}

export interface GridStyle {
  ws: { gridTemplateRows: string; gridTemplateColumns: string }
  group: { gridTemplateRows: string; gridTemplateColumns: string }
  showGroup: boolean
  showSplitB: boolean
  splitAAxis: 'h' | 'v' // h = horizontal bar (drag up/down)
  splitBAxis: 'h' | 'v'
}

export function gridFor(L: Layout): GridStyle {
  const f = L[L.mode]
  const stack = L.mode === 'stack'
  const showGroup = L.showExplain || L.showNotes
  const both = L.showExplain && L.showNotes
  const main = showGroup ? `minmax(0,${f.a}fr) 12px minmax(0,${1 - f.a}fr)` : 'minmax(0,1fr)'
  const sub = both ? `minmax(0,${f.b}fr) 12px minmax(0,${1 - f.b}fr)` : 'minmax(0,1fr)'
  const one = 'minmax(0,1fr)'
  return {
    ws: stack ? { gridTemplateRows: main, gridTemplateColumns: one } : { gridTemplateColumns: main, gridTemplateRows: one },
    group: stack ? { gridTemplateColumns: sub, gridTemplateRows: one } : { gridTemplateRows: sub, gridTemplateColumns: one },
    showGroup,
    showSplitB: both,
    splitAAxis: stack ? 'h' : 'v',
    splitBAxis: stack ? 'v' : 'h',
  }
}

/** Fit the slide image into its pane, keeping the aspect ratio (mockup fitStage). */
export function fitWidth(paneW: number, paneH: number, aspect: number): number {
  return Math.max(160, Math.min(paneW - 16, (paneH - 42) * aspect))
}

/** Text appended to the notes by "摘到笔记": the question and the first paragraph of the answer. */
export function noteClip(n: number, question: string, answer: string): string {
  const first = answer.trim().split(/\n\s*\n/)[0]
  return `【问题 ${n}】${question}\n${first}`
}

export function appendNote(existing: string, block: string): string {
  return existing.trim() ? existing.trimEnd() + '\n\n' + block : block
}

/** Normalize a drag between two points into a 0–1 rect. */
export function dragRect(a: { x: number; y: number }, b: { x: number; y: number }) {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) }
}

export const MIN_SELECTION = 0.02
