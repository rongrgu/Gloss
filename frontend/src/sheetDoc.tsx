// The cheatsheet document as an editable, auto-saving Word-like editor (DESIGN.md §16.4, §16.8), shared by the
// cheatsheet page and the panel next to a quiz (§16.9): loading, saving with its guards, formulas, toolbar.

import { useEditor, type Editor } from '@tiptap/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Sheet } from './api'
import type { Messages } from './i18n'
import { Markdown } from './Markdown'
import { MATH_EDIT, sheetExtensions, tidyMarkdown, type MathEditDetail } from './sheetEditor'

const SAVE_AFTER_MS = 1500

export type MathDialog = { pos: number | null; latex: string; block: boolean } // pos null: inserting a new formula

export function useSheetDoc({ folder, sid, onSelection, onError }: {
  folder: string
  sid: string
  onSelection?: (text: string) => void
  onError: (message: string) => void
}) {
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [content, setContent] = useState('') // the Markdown, as saved or as last typed
  const [saving, setSaving] = useState<'idle' | 'pending' | 'saved'>('idle')
  const [math, setMath] = useState<MathDialog | null>(null)
  const timer = useRef<number | null>(null)
  const pendingText = useRef<string | null>(null)
  const baseRef = useRef<number | null>(null) // the version the editor is on
  const loaded = useRef<string | null>(null) // the editor's Markdown right after loading; null until the sheet is in
  const errorRef = useRef(onError)
  errorRef.current = onError
  const selectionRef = useRef(onSelection)
  selectionRef.current = onSelection

  // --- saving: 1.5 s after typing stops; consecutive edits share one version (§16.2) ---
  const flush = useCallback(async () => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null }
    const text = pendingText.current
    if (text === null || baseRef.current === null) return
    pendingText.current = null
    try {
      const r = await api.saveSheet(folder, sid, text, baseRef.current)
      baseRef.current = r.version
      setSaving('saved')
      setSheet((s) => s && { ...s, title: r.title || s.title, version: r.version })
    } catch (e) {
      errorRef.current(e instanceof Error ? e.message : String(e))
    }
  }, [folder, sid])
  const onType = useCallback((md: string) => {
    setContent(md)
    pendingText.current = md
    setSaving('pending')
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { flush() }, SAVE_AFTER_MS)
  }, [flush])

  const editor = useEditor({
    extensions: sheetExtensions(),
    content: '',
    contentType: 'markdown',
    shouldRerenderOnTransaction: true, // the toolbar shows what is active at the cursor
    editorProps: { attributes: { class: 'md sheet-md', spellcheck: 'false' } },
    onUpdate: ({ editor: ed }) => {
      // Only the student's own edits are saved. Not: anything before the sheet has loaded (the editor starts
      // empty), "updates" from switching read-only on or off, or the editor's own fix-ups after loading
      // (tables get normalized), which leave the Markdown as it was loaded.
      if (loaded.current === null || !ed.isEditable) return
      const md = tidyMarkdown(ed.getMarkdown())
      if (md !== loaded.current) onType(md)
    },
    onSelectionUpdate: ({ editor: ed }) => {
      const { from, to, empty } = ed.state.selection
      selectionRef.current?.(empty ? '' : ed.state.doc.textBetween(from, to, '\n', ' ').trim().slice(0, 2000))
    },
  })

  /** Show `md` in the editor without counting it as a change. */
  const load = useCallback((md: string) => {
    // The editor React gives us first can be replaced right after mounting; a load that arrives for the old,
    // destroyed one is skipped (the effect below runs again for the new one).
    if (!editor || editor.isDestroyed) return
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null }
    pendingText.current = null // whatever was waiting to be saved belongs to the content being replaced
    editor.commands.setContent(md, { contentType: 'markdown', emitUpdate: false })
    loaded.current = tidyMarkdown(editor.getMarkdown())
  }, [editor])

  const apply = useCallback((s: Sheet) => {
    setSheet(s)
    setContent(s.content)
    baseRef.current = s.version
    load(s.content)
  }, [load])
  const reload = useCallback(() => api.sheet(folder, sid).then((s) => { apply(s); return s }), [folder, sid, apply])

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (pendingText.current !== null) e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])
  useEffect(() => {
    const open = (e: Event) => {
      if (!editor?.isEditable) return
      const d = (e as CustomEvent<MathEditDetail>).detail
      setMath({ pos: d.pos, latex: d.latex, block: d.block })
    }
    window.addEventListener(MATH_EDIT, open)
    return () => window.removeEventListener(MATH_EDIT, open)
  }, [editor])

  /** The formula dialog's result: insert, change, or (empty) delete. */
  const saveFormula = useCallback((latex: string, block: boolean) => {
    const m = math
    setMath(null)
    if (!m || !editor || editor.isDestroyed) return
    if (m.pos === null) {
      if (latex.trim()) editor.chain().focus().insertContent({ type: block ? 'blockMath' : 'inlineMath', attrs: { latex } }).run()
      return
    }
    const pos = m.pos
    editor.chain().focus().command(({ tr }) => {
      const node = tr.doc.nodeAt(pos)
      if (!node) return false
      if (!latex.trim()) tr.delete(pos, pos + node.nodeSize)
      else tr.setNodeMarkup(pos, undefined, { ...node.attrs, latex })
      return true
    }).run()
  }, [editor, math])

  /** Read-only while Gloss is revising, an old version is shown, the source is open… (editable is the default). */
  const setEditable = useCallback((on: boolean) => {
    if (editor && !editor.isDestroyed && editor.isEditable !== on) editor.setEditable(on)
  }, [editor])

  return { editor, sheet, setSheet, content, saving, flush, load, apply, reload, onType, math, setMath, saveFormula, setEditable }
}

