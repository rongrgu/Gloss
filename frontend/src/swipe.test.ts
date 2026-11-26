import { describe, expect, it } from 'vitest'
import { GESTURE_GAP_MS, MAX_NUDGE, SWIPE_THRESHOLD, SwipeTracker } from './swipe'

/** A swipe as a trackpad sends it: `n` events of `dx` px, 16 ms apart, starting at `t0`. */
function swipe(tr: SwipeTracker, dx: number, n: number, t0 = 0, dy = 0) {
  const out = []
  for (let i = 0; i < n; i++) out.push(tr.feed(dx, dy, t0 + i * 16))
  return out
}

describe('trackpad swipe', () => {
  it('fingers left (deltaX > 0) turns to the next slide, right to the previous', () => {
    expect(swipe(new SwipeTracker(), 20, 10).filter((r) => r.turn).map((r) => r.turn)).toEqual(['next'])
    expect(swipe(new SwipeTracker(), -20, 10).filter((r) => r.turn).map((r) => r.turn)).toEqual(['prev'])
  })
  it('one page per gesture: the momentum tail is swallowed', () => {
    const tr = new SwipeTracker()
    const rs = swipe(tr, 30, 40) // a long swipe with inertia, far past the threshold
    expect(rs.filter((r) => r.turn).length).toBe(1)
    expect(rs.every((r) => r.handled)).toBe(true)
    // after a pause, the next swipe turns again
    const again = swipe(tr, 30, 10, 40 * 16 + GESTURE_GAP_MS + 50)
    expect(again.filter((r) => r.turn).map((r) => r.turn)).toEqual(['next'])
  })
  it('small or vertical movement does not turn; vertical scrolling is left to the page', () => {
    const tr = new SwipeTracker()
    expect(swipe(tr, 5, 5).some((r) => r.turn)).toBe(false) // 25 px < threshold
    const v = new SwipeTracker()
    const rs = swipe(v, 4, 20, 0, 30)
    expect(rs.some((r) => r.turn) || rs.some((r) => r.handled)).toBe(false)
  })
  it('pinch-zoom (ctrlKey) is ignored', () => {
    expect(new SwipeTracker().feed(500, 0, 0, true)).toEqual({ turn: null, nudge: 0, handled: false })
  })
  it('the slide follows the fingers a little before turning, opposite to deltaX', () => {
    const r = new SwipeTracker().feed(SWIPE_THRESHOLD / 2, 0, 0)
    expect(r.turn).toBeNull()
    expect(r.nudge).toBeLessThan(0)
    expect(Math.abs(new SwipeTracker().feed(SWIPE_THRESHOLD - 1, 0, 0).nudge)).toBeLessThanOrEqual(MAX_NUDGE)
  })
})
