import '@xyflow/react/dist/style.css'
import {
  Background, BackgroundVariant, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps,
  type ReactFlowInstance, type Viewport,
} from '@xyflow/react'
import { createContext, memo, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Course } from './api'
import { Icon } from './Icon'
import { layoutMap, THUMB_W } from './mapLayout'
import { go } from './route'

interface Ctx {
  course: Course
  noted: Set<number>
  collapsed: Set<number>
  toggle: (topic: number) => void
}
const MapCtx = createContext<Ctx>(null!)

type Data = { topic?: number; page?: number; color?: string; h?: number }

const hidden = { opacity: 0, pointerEvents: 'none' as const, border: 0, width: 1, height: 1, minWidth: 0, minHeight: 0 }

const RootNode = memo(function RootNode() {
  const { course } = useContext(MapCtx)
  const tree = course.tree!
  return (
    <div className="root" style={{ position: 'relative', boxShadow: 'var(--shadow-2)' }}>
      <h1>{tree.title || course.title}</h1>
      {tree.subtitle && <p>{tree.subtitle}</p>}
      <p>{course.slide_count} 页，{tree.children.length} 个主题</p>
      <Handle type="source" position={Position.Right} style={hidden} isConnectable={false} />
    </div>
  )
})

const TopicNode = memo(function TopicNode({ data }: NodeProps<Node<Data>>) {
  const { course, collapsed, toggle } = useContext(MapCtx)
  const t = course.tree!.children[data.topic!]
  const open = !collapsed.has(data.topic!)
  return (
    <button className="snode" aria-expanded={open} onClick={() => toggle(data.topic!)}>
      <Handle type="target" position={Position.Left} style={hidden} isConnectable={false} />
      <span className="dot" style={{ background: data.color }} />
      <span className="txt"><span className="t">{t.title}</span>{t.summary && <span className="s">{t.summary}</span>}</span>
      <Icon name="chev" className="chev" />
      <Handle type="source" position={Position.Right} style={hidden} isConnectable={false} />
    </button>
  )
})

const ThumbNode = memo(function ThumbNode({ data }: NodeProps<Node<Data>>) {
  const { course, noted } = useContext(MapCtx)
  const p = data.page!
  const n = course.question_counts[String(p)] ?? 0
  return (
    <button className="thumb" onClick={() => go({ name: 'slide', cid: course.id, page: p })}
      aria-label={`第 ${p} 页${n ? `，${n} 个问题` : ''}${noted.has(p) ? '，有笔记' : ''}`}>
      <Handle type="target" position={Position.Left} style={hidden} isConnectable={false} />
      <img src={api.imageUrl(course.id, p)} alt="" loading="lazy" style={{ height: data.h, objectFit: 'cover' }} />
      <span className="pg">{p}</span>
      {noted.has(p) && <span className="noted"><Icon name="pen" /></span>}
      {n > 0 && <span className="badge">{n}</span>}
    </button>
  )
})

const MoreNode = memo(function MoreNode({ data }: NodeProps<Node<Data>>) {
  const { course, toggle } = useContext(MapCtx)
  return (
    <button className="more" onClick={() => toggle(data.topic!)}>
      <Handle type="target" position={Position.Left} style={hidden} isConnectable={false} />
      展开 {course.tree!.children[data.topic!].slides.length} 页
    </button>
  )
})

const nodeTypes = { root: RootNode, topic: TopicNode, thumb: ThumbNode, more: MoreNode }

/** The mind map (DESIGN.md §9). Pan / zoom only: nodes are not draggable. */
export function MindMap({ course }: { course: Course }) {
  const tree = course.tree!
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())
  const [ready, setReady] = useState(false)
  const flow = useRef<ReactFlowInstance<Node<Data>, Edge> | null>(null)
  const saved = useRef<Viewport | null>(null)
  const saveTimer = useRef<number>(undefined)

  // Collapsed topics and the viewport are part of the file's ui_state.
  useEffect(() => {
    Promise.all([
      api.getUi<number[]>(course.id, 'collapsed').catch(() => ({ value: null })),
      api.getUi<Viewport>(course.id, 'map_view').catch(() => ({ value: null })),
    ]).then(([c, v]) => {
      const n = tree.children.length
      setCollapsed(new Set((c.value ?? []).filter((i) => Number.isInteger(i) && i >= 0 && i < n)))
      // A viewport saved on a wide screen is meaningless on a phone: fit instead.
      saved.current = window.matchMedia('(max-width:760px)').matches ? null : v.value
      setReady(true)
    })
  }, [course.id, tree.children.length])

  const toggle = (i: number) => setCollapsed((cur) => {
    const next = new Set(cur)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    api.putUi(course.id, 'collapsed', [...next]).catch(() => {})
    return next
  })

  const { nodes, edges } = useMemo(() => {
    const L = layoutMap(tree.children, collapsed, course.aspect)
    const nodes: Node<Data>[] = L.nodes.map((n) => ({
      id: n.id, type: n.kind, position: { x: n.x, y: n.y },
      data: { topic: n.topic, page: n.page, color: n.color, h: n.kind === 'thumb' ? n.h : undefined },
      style: n.kind === 'thumb' ? { width: THUMB_W } : undefined,
    }))
    const edges: Edge[] = L.edges.map((e) => ({
      id: e.id, source: e.source, target: e.target, type: e.kind === 'curve' ? 'default' : 'straight',
      style: { stroke: e.color, strokeOpacity: 0.55, strokeWidth: 1.6 }, focusable: false,
    }))
    return { nodes, edges }
  }, [tree, collapsed, course.aspect])

  const ctx = useMemo(() => ({ course, noted: new Set(course.pages_with_notes), collapsed, toggle }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [course, collapsed])

  if (!ready) return null
  return (
    <MapCtx.Provider value={ctx}>
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
        nodesFocusable={false} edgesFocusable={false}
        minZoom={0.2} maxZoom={2}
        defaultViewport={saved.current ?? undefined}
        fitView={!saved.current} fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
        onInit={(f) => { flow.current = f }}
        onMoveEnd={(_, vp) => {
          clearTimeout(saveTimer.current)
          saveTimer.current = window.setTimeout(() => api.putUi(course.id, 'map_view', vp).catch(() => {}), 400)
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} />
        <Controls showInteractive={false} fitViewOptions={{ padding: 0.08, maxZoom: 1 }} />
      </ReactFlow>
    </MapCtx.Provider>
  )
}
