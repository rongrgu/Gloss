import { describe, expect, it } from 'vitest'
import { ago } from './CatchUp'
import { MESSAGES } from './i18n'

describe('ago', () => {
  const now = Date.parse('2026-11-26T12:00:00Z')
  it('says how long ago in both languages', () => {
    expect(ago('2026-11-26T11:59:30+00:00', MESSAGES.en, now)).toBe('just now')
    expect(ago('2026-11-26T11:30:00+00:00', MESSAGES.en, now)).toBe('30 minutes ago')
    expect(ago('2026-11-26T09:00:00+00:00', MESSAGES.zh, now)).toBe('3 小时前')
    expect(ago('2026-11-25T10:00:00+00:00', MESSAGES.zh, now)).toBe('昨天')
    expect(ago('2026-11-20T12:00:00+00:00', MESSAGES.en, now)).toBe('6 days ago')
    expect(ago('2026-08-01T12:00:00+00:00', MESSAGES.en, now)).toBe('on 2026-08-01')
    expect(ago('', MESSAGES.en, now)).toBe('')
  })
})
