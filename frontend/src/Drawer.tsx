import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './Icon'

type Log = Awaited<ReturnType<typeof api.session>>

/** "后台 session" drawer: what this page's Claude session actually received (DESIGN.md §9). */
export function Drawer({ cid, page, open, version, onClose }: {
  cid: string
  page: number
  open: boolean
  version: number // bump to refetch (after an answer completes)
  onClose: () => void
}) {
  const [log, setLog] = useState<Log | null>(null)
  useEffect(() => {
    if (!open) return
    let live = true
    api.session(cid, page).then((l) => live && setLog(l)).catch(() => {})
    return () => { live = false }
  }, [cid, page, open, version])

  return (
    <aside className={'drawer' + (open ? ' open' : '')} aria-label="后台 session 日志" aria-hidden={!open}>
      <div className="dhead">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>第 {page} 页的 Claude session</h2>
          <div className="sid">session_id {log?.session_id ?? '（还没有）'}</div>
        </div>
        <button className="iconbtn" aria-label="关闭 session 日志" onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className="dlist">
        <p className="dnote">在界面上，每个问题是一个独立的对话框；在后台，这一页的所有问答按时间顺序进入同一个 Claude session。每条消息发出前都会加上 thread 标签，Claude 才知道哪句追问属于哪个问题。</p>
        {!log?.messages.length ? (
          <p className="dnote">这一页还没有提问。在 slide 上框选一块区域试试。</p>
        ) : (
          <>
            <div className="log ctx"><div className="who">上下文</div>
              <div className="msg">第 {page} 页的图片、文字和讲解，在 session 开头发送一次。</div></div>
            {log.messages.map((m, i) => {
              if (m.role !== 'user') {
                return (
                  <div key={i} className="log ai"><div className="who">Claude 的回答（显示在问题 {m.thread} 的对话框里）</div>
                    <div className="msg">{m.content}</div></div>
                )
              }
              const [tag, ...rest] = m.content.split('\n')
              return (
                <div key={i} className="log user"><div className="who">发给 Claude</div>
                  <div className="tag">{tag}</div><div className="msg">{rest.join('\n')}</div></div>
              )
            })}
          </>
        )}
      </div>
    </aside>
  )
}
