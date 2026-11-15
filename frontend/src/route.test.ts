import { describe, expect, it } from 'vitest'
import { href, parseHash } from './route'

describe('folder routes', () => {
  it('root and nested folders round-trip, including spaces and slashes in names', () => {
    for (const folder of ['', 'CMU', 'CMU/Fall 2026/11-768 LLM Systems', 'a#b/c?d']) {
      const r = { name: 'home' as const, folder }
      expect(parseHash(href(r))).toEqual(r)
    }
    expect(href({ name: 'home', folder: '' })).toBe('#/')
    expect(parseHash('')).toEqual({ name: 'home', folder: '' })
  })
  it('course routes are unchanged', () => {
    expect(parseHash('#/c/abc/p/3')).toEqual({ name: 'slide', cid: 'abc', page: 3 })
    expect(parseHash('#/c/abc')).toEqual({ name: 'overview', cid: 'abc' })
  })
})
