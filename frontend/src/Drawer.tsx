import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './Icon'
import { useT } from './i18n'

type Log = Awaited<ReturnType<typeof api.session>>

/** Session-log drawer ("后台 session"): what this page's Claude session actually received (DESIGN.md §9). */
export function Drawer({ cid, page, open, version, onClose }: {
  cid: string
  page: number
  open: boolean
  version: number // bump to refetch (after an answer completes)
  onClose: () => void
}) {
  const [log, setLog] = useState<Log | null>(null)
  const t = useT()
  useEffect(() => {
    if (!open) return
    let live = true
    api.session(cid, page).then((l) => live && setLog(l)).catch(() => {})
    return () => { live = false }
  }, [cid, page, open, version])

  return (
    <aside className={'drawer' + (open ? ' open' : '')} aria-label={t.drawerLabel} aria-hidden={!open}>
      <div className="dhead">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>{t.drawerTitle(page)}</h2>
          <div className="sid">session_id {log?.session_id ?? t.drawerNoSession}</div>
        </div>
        <button className="iconbtn" aria-label={t.drawerClose} onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className="dlist">
        <p className="dnote">{t.drawerIntro}</p>
        {!log?.messages.length ? (
          <p className="dnote">{t.drawerEmpty}</p>
        ) : (
          <>
            <div className="log ctx"><div className="who">{t.drawerContext}</div>
              <div className="msg">{t.drawerContextMsg(page)}</div></div>
            {log.messages.map((m, i) => {
              if (m.role !== 'user') {
                return (
                  <div key={i} className="log ai"><div className="who">{t.drawerAnswer(m.thread)}</div>
                    <div className="msg">{m.content}</div></div>
                )
              }
              const [tag, ...rest] = m.content.split('\n')
              return (
                <div key={i} className="log user"><div className="who">{t.drawerSent}</div>
                  <div className="tag">{tag}</div><div className="msg">{rest.join('\n')}</div></div>
              )
            })}
          </>
        )}
      </div>
    </aside>
  )
}
