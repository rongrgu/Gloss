// Two-finger horizontal swipe on a trackpad -> previous / next slide (like switching desktops on macOS).
// Trackpads report the gesture as `wheel` events with deltaX; with natural scrolling, fingers moving left
// give deltaX > 0, which means "next". Unit-tested in swipe.test.ts.
import { useEffect, useRef } from 'react'

export const SWIPE_THRESHOLD = 90 // px of horizontal travel before the page turns
export const GESTURE_GAP_MS = 250 // a pause this long ends one gesture (and its momentum tail)
export const MAX_NUDGE = 48 // px the slide follows the fingers, for feedback

export type SwipeResult = { turn: 'next' | 'prev' | null; nudge: number; handled: boolean }

export class SwipeTracker {
  private acc = 0
  private last = -Infinity
  private fired = false

  /** One wheel event. `handled` means it belonged to a horizontal gesture (the caller should preventDefault). */
  feed(dx: number, dy: number, now: number, pinch = false): SwipeResult {
    const gap = now - this.last
    if (gap > GESTURE_GAP_MS) {
      this.acc = 0
      this.fired = false
    }
    const horizontal = Math.abs(dx) > Math.abs(dy) * 1.5
    const inGesture = gap <= GESTURE_GAP_MS && (this.acc !== 0 || this.fired)
    if (pinch || (!horizontal && !inGesture)) return { turn: null, nudge: 0, handled: false }
    this.last = now
    if (this.fired) return { turn: null, nudge: 0, handled: true } // momentum after the turn: swallow it
    this.acc += dx
    if (Math.abs(this.acc) >= SWIPE_THRESHOLD) {
      const turn = this.acc > 0 ? 'next' : 'prev'
      this.acc = 0
      this.fired = true
      return { turn, nudge: 0, handled: true }
    }
    return { turn: null, nudge: Math.max(-MAX_NUDGE, Math.min(MAX_NUDGE, -this.acc * 0.5)), handled: true }
  }
}

/** Turn pages with horizontal trackpad swipes over `area`; `follow` (the slide) moves a little with the fingers. */
export function useSwipePages(area: React.RefObject<HTMLElement | null>, follow: React.RefObject<HTMLElement | null>,
  onTurn: (dir: 'next' | 'prev') => void) {
  // One tracker for the life of the pane: turning the page must not reset it mid-gesture, or the rest of the
  // same swipe (its momentum) would count as a new one and skip a page.
  const turn = useRef(onTurn)
  turn.current = onTurn
  useEffect(() => {
    const el = area.current
    if (!el) return
    const tracker = new SwipeTracker()
    let settle: number | undefined
    const nudge = (px: number, animate: boolean) => {
      const f = follow.current
      if (!f) return
      f.style.transition = animate ? 'transform .18s ease-out' : 'none'
      f.style.transform = px ? `translateX(${px}px)` : ''
    }
    const onWheel = (e: WheelEvent) => {
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? el.clientWidth : 1 // lines / pages -> px
      const r = tracker.feed(e.deltaX * unit, e.deltaY * unit, e.timeStamp || performance.now(), e.ctrlKey)
      if (!r.handled) return
      e.preventDefault() // also stops the browser's swipe-to-go-back over the slide
      nudge(r.nudge, false)
      clearTimeout(settle)
      settle = window.setTimeout(() => nudge(0, true), 120)
      if (r.turn) {
        nudge(0, true)
        turn.current(r.turn)
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', onWheel)
      clearTimeout(settle)
      nudge(0, false)
    }
  }, [area, follow])
}
