// @vitest-environment happy-dom
import { Editor } from '@tiptap/core'
import { describe, expect, it } from 'vitest'
import { sheetExtensions } from './sheetEditor'

const roundTrip = (md: string) => {
  const ed = new Editor({ extensions: sheetExtensions(), content: md, contentType: 'markdown' })
  const out = ed.getMarkdown()
  ed.destroy()
  return out
}

const SHEET = `# ML Cheatsheet

## Scaled dot-product

$$
\\mathrm{Attn}(Q,K,V)=\\mathrm{softmax}\\left(\\frac{QK^T}{\\sqrt{d}}\\right)V
$$

- **Why** $\\sqrt{d}$ (not $d$): variance of $q\\cdot k$ is $d$.
- ⚠ Mask goes **before** softmax.
  - nested point

1. first
2. second

| Term | Meaning |
| --- | --- |
| $d_k$ | head width |
| $h$ | heads |

Costs $5 and $6 are not math.`

describe('cheatsheet editor markdown', () => {
  it('keeps headings, lists, bold, formulas and tables through a round trip', () => {
    const out = roundTrip(SHEET)
    for (const piece of ['# ML Cheatsheet', '## Scaled dot-product', '$$\n\\mathrm{Attn}(Q,K,V)', '**Why** $\\sqrt{d}$ (not $d$)',
      '- ⚠ Mask goes **before** softmax.', 'nested point', '1. first', '| $d_k$ | head width |', 'Costs $5 and $6']) {
      expect(out).toContain(piece)
    }
  })
  it('is stable: a second round trip changes nothing', () => {
    const once = roundTrip(SHEET)
    expect(roundTrip(once)).toBe(once)
  })
  it('parses formulas as formula nodes', () => {
    const ed = new Editor({ extensions: sheetExtensions(), content: 'a $x^2$ b\n\n$$\ny\n$$', contentType: 'markdown' })
    const types: string[] = []
    ed.state.doc.descendants((n) => { types.push(n.type.name) })
    expect(types).toContain('inlineMath')
    expect(types).toContain('blockMath')
    ed.destroy()
  })
})