/** Word-style formatting buttons. */
export function FormatBar({ editor, t, onFormula, compact = false }: {
  editor: Editor; t: Messages; onFormula: () => void; compact?: boolean
}) {
  const level = editor.isActive('heading', { level: 1 }) ? 1 : editor.isActive('heading', { level: 2 }) ? 2
    : editor.isActive('heading', { level: 3 }) ? 3 : 0
  const inTable = editor.isActive('table')
  const btn = (label: string, title: string, active: boolean, run: () => void, cls = '') => (
    <button type="button" className={'fmt ' + cls} aria-pressed={active} title={title} aria-label={title}
      onMouseDown={(e) => e.preventDefault()} onClick={run}>{label}</button>
  )
  const c = () => editor.chain().focus()
  return (
    <div className={'fmt-bar' + (compact ? ' compact' : '')} role="toolbar" aria-label={t.formatting}>
      <select value={level} aria-label={t.textStyle}
        onChange={(e) => {
          const v = Number(e.target.value)
          if (v === 0) c().setParagraph().run()
          else c().setHeading({ level: v as 1 | 2 | 3 }).run()
        }}>
        <option value={0}>{t.fmtParagraph}</option>
        <option value={1}>{t.fmtTitle}</option>
        <option value={2}>{t.fmtHeading}</option>
        <option value={3}>{t.fmtSubheading}</option>
      </select>
      <span className="fmt-sep" />
      {btn('B', t.fmtBold, editor.isActive('bold'), () => c().toggleBold().run(), 'b')}
      {btn('I', t.fmtItalic, editor.isActive('italic'), () => c().toggleItalic().run(), 'i')}
      <span className="fmt-sep" />
      {btn('•', t.fmtBullets, editor.isActive('bulletList'), () => c().toggleBulletList().run())}
      {btn('1.', t.fmtNumbers, editor.isActive('orderedList'), () => c().toggleOrderedList().run())}
      <span className="fmt-sep" />
      {btn('∑', t.insertFormula, false, onFormula)}
      {btn('▦', t.insertTable, false, () => c().insertTable({ rows: 3, cols: 2, withHeaderRow: true }).run())}
      {inTable && <>
        <span className="fmt-sep" />
        {btn(t.addRow, t.addRow, false, () => c().addRowAfter().run(), 'txt')}
        {btn(t.addCol, t.addCol, false, () => c().addColumnAfter().run(), 'txt')}
        {btn(t.delRow, t.delRow, false, () => c().deleteRow().run(), 'txt')}
        {btn(t.delCol, t.delCol, false, () => c().deleteColumn().run(), 'txt')}
        {btn(t.delTable, t.delTable, false, () => c().deleteTable().run(), 'txt')}
      </>}
      <span className="spacer" />
      {btn('↶', t.undoTyping, false, () => c().undo().run())}
      {btn('↷', t.redoTyping, false, () => c().redo().run())}
    </div>
  )
}

export function FormulaDialog({ t, value, onClose, onSave }: {
  t: Messages; value: MathDialog; onClose: () => void; onSave: (latex: string, block: boolean) => void
}) {
  const [latex, setLatex] = useState(value.latex)
  const [block, setBlock] = useState(value.block)
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  return (
    <div className="modal-back" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog formula-dialog" role="dialog" aria-modal="true" aria-label={t.formulaTitle}
        onSubmit={(e) => { e.preventDefault(); onSave(latex, block) }}>
        <h2>{value.pos === null ? t.insertFormula : t.formulaTitle}</h2>
        <label className="field">
          <span className="lbl">{t.formulaHelp}</span>
          <textarea rows={3} value={latex} autoFocus spellCheck={false} onChange={(e) => setLatex(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSave(latex, block) } }} />
        </label>
        <div className="formula-preview">{latex.trim() ? <Markdown text={`$$${latex}$$`} /> : <span className="row-meta">{t.formulaEmpty}</span>}</div>
        {value.pos === null && (
          <label className="check"><input type="checkbox" checked={block} onChange={(e) => setBlock(e.target.checked)} />{t.formulaBlock}</label>
        )}
        <div className="dialog-actions">
          {value.pos !== null && <button type="button" className="btn ghost" onClick={() => onSave('', block)}>{t.delete}</button>}
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>{t.cancel}</button>
          <button className="btn primary">{t.save}</button>
        </div>
      </form>
    </div>
  )
}
