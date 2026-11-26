import { EditorContent } from '@tiptap/react'
import { useEffect, useState } from 'react'
import { api } from './api'
import { Icon } from './Icon'
import { useT } from './i18n'
import { href } from './route'
import { FormatBar, FormulaDialog, useSheetDoc } from './sheetDoc'

/** A cheatsheet open next to a quiz (DESIGN.md §16.9), editable while answering. Loaded on demand. */
export default function QuizSheet({ folder, sid, onClose }: { folder: string; sid: string; onClose: () => void }) {
  const t = useT()
  const [error, setError] = useState<string | null>(null)
  const { editor, sheet, saving, apply, math, setMath, saveFormula } = useSheetDoc({ folder, sid, onError: setError })

  useEffect(() => {
    if (!editor) return
    api.sheet(folder, sid).then(apply).catch((e) => setError(e?.status === 404 ? t.sheetNotFound : String(e.message ?? e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, sid, editor])

  return (
    <aside className="quiz-sheet" aria-label={t.sheetsH}>
      <div className="qs-head">
        <b className="qs-title">{sheet?.title ?? t.opening}</b>
        <span className="spacer" />
        {saving !== 'idle' && <span className="row-meta">{saving === 'pending' ? t.saving : t.savedShort}</span>}
        <a className="btn ghost" href={href({ name: 'sheet', folder, sid })} target="_blank" rel="noreferrer" title={t.openSheetTitle}>
          {t.openSheet}
        </a>
        <button className="btn ghost icon-btn" aria-label={t.close} title={t.close} onClick={onClose}><Icon name="close" /></button>
      </div>
      {error && <p className="err" role="alert">{error}</p>}
      {editor && sheet && <FormatBar editor={editor} t={t} compact onFormula={() => setMath({ pos: null, latex: '', block: false })} />}
      <div className="sheet-doc live"><EditorContent editor={editor} /></div>
      {math && <FormulaDialog t={t} value={math} onClose={() => setMath(null)} onSave={saveFormula} />}
    </aside>
  )
}
