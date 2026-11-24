import { describe, expect, it } from 'vitest'
import { glossLink, href, parseHash } from './route'

describe('folder routes', () => {
  it('root and nested folders round-trip, including spaces and slashes in names', () => {
    for (const folder of ['', 'CMU', 'CMU/Fall 2026/11-768 LLM Systems', 'a#b/c?d']) {
      const r = { name: 'home' as const, folder }
      expect(parseHash(href(r))).toEqual(r)
    }
    expect(href({ name: 'home', folder: '' })).toBe('#/')
    expect(parseHash('')).toEqual({ name: 'home', folder: '' })
  })
  it('summary and thread routes round-trip; gloss: links map to slides', () => {
    for (const r of [{ name: 'digest' as const, cid: 'abc' },
      { name: 'slide' as const, cid: 'abc', page: 4, thread: '3f9a0b' }]) expect(parseHash(href(r))).toEqual(r)
    expect(glossLink('gloss:abc/16/3f9a0b')).toEqual({ name: 'slide', cid: 'abc', page: 16, thread: '3f9a0b' })
    expect(glossLink('gloss:abc/2')).toEqual({ name: 'slide', cid: 'abc', page: 2 })
    expect(glossLink('https://example.com')).toBeNull()
  })
  it('course routes are unchanged', () => {
    expect(parseHash('#/c/abc/p/3')).toEqual({ name: 'slide', cid: 'abc', page: 3 })
    expect(parseHash('#/c/abc')).toEqual({ name: 'overview', cid: 'abc' })
  })
})
