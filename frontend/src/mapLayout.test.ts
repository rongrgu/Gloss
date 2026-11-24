import { describe, expect, it } from 'vitest'
import { layoutMap, TOPIC_COLORS } from './mapLayout'

const topics = [
  { title: 'A', summary: '', slides: [1, 2] },
  { title: 'B', summary: '', slides: [3, 4, 5] },
]

describe('layoutMap', () => {
  it('places root, topics and one thumb per page', () => {
    const { nodes, edges } = layoutMap(topics, new Set(), 16 / 9)
    expect(nodes.filter((n) => n.kind === 'thumb').map((n) => n.page)).toEqual([1, 2, 3, 4, 5])
    expect(nodes.filter((n) => n.kind === 'topic')).toHaveLength(2)
    // root → each topic (curve), topic → first thumb (straight)
    expect(edges.map((e) => [e.source, e.target, e.kind])).toEqual([
      ['root', 'topic-0', 'curve'], ['topic-0', 'page-1', 'straight'],
      ['root', 'topic-1', 'curve'], ['topic-1', 'page-3', 'straight'],
    ])
    expect(edges[2].color).toBe(TOPIC_COLORS[1])
  })

  it('thumbs in a strip are left to right, rows do not overlap', () => {
    const { nodes } = layoutMap(topics, new Set(), 16 / 9)
    const t = nodes.filter((n) => n.kind === 'thumb')
    expect(t[1].x).toBeGreaterThan(t[0].x + t[0].w)
    expect(t[2].y).toBeGreaterThan(t[0].y + t[0].h)
    const tp = nodes.filter((n) => n.kind === 'topic')
    expect(tp[1].y).toBeGreaterThanOrEqual(tp[0].y + tp[0].h)
  })

  it('a topic taller than TOPIC_H (long summary) pushes the next row down', () => {
    const tall = new Map([[0, 160]])
    const { nodes } = layoutMap(topics, new Set(), 16 / 9, tall)
    const [a, b] = nodes.filter((n) => n.kind === 'topic')
    expect(a.h).toBe(160)
    expect(b.y).toBeGreaterThanOrEqual(a.y + a.h)
    // the first row's thumbs stay vertically centred on the taller topic
    const t1 = nodes.find((n) => n.page === 1)!
    expect(t1.y + t1.h / 2).toBeCloseTo(a.y + a.h / 2)
    // measured heights below the floor are ignored
    const small = layoutMap(topics, new Set(), 16 / 9, new Map([[0, 10]]))
    expect(small.nodes.find((n) => n.id === 'topic-0')!.h).toBe(84)
  })

  it('collapsed topics show a single "more" node', () => {
    const { nodes, edges } = layoutMap(topics, new Set([1]), 16 / 9)
    expect(nodes.filter((n) => n.kind === 'thumb').map((n) => n.page)).toEqual([1, 2])
    expect(nodes.find((n) => n.kind === 'more')?.topic).toBe(1)
    expect(edges.at(-1)?.target).toBe('more-1')
  })

  it('thumbnail height follows the slide aspect; root is vertically centred', () => {
    const wide = layoutMap(topics, new Set(), 16 / 9)
    const square = layoutMap(topics, new Set(), 1)
    expect(wide.nodes.find((n) => n.kind === 'thumb')!.h).toBe(74)
    expect(square.nodes.find((n) => n.kind === 'thumb')!.h).toBe(132)
    const root = square.nodes[0]
    expect(root.y + root.h / 2).toBeCloseTo(square.height / 2)
  })
})
