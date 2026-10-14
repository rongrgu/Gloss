import { useEffect } from 'react'
import { api, type Course } from './api'
import { Brand, Icon } from './Icon'
import { go, href } from './route'

/** Stand-in for the mind map (M4): every slide as a thumbnail with question / note badges. */
export default function Overview({ course, onRefresh }: { course: Course; onRefresh: () => void }) {
  useEffect(onRefresh, [onRefresh])
  const noted = new Set(course.pages_with_notes)
  const pages = Array.from({ length: course.slide_count }, (_, i) => i + 1)
  return (
    <>
      <header className="bar">
        <a className="btn ghost" href={href({ name: 'home' })} style={{ textDecoration: 'none' }}><Brand /></a>
        <span className="file" title={course.path}>{course.title}</span>
        <span className="spacer" />
        <span className="saved">所有内容自动保存</span>
      </header>
      <div className="intro"><p>点开任意一页 slide。黄色数字是这一页上的提问数，铅笔表示这一页有笔记。</p></div>
      <div className="grid">
        {pages.map((p) => {
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
    </>
  )
}
