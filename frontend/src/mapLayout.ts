// Mind-map geometry (DESIGN.md §9): root → topic column → strip of slide thumbnails.
// Dimensions follow docs/mockup.html (.root, .snode, .thumb, gaps).

import type { TreeTopic } from './api'

export const ROOT_W = 228
export const ROOT_H = 150
export const TOPIC_W = 268
export const TOPIC_H = 84
export const THUMB_W = 132
export const MORE_W = 104
export const MORE_H = 40
const ROOT_GAP = 88
const STRIP_GAP = 46
const THUMB_GAP = 12
const ROW_GAP = 18

// Topic colours from the mockup, then repeated.
export const TOPIC_COLORS = ['#2F6FDE', '#0F8A7E', '#B7791F', '#7C4DDB', '#C2410C', '#0E7490', '#64748B',
  '#BE185D', '#4D7C0F']

export interface MapNode {
  id: string
  kind: 'root' | 'topic' | 'thumb' | 'more'
  x: number
  y: number
  w: number
  h: number
  topic?: number // index into tree.children
  page?: number
  color?: string
}

export interface MapEdge {
  id: string
  source: string
  target: string
  kind: 'curve' | 'straight'
  color: string
}

export function topicId(i: number) {
  return `topic-${i}`
}

export function layoutMap(topics: TreeTopic[], collapsed: Set<number>, aspect: number) {
  const thumbH = Math.round(THUMB_W / (aspect > 0 ? aspect : 16 / 9))
  const nodes: MapNode[] = []
  const edges: MapEdge[] = []
  const topicX = ROOT_W + ROOT_GAP
  const stripX = topicX + TOPIC_W + STRIP_GAP

  let y = 0
  topics.forEach((t, i) => {
    const color = TOPIC_COLORS[i % TOPIC_COLORS.length]
    const isCollapsed = collapsed.has(i)
    const rowH = Math.max(TOPIC_H, isCollapsed ? MORE_H : thumbH)
    const tid = topicId(i)
    nodes.push({ id: tid, kind: 'topic', x: topicX, y: y + (rowH - TOPIC_H) / 2, w: TOPIC_W, h: TOPIC_H, topic: i, color })
    edges.push({ id: `e-root-${i}`, source: 'root', target: tid, kind: 'curve', color })
    if (isCollapsed) {
      const id = `more-${i}`
      nodes.push({ id, kind: 'more', x: stripX, y: y + (rowH - MORE_H) / 2, w: MORE_W, h: MORE_H, topic: i, color })
      edges.push({ id: `e-${tid}-more`, source: tid, target: id, kind: 'straight', color })
    } else {
      t.slides.forEach((p, j) => {
        const id = `page-${p}`
        nodes.push({ id, kind: 'thumb', x: stripX + j * (THUMB_W + THUMB_GAP), y: y + (rowH - thumbH) / 2,
          w: THUMB_W, h: thumbH, topic: i, page: p, color })
        if (j === 0) edges.push({ id: `e-${tid}-strip`, source: tid, target: id, kind: 'straight', color })
      })
    }
    y += rowH + ROW_GAP
  })
  const total = Math.max(0, y - ROW_GAP)
  nodes.unshift({ id: 'root', kind: 'root', x: 0, y: Math.max(0, total / 2 - ROOT_H / 2), w: ROOT_W, h: ROOT_H })
  return { nodes, edges, height: total }
}
