import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Course, type Lecture, type Listing, type ProviderInfo, type RecentItem } from './api'
import { Brand, Icon } from './Icon'
import { LangSwitch, useT, type Messages } from './i18n'
import { QuizPanel } from './QuizPanel'
import { ProfilePanel } from './ProfilePanel'
import { href, rememberPath } from './route'

type Drag = { path: string; kind: 'lecture' | 'folder' }

const parentOf = (p: string) => p.split('/').slice(0, -1).join('/')
const inside = (p: string, dir: string) => p === dir || p.startsWith(dir + '/')

/**
 * Home: a browser over the library folder tree (DESIGN.md §13.2). The root also shows the
 * intro, recent files and "open a file". Courses are folders that hold lectures (.gloss);
 * groups are any folders above them.
 */
export default function Home({ folder, onOpen, notice }: { folder: string; onOpen: (c: Course) => void; notice?: string }) {
  const t = useT()
  const root = folder === ''
  const [listing, setListing] = useState<Listing | null>(null)
  const [recent, setRecent] = useState<RecentItem[]>([])
  const [health, setHealth] = useState<ProviderInfo | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const [path, setPath] = useState('')
  const [moving, setMoving] = useState<{ path: string; name: string } | null>(null)
  const [target, setTarget] = useState<string | null>(null) // 'folder:<path>' | 'at:<index>'
  const drag = useRef<Drag | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(() => {
    api.library(folder).then(setListing).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [folder])

  useEffect(() => {
    reload()
    if (root) api.recent().then(setRecent).catch(() => {})
    api.health().then(setHealth).catch(() => setError(t.backendDown))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload])

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
  const act = async (f: () => Promise<unknown>) => {
    setError(null)
    try {
      await f()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    reload()
  }
  const importFile = (file: File | undefined) => {
    if (!file) return
    if (!/\.pdf$/i.test(file.name)) return setError(t.pdfOnly)
    run(t.importing(file.name), () => api.importPdf(file, folder))
  }

  /** After a lecture moves, keep old links to it working (route.ts remembers id -> path). */
  const remember = (l: Lecture | undefined, newPath: string) => {
    if (l?.course_id && listing) rememberPath(l.course_id, `${listing.root}/${newPath}`)
  }
  const lectureAt = (p: string) => listing?.lectures.find((l) => l.path === p)
  const move = (p: string, to: string) => act(async () => remember(lectureAt(p), (await api.move(p, to)).path))
  const rename = (p: string, current: string) => {
    const name = prompt(t.newName, current)?.trim()
    if (name && name !== current) act(async () => remember(lectureAt(p), (await api.rename(p, name)).path))
  }
  const reorder = (p: string, before: string | null) =>
    api.reorder(p, before).then(setListing).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  const newFolder = () => {
    const name = prompt(t.folderName)?.trim()
    if (name) act(() => api.mkdir(folder, name))
  }

  // --- drag and drop: PDFs from the desktop import here; lectures reorder or move into folders ---
  const endDrag = () => { drag.current = null; setTarget(null) }
  const canMoveInto = (dir: string) => {
    const d = drag.current
    return !!d && parentOf(d.path) !== dir && !(d.kind === 'folder' && inside(dir, d.path))
  }
  const folderDrop = (dir: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!canMoveInto(dir)) return
      e.preventDefault()
      setTarget('folder:' + dir)
    },
    onDragLeave: () => setTarget((x) => (x === 'folder:' + dir ? null : x)),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const d = drag.current
      const ok = canMoveInto(dir) // before endDrag clears the drag
      endDrag()
      if (d && ok) move(d.path, dir)
    },
  })
  const lectures = listing?.lectures ?? []
  const lectureDrop = (i: number) => ({
    onDragOver: (e: React.DragEvent) => {
      if (drag.current?.kind !== 'lecture' || parentOf(drag.current.path) !== folder) return
      e.preventDefault()
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
      setTarget('at:' + (e.clientY < r.top + r.height / 2 ? i : i + 1))
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const d = drag.current
      const at = target?.startsWith('at:') ? Number(target.slice(3)) : null
      endDrag()
      if (!d || at === null) return
      const from = lectures.findIndex((l) => l.path === d.path)
      if (at === from || at === from + 1) return
      reorder(d.path, lectures[at]?.path ?? null)
    },
  })
  const pageDrop = {
    onDragOver: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes('Files')) return
      e.preventDefault()
      setOver(true)
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) setOver(false)
    },
    onDrop: (e: React.DragEvent) => {
      if (!e.dataTransfer.files.length) return
      e.preventDefault()
      setOver(false)
      importFile(e.dataTransfer.files[0])
    },
  }
  const dragStart = (d: Drag) => (e: React.DragEvent) => {
    drag.current = d
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', d.path)
  }

  const here = listing?.name || t.library
  return (
    <>
      <header className="bar"><Brand /><span className="spacer" /><span className="saved wide-only">{t.savedLocally}</span><LangSwitch /></header>
      <main className={'home' + (over ? ' over' : '')} {...pageDrop}>
        {root && (
          <div>
            <h1>{t.heroTitle}</h1>
            <p className="lead">{t.heroLead}</p>
          </div>
        )}
        {notice && <p className="warn">{notice}</p>}
        {health && <ProviderBar info={health} onChange={setHealth} />}

        <section className="card lib">
          <div className="lib-head">
            <nav className="crumbs" aria-label={t.library}>
              {(listing?.crumbs ?? [{ name: '', path: '' }]).map((c, i, all) => (
                <span key={c.path} className="crumb-item">
                  {i > 0 && <Icon name="next" className="sep" />}
                  {i === all.length - 1 ? (
                    <span className={'here' + (target === 'folder:' + c.path ? ' drop-into' : '')} aria-current="page">{c.name || t.library}</span>
                  ) : (
                    <a href={href({ name: 'home', folder: c.path })}
                      className={target === 'folder:' + c.path ? 'drop-into' : undefined} {...folderDrop(c.path)}>
                      {c.name || t.library}
                    </a>
                  )}
                </span>
              ))}
            </nav>
            <span className="spacer" />
            <button className="btn" onClick={newFolder}><Icon name="plus" />{t.newFolder}</button>
            <button className="btn primary" disabled={!!busy} onClick={() => fileRef.current?.click()}>{t.importPdf}</button>
            <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden
              onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = '' }} />
          </div>

          {listing && listing.mixed && <p className="tip">{t.mixedNote}</p>}

          {listing && listing.folders.length > 0 && (
            <ul className="rows">
              {listing.folders.map((f) => (
                <li key={f.path} className={target === 'folder:' + f.path ? 'drop-into' : undefined}
                  draggable onDragStart={dragStart({ path: f.path, kind: 'folder' })} onDragEnd={endDrag} {...folderDrop(f.path)}>
                  <a className="row-main" href={href({ name: 'home', folder: f.path })}>
                    <Icon name="folder" className={'kind ' + f.kind} />
                    <span className="row-txt">
                      <span className="row-title">{f.name}</span>
                      <span className="row-meta">{kindLabel(t, f)}</span>
                    </span>
                  </a>
                  <RowMenu label={t.actions} items={[
                    { label: t.rename, onClick: () => rename(f.path, f.name) },
                    { label: t.moveTo, onClick: () => setMoving({ path: f.path, name: f.name }) },
                    { label: t.deleteFolder, disabled: f.kind !== 'empty',
                      onClick: () => confirm(t.deleteFolderConfirm(f.name)) && act(() => api.rmdir(f.path)) },
                  ]} />
                </li>
              ))}
            </ul>
          )}

          {lectures.length > 0 && (
            <>
              <h2 className="sub">{t.lecturesH}</h2>
              <ol className="rows lectures">
                {lectures.map((l, i) => (
                  <li key={l.path} {...lectureDrop(i)} onDragEnd={endDrag}
                    className={(target === 'at:' + i ? 'ins-before' : '') + (target === 'at:' + (i + 1) && i === lectures.length - 1 ? ' ins-after' : '')}>
                    <span className="grip" draggable onDragStart={dragStart({ path: l.path, kind: 'lecture' })}
                      title={t.reorderHint}><Icon name="grip" /></span>
                    <button className="row-main" disabled={!!busy || !!l.error} title={l.error ? t.unreadable : l.file}
                      onClick={() => run(t.opening, () => api.open(l.file))}
                      draggable onDragStart={dragStart({ path: l.path, kind: 'lecture' })}>
                      <span className="num">{i + 1}</span>
                      <span className="row-txt">
                        <span className="row-title">{l.title || t.untitled}</span>
                        <span className="row-meta">
                          {l.error ? t.unreadable : t.lectureMeta(l.slide_count, l.questions)}
                          {!l.error && l.status !== 'new' && <span className={'status ' + l.status}>{t.statusName[l.status]}</span>}
                          {!!l.new_since_digest && <span className="status fresh">{t.newSinceDigest(l.new_since_digest)}</span>}
                        </span>
                      </span>
                    </button>
                    <RowMenu label={t.actions} items={[
                      { label: t.moveUp, disabled: i === 0, onClick: () => reorder(l.path, lectures[i - 1].path) },
                      { label: t.moveDown, disabled: i === lectures.length - 1,
                        onClick: () => reorder(l.path, lectures[i + 2]?.path ?? null) },
                      { label: t.rename, onClick: () => rename(l.path, l.path.split('/').pop()!.replace(/\.gloss$/, '')) },
                      { label: t.moveTo, onClick: () => setMoving({ path: l.path, name: l.title }) },
                    ]} />
                  </li>
                ))}
              </ol>
              {lectures.length > 1 && <p className="tip">{t.reorderHint}</p>}
            </>
          )}

          {listing && !listing.folders.length && !lectures.length && (
            <div className={'drop' + (over ? ' over' : '')} role="button" tabIndex={0}
              onClick={() => fileRef.current?.click()}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && fileRef.current?.click()}>
              {busy ?? (root ? t.dropHint : t.emptyFolder)}
            </div>
          )}
          {(listing?.folders.length || lectures.length) ? (
            <p className={'drop-line' + (over ? ' over' : '')}>{busy ?? t.importInto(here)}</p>
          ) : null}
        </section>

        {listing && (listing.lectures.length > 0 || listing.has_study) && <ProfilePanel key={'p' + listing.path} listing={listing} />}
        {listing && listing.kind !== 'empty' && <QuizPanel key={listing.path} listing={listing} />}

        {root && recent.length > 0 && (
          <section className="card">
            <h2>{t.recent}</h2>
            <ul className="recent">
              {recent.map((r) => (
                <li key={r.path}>
                  <button disabled={!r.exists || !!busy} onClick={() => run(t.opening, () => api.open(r.path))}
                    title={r.exists ? r.path : t.fileMissing}>
                    <span>{r.title || t.untitled}</span>
                    <span className="p">{r.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {root && (
          <section className="card">
            <h2>{t.openExisting}</h2>
            <form className="inline" onSubmit={(e) => { e.preventDefault(); if (path.trim()) run(t.opening, () => api.open(path.trim())) }}>
              <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/you/Gloss/lecture-07.gloss" aria-label={t.filePath} />
              <button className="btn" disabled={!path.trim() || !!busy}>{t.open}</button>
            </form>
          </section>
        )}
        {error && <p className="err" role="alert">{error}</p>}
      </main>
      {moving && (
        <MoveDialog item={moving} onClose={() => setMoving(null)}
          onPick={(to) => { const p = moving.path; setMoving(null); move(p, to) }} />
      )}
    </>
  )
}

const LABEL: Record<string, string> = { claude: 'Claude Code', codex: 'Codex', anthropic_api: 'Anthropic API',
  openai_api: 'OpenAI API' }

/** Which AI answers (§6.2): pick one when both CLIs are installed, say so when none is, warn if not logged in. */
function ProviderBar({ info, onChange }: { info: ProviderInfo; onChange: (p: ProviderInfo) => void }) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pick = async (name: string) => {
    setBusy(true)
    setError(null)
    try { onChange(await api.setProvider(name)) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    setBusy(false)
  }
  if (info.provider_source === 'none') {
    return <p className="warn" role="alert">{t.noCli}</p>
  }
  if (info.needs_choice) {
    return (
      <section className="card pick-ai">
        <h2>{t.chooseAi}</h2>
        <p className="tip">{t.chooseAiLead}</p>
        <div className="ai-options">
          {info.available.map((name) => (
            <button key={name} className="btn ai" disabled={busy} onClick={() => pick(name)}>
              <b>{LABEL[name]}</b><span className="row-meta">{t.aiPlan[name]}</span>
            </button>
          ))}
        </div>
        {error && <p className="err">{error}</p>}
      </section>
    )
  }
  const name = info.provider ?? ''
  const other = info.available.find((n) => n !== name)
  return (
    <>
      {info.logged_in === false && (
        <p className="warn">{name === 'codex' ? t.notLoggedInCodex : name.endsWith('_api') ? t.noApiKey(LABEL[name]) : t.notLoggedInClaude}</p>
      )}
      <p className="ai-line row-meta">
        {t.usingAi(LABEL[name] ?? name)}
        {info.provider_source !== 'config' && other && (
          <> · <button className="linkbtn" disabled={busy} onClick={() => pick(other)}>{t.switchAi(LABEL[other])}</button></>
        )}
        {error && <span className="err"> {error}</span>}
      </p>
    </>
  )
}

function kindLabel(t: Messages, f: Listing['folders'][number]) {
  if (f.kind === 'course') return t.kindCourse(f.lectures)
  if (f.kind === 'group') return t.kindGroup(f.folders)
  return t.kindEmpty
}

type MenuItem = { label: string; onClick: () => void; disabled?: boolean }

function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])
  return (
    <div className="menu row-menu" ref={ref}>
      <button className="btn ghost icon-btn" aria-label={label} title={label} aria-expanded={open}
        onClick={() => setOpen((o) => !o)}><Icon name="more" /></button>
      {open && (
        <div className="menu-pop list" role="menu">
          {items.map((it) => (
            <button key={it.label} role="menuitem" className="btn ghost" disabled={it.disabled}
              onClick={() => { setOpen(false); it.onClick() }}>{it.label}</button>
          ))}
        </div>
      )}
    </div>
  )
}

function MoveDialog({ item, onPick, onClose }: { item: { path: string; name: string }; onPick: (to: string) => void; onClose: () => void }) {
  const t = useT()
  const [tree, setTree] = useState<{ path: string; depth: number }[] | null>(null)
  useEffect(() => {
    api.libraryTree().then(setTree).catch(() => setTree([]))
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const isFolder = !item.path.endsWith('.gloss')
  return (
    <div className="modal-back" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={t.moveTitle(item.name)}>
        <h2>{t.moveTitle(item.name)}</h2>
        {!tree && <p className="tip">{t.opening}</p>}
        <ul className="pick">
          {(tree ?? []).map((f) => {
            const invalid = f.path === parentOf(item.path) || (isFolder && inside(f.path, item.path))
            return (
              <li key={f.path}>
                <button className="btn ghost" disabled={invalid} style={{ paddingLeft: 12 + f.depth * 18 }}
                  onClick={() => onPick(f.path)}>
                  <Icon name="folder" />{f.path ? f.path.split('/').pop() : t.library}
                </button>
              </li>
            )
          })}
        </ul>
        <div className="dialog-actions">
          <button className="btn" onClick={onClose}>{t.cancel}</button>
        </div>
      </div>
    </div>
  )
}

