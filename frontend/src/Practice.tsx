import { api, type PastExam } from './api'
import { Icon } from './Icon'
import { useT } from './i18n'
import { RowMenu } from './RowMenu'

/** Materials › Practice Exam / Quiz (DESIGN.md §13.8): past quizzes and exams in this folder, PDF or Word.
 *  Imported with the library card's "Import exam / quiz" button. */
export function Practice({ exams, onDelete, onRenamed }: {
  exams: PastExam[]; onDelete: (e: PastExam) => void; onRenamed: (e: PastExam) => void
}) {
  const t = useT()
  const rename = async (e: PastExam) => {
    const title = prompt(t.newName, e.title)?.trim()
    if (!title || title === e.title) return
    try { onRenamed(await api.renameExam(e, title)) } catch (err) { alert(err instanceof Error ? err.message : String(err)) }
  }
  return (
    <div className="practice">
      <h3 className="sub">{t.practiceH}</h3>
      {exams.length === 0 ? <p className="tip">{t.pastExamsEmpty}</p> : (
        <ul className="rows">
          {exams.map((e) => (
            <li key={e.id}>
              <a className="row-main" href={api.examUrl(e)} target="_blank" rel="noreferrer">
                <Icon name="doc" className="kind" />
                <span className="row-txt">
                  <span className="row-title">{e.title}</span>
                  <span className="row-meta">{e.kind === 'docx' ? 'Word' : 'PDF'} · {t.examMeta(e.pages)}</span>
                </span>
              </a>
              <RowMenu label={t.actions} items={[
                { label: t.rename, onClick: () => rename(e) },
                { label: t.delete, onClick: () => { if (confirm(t.deleteExamConfirm(e.title))) api.deleteExam(e).then(() => onDelete(e)) } },
              ]} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
