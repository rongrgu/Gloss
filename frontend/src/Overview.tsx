import { useCallback, useEffect, useState } from 'react'
import { api, followJob, type Course, type JobInfo } from './api'
import { Brand, Icon } from './Icon'
import { MindMap } from './MindMap'
import { go, href } from './route'

type TreeState = { status: 'ready' | 'building' } | { status: 'error'; error: string }

/**
 * Course overview: the mind map once the tree exists. The first time a course is
 * opened the tree is generated (one Claude call); meanwhile a plain thumbnail grid is shown.
 */
export default function Overview({ course, onRefresh }: { course: Course; onRefresh: () => void }) {
  useEffect(onRefresh, [onRefresh])
  const [st, setSt] = useState<TreeState>({ status: course.tree ? 'ready' : 'building' })

  const follow = useCallback((job: JobInfo, signal?: AbortSignal) => {
    setSt({ status: 'building' })
    followJob(job.id, (ev) => {
      if (ev.type === 'done') { setSt({ status: 'ready' }); onRefresh() }
      if (ev.type === 'error') setSt({ status: 'error', error: ev.message })
    }, signal).catch((e) => !signal?.aborted && setSt({ status: 'error', error: String(e.message ?? e) }))
  }, [onRefresh])

  const build = useCallback(async (regenerate: boolean, signal?: AbortSignal) => {
    setSt({ status: 'building' })
    try {
      const r = await api.buildTree(course.id, regenerate)
      if (r.job) follow(r.job, signal)
      else { setSt({ status: 'ready' }); onRefresh() }
    } catch (e) {
      setSt({ status: 'error', error: e instanceof Error ? e.message : String(e) })
    }
  }, [course.id, follow, onRefresh])

  const hasTree = !!course.tree
  useEffect(() => {
    if (hasTree) return
    const ctrl = new AbortController()
    if (course.tree_job) follow(course.tree_job, ctrl.signal)
    else build(false, ctrl.signal)
    return () => ctrl.abort()
    // Only on first open of a tree-less course.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [course.id, hasTree])

  const showMap = hasTree && st.status !== 'building'
  return (
    <div id="app" className="full">
      <header className="bar">
        <a className="btn ghost" href={href({ name: 'home' })} style={{ textDecoration: 'none' }}><Brand /></a>
        <span className="file" title={course.path}>{course.title}</span>
        <span className="spacer" />
        <span className="saved wide-only">所有内容自动保存</span>
        <a className="btn ghost" href={api.exportUrl(course.id)} download style={{ textDecoration: 'none' }}
          title="把思维导图、讲解、问答和笔记导出成一个 Markdown 文件">导出 Markdown</a>
        {hasTree && (
          <button className="btn ghost" disabled={st.status === 'building'}
            onClick={() => confirm('让 Claude 重新整理思维导图？现有的主题划分会被替换（问答和笔记不受影响）。')
              && api.putUi(course.id, 'collapsed', []).then(() => build(true))}>
            <Icon name="refresh" />重新生成导图
          </button>
        )}
      </header>
      <div className="intro">
        {st.status === 'building' ? (
          <p className="warn">Claude 正在整理这节课的结构，生成后这里会变成思维导图。可以先点开任意一页。</p>
        ) : st.status === 'error' ? (
          <p className="merr">思维导图生成失败：{st.error}<button className="btn" onClick={() => build(hasTree)}>重试</button></p>
        ) : (
          <p>点开任意一页 slide。黄色数字是这一页上的提问数，铅笔表示这一页有笔记。拖动画布平移，滚轮或右下角按钮缩放。</p>
        )}
      </div>
      {showMap ? (
        <div className="map-flow"><MindMap key={JSON.stringify(course.tree)} course={course} /></div>
      ) : (
        <ThumbGrid course={course} />
      )}
    </div>
  )
}

function ThumbGrid({ course }: { course: Course }) {
  const noted = new Set(course.pages_with_notes)
  return (
    <div className="grid">
      {Array.from({ length: course.slide_count }, (_, i) => i + 1).map((p) => {
        const n = course.question_counts[String(p)] ?? 0
        return (
          <button key={p} className="thumb" onClick={() => go({ name: 'slide', cid: course.id, page: p })}
            aria-label={`第 ${p} 页${n ? `，${n} 个问题` : ''}${noted.has(p) ? '，有笔记' : ''}`}>
            <img src={api.imageUrl(course.id, p)} alt="" loading="lazy" />
            <span className="pg">{p}</span>
            {noted.has(p) && <span className="noted"><Icon name="pen" /></span>}
            {n > 0 && <span className="badge">{n}</span>}
          </button>
        )
      })}
    </div>
  )
}
