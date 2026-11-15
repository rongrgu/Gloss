// Pure helpers for the pre-quiz screens (DESIGN.md §13.6). Unit-tested in quizLogic.test.ts.
import type { Quiz, QuizQuestion } from './api'
import type { Messages } from './i18n'

export const LETTERS = 'ABCDE'

/** Where to resume: the first question without an attempt, or the first one. */
export function resumeIndex(quiz: Quiz): number {
  const i = quiz.questions.findIndex((q) => !q.attempt)
  return i === -1 ? 0 : i
}

export function mcqCorrect(q: QuizQuestion, letter: string): boolean {
  return q.answer.trim().toUpperCase() === letter.toUpperCase()
}

export interface TopicStat {
  topic: string
  right: number
  total: number
}

export function summarize(quiz: Quiz) {
  const right = quiz.questions.filter((q) => q.attempt?.correct === 1)
  const wrong = quiz.questions.filter((q) => q.attempt?.correct === 0)
  const unmarked = quiz.questions.filter((q) => !q.attempt || q.attempt.correct === null)
  const topics = new Map<string, TopicStat>()
  for (const q of quiz.questions) {
    const s = topics.get(q.topic) ?? { topic: q.topic, right: 0, total: 0 }
    s.total += 1
    if (q.attempt?.correct === 1) s.right += 1
    topics.set(q.topic, s)
  }
  // weakest topics first
  const byTopic = [...topics.values()].sort((a, b) => a.right / a.total - b.right / b.total || b.total - a.total)
  return { right: right.length, wrong, unmarked: unmarked.length, total: quiz.questions.length, byTopic }
}

/** One line of progress while a quiz is generated, from the latest job events. */
export function quizProgress(
  events: { type: string; phase?: string; attempt?: number; target?: string }[], t: Messages,
): string {
  let status = t.quizGenerating
  let tool = ''
  for (const e of events) {
    if (e.type === 'phase') {
      status = e.phase === 'verify' ? t.quizVerifying : (e.attempt ?? 1) > 1 ? t.quizRetry : t.quizGenerating
      tool = ''
    } else if (e.type === 'tool' && e.target) tool = describeQuizTool(e.target, t)
  }
  return tool ? `${status} ${t.quizReading(tool)}` : status
}

export function describeQuizTool(target: string, t: Messages): string {
  const [kind, a = '', b = ''] = target.split(':')
  switch (kind) {
    case 'slide': return t.toolSlide(a, b)
    case 'search': return t.quizToolSearch(target.slice('search:'.length))
    case 'questions': return t.toolQuestions(a)
    case 'notes': return t.toolNotes(a)
    case 'outline': return t.toolOutline(a)
    case 'list_lectures': return t.toolLectures
    case 'study_profile': return t.toolProfile
    default: return ''
  }
}
