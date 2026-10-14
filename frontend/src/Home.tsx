import { useEffect, useRef, useState } from 'react'
import { api, type Course, type RecentItem } from './api'
import { Brand } from './Icon'

export default function Home({ onOpen, notice }: { onOpen: (c: Course) => void; notice?: string }) {
  const [recent, setRecent] = useState<RecentItem[]>([])
  const [health, setHealth] = useState<{ claude_found: boolean; model: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const [path, setPath] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.recent().then(setRecent).catch(() => {})
    api.health().then(setHealth).catch(() => setError('连不上 Gloss 后台。请确认已运行 `uv run gloss`。'))
  }, [])

  const run = async (label: string, f: () => Promise<Course>) => {
    setBusy(label)
    setError(null)
    try {
      onOpen(await f())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }
  const importFile = (file: File | undefined) => {
    if (!file) return
    if (!/\.pdf$/i.test(file.name)) return setError('只支持 PDF 文件。')
    run(`正在导入 ${file.name}…`, () => api.importPdf(file))
  }

  return (
    <>
      <header className="bar"><Brand /><span className="spacer" /><span className="saved">所有内容自动保存在本地</span></header>
      <main className="home">
        <div>
          <h1>把课件变成可以提问的学习空间</h1>
          <p className="lead">导入 PDF slides，在任意一页上框选一块区域提问。讲解、问答和笔记都保存在一个 .gloss 文件里。</p>
        </div>
        {notice && <p className="warn">{notice}</p>}
        {health && !health.claude_found && (
          <p className="warn">没有找到 <code>claude</code> 命令。可以浏览已有内容，但不能生成讲解或提问。请先安装 Claude Code 并登录。</p>
        )}
        <section className="card">
          <h2>导入课件</h2>
          <div
            className={'drop' + (over ? ' over' : '')}
            role="button" tabIndex={0}
            onClick={() => fileRef.current?.click()}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true) }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); importFile(e.dataTransfer.files[0]) }}
          >
            {busy ?? '把 PDF 拖到这里，或点击选择文件'}
          </div>
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden
            onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = '' }} />
        </section>
        {recent.length > 0 && (
          <section className="card">
            <h2>最近打开</h2>
            <ul className="recent">
              {recent.map((r) => (
                <li key={r.path}>
                  <button disabled={!r.exists || !!busy} onClick={() => run('正在打开…', () => api.open(r.path))}
                    title={r.exists ? r.path : '文件已不存在'}>
                    <span>{r.title || '（无标题）'}</span>
                    <span className="p">{r.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className="card">
          <h2>打开已有的 .gloss 文件</h2>
          <form className="inline" onSubmit={(e) => { e.preventDefault(); if (path.trim()) run('正在打开…', () => api.open(path.trim())) }}>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/you/Gloss/lecture-07.gloss" aria-label="文件路径" />
            <button className="btn" disabled={!path.trim() || !!busy}>打开</button>
          </form>
        </section>
        {error && <p className="err" role="alert">{error}</p>}
      </main>
    </>
  )
}
