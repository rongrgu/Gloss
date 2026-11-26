import { describe, expect, it } from 'vitest'
import type { Quiz, QuizQuestion } from './api'
import { MESSAGES } from './i18n'
import { describeQuizTool, mcqCorrect, quizProgress, resumeIndex, summarize } from './quizLogic'

const question = (n: number, topic: string, correct: 0 | 1 | null | undefined): QuizQuestion => ({
  id: `q${n}`, n, type: 'mcq', stem: 's', options: ['a', 'b', 'c'], answer: 'B', explanation: '', why: '', topic,
  difficulty: 2, sources: [], review: null,
  attempt: correct === undefined ? null : { answer: 'A', correct, created_at: '' },
})
const quiz = (qs: QuizQuestion[]): Quiz => ({ id: 'x', title: 't', created_at: '', model: null,
  options: { count: qs.length, types: ['mcq'], focus: 'weak' }, questions: qs })

describe('quiz logic', () => {
  it('resumes at the first unanswered question', () => {
    expect(resumeIndex(quiz([question(1, 'a', 1), question(2, 'a', undefined)]))).toBe(1)
    expect(resumeIndex(quiz([question(1, 'a', 1)]))).toBe(0)
  })
  it('checks multiple-choice answers case-insensitively', () => {
    expect(mcqCorrect(question(1, 'a', undefined), 'b')).toBe(true)
    expect(mcqCorrect(question(1, 'a', undefined), 'A')).toBe(false)
  })
  it('summarizes by topic, weakest first', () => {
    const s = summarize(quiz([question(1, 'attn', 1), question(2, 'attn', 1), question(3, 'norm', 0),
      question(4, 'norm', null), question(5, 'fp16', undefined)]))
    expect([s.right, s.wrong.map((q) => q.n), s.unmarked, s.total]).toEqual([2, [3], 2, 5])
    expect(s.byTopic.map((x) => [x.topic, x.right, x.total])).toEqual([['norm', 0, 2], ['fp16', 0, 1], ['attn', 2, 2]])
  })
  it('describes generation progress in both languages', () => {
    const ev = [{ type: 'phase', phase: 'generate', attempt: 1 }, { type: 'tool', target: 'slide:L2:7' }]
    expect(quizProgress(ev, MESSAGES.en)).toBe('Gloss is writing the questions… Reading L2 slide 7')
    expect(quizProgress(ev, MESSAGES.zh)).toBe('Gloss 正在出题… 正在看 L2 第 7 页')
    expect(quizProgress([...ev, { type: 'phase', phase: 'verify' }], MESSAGES.en)).toBe('Checking each answer against the slides…')
    expect(quizProgress([{ type: 'phase', phase: 'generate', attempt: 2 }], MESSAGES.en)).toMatch(/rewriting/)
    expect(describeQuizTool('search:a:b', MESSAGES.en)).toBe('search results for “a:b”')
    expect(describeQuizTool('unknown', MESSAGES.en)).toBe('')
  })
})

describe('past exam progress', () => {
  it('names the exam and page', () => {
    expect(describeQuizTool('exam:E1:2', MESSAGES.en)).toBe('past exam E1, page 2')
    expect(describeQuizTool('exam:E1:', MESSAGES.zh)).toBe(' 往年试卷 E1')
    expect(describeQuizTool('list_past_exams', MESSAGES.en)).toBe('the past exams')
  })
})
