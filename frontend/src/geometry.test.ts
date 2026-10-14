import { describe, expect, it } from 'vitest'
import { parseSse } from './api'
import {
  DEFAULT_LAYOUT, appendNote, dragRect, fitWidth, gridFor, noteClip, normalizeLayout, placePopup,
} from './geometry'

const stage = { left: 100, top: 100, width: 800, height: 450 }
const vp = { width: 1400, height: 900 }

describe('placePopup', () => {
  it('prefers the right side of the selection', () => {
    const p = placePopup(stage, { x: 0.1, y: 0.2, w: 0.2, h: 0.1 }, 360, 300, vp, 64)
    expect(p).toEqual({ left: 100 + 80 + 160 + 14, top: 100 + 90, width: 360 })
  })
  it('falls back to the left when the right does not fit', () => {
    const p = placePopup(stage, { x: 0.7, y: 0.2, w: 0.3, h: 0.1 }, 360, 300, { width: 1000, height: 900 }, 64)
    expect(p.left).toBe(100 + 560 - 14 - 360)
  })
  it('goes below when neither side fits, above if no room below', () => {
    const narrow = { width: 700, height: 900 }
    const s = { left: 10, top: 80, width: 680, height: 380 }
    const below = placePopup(s, { x: 0.1, y: 0.1, w: 0.8, h: 0.2 }, 360, 300, narrow, 64)
    expect(below.top).toBe(80 + 38 + 76 + 14)
    const above = placePopup(s, { x: 0.1, y: 0.8, w: 0.8, h: 0.2 }, 360, 300, { width: 700, height: 600 }, 64)
    expect(above.top).toBeLessThan(80 + 0.8 * 380)
  })
  it('never goes above the top bar or off the bottom', () => {
    const p = placePopup(stage, { x: 0.1, y: 0, w: 0.1, h: 0.1 }, 360, 300, vp, 250)
    expect(p.top).toBe(250)
    const q = placePopup(stage, { x: 0.1, y: 0.95, w: 0.1, h: 0.05 }, 360, 800, vp, 64)
    expect(q.top).toBe(900 - 800 - 8)
  })
  it('shrinks to the viewport', () => {
    expect(placePopup(stage, { x: 0, y: 0, w: 0.1, h: 0.1 }, 360, 300, { width: 300, height: 600 }, 64).width).toBe(284)
  })
})

describe('layout', () => {
  it('normalizes junk to defaults and clamps fractions', () => {
    expect(normalizeLayout(null)).toEqual(DEFAULT_LAYOUT)
    expect(normalizeLayout({ mode: 'side', stack: { a: 5 }, showNotes: false })).toEqual({
      ...DEFAULT_LAYOUT, mode: 'side', showNotes: false, stack: { a: 0.85, b: 0.58 },
    })
  })
  it('stack mode: slide on top, explain|notes below', () => {
    const g = gridFor(DEFAULT_LAYOUT)
    expect(g.ws.gridTemplateRows).toBe('minmax(0,0.6fr) 12px minmax(0,0.4fr)')
    expect(g.group.gridTemplateColumns).toContain('0.58fr')
    expect([g.splitAAxis, g.splitBAxis]).toEqual(['h', 'v'])
  })
  it('side mode and hidden panels', () => {
    const g = gridFor({ ...DEFAULT_LAYOUT, mode: 'side', showNotes: false })
    expect(g.ws.gridTemplateColumns).toContain('0.6fr')
    expect(g.group.gridTemplateRows).toBe('minmax(0,1fr)')
    expect(g.showSplitB).toBe(false)
    const none = gridFor({ ...DEFAULT_LAYOUT, showNotes: false, showExplain: false })
    expect(none.showGroup).toBe(false)
    expect(none.ws.gridTemplateRows).toBe('minmax(0,1fr)')
  })
  it('fits the slide by height or width', () => {
    expect(fitWidth(1000, 400, 16 / 9)).toBeCloseTo((400 - 42) * 16 / 9)
    expect(fitWidth(500, 2000, 16 / 9)).toBe(484)
  })
})

describe('notes', () => {
  it('clips the first paragraph', () => {
    expect(noteClip(2, '为什么？', '第一段。\n\n第二段。')).toBe('【问题 2】为什么？\n第一段。')
  })
  it('appends with a blank line', () => {
    expect(appendNote('', 'x')).toBe('x')
    expect(appendNote('a\n\n\n', 'x')).toBe('a\n\nx')
  })
})

describe('dragRect', () => {
  it('normalizes direction', () => {
    expect(dragRect({ x: 0.5, y: 0.6 }, { x: 0.2, y: 0.1 })).toEqual({ x: 0.2, y: 0.1, w: 0.3, h: 0.5 })
  })
})

describe('parseSse', () => {
  it('splits complete events and keeps the remainder', () => {
    const out = [...parseSse('event: delta\ndata: {"a":1}\n\nevent: x\ndata: {"b"')]
    expect(out).toHaveLength(1)
    expect(out[0].data).toBe('{"a":1}')
    expect(out[0].rest).toBe('event: x\ndata: {"b"')
  })
})

describe('describeTool', async () => {
  const { describeTool } = await import('./useSlide')
  it('turns tool targets into status text', () => {
    expect(describeTool('page:7')).toBe('正在查看第 7 页…')
    expect(describeTool('search:softmax')).toBe('正在课件里搜索「softmax」…')
    expect(describeTool('course')).toBe('正在查阅整门课的内容…')
    expect(describeTool('files')).toBe('正在查阅课件…')
  })
})
