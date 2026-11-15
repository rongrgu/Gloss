// Interface language (DESIGN.md §9.3): one dictionary per locale; `en` must have the same keys as `zh`.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from './api'

export type Locale = 'zh' | 'en'

const zh = {
  // shared
  cancel: '取消',
  retry: '重试',
  delete: '删除',
  open: '打开',
  opening: '正在打开…',
  explanation: '讲解',
  notes: '笔记',
  autosaved: '所有内容自动保存',
  page: (p: number | string) => `第 ${p} 页`,
  pageAria: (p: number, n: number, noted: boolean) => `第 ${p} 页${n ? `，${n} 个问题` : ''}${noted ? '，有笔记' : ''}`,
  switchTo: 'EN',
  switchLabel: 'Switch to English',

  // home
  backendDown: '连不上 Gloss 后台。请确认已运行 `uv run gloss`。',
  pdfOnly: '只支持 PDF 文件。',
  importing: (name: string) => `正在导入 ${name}…`,
  savedLocally: '所有内容自动保存在本地',
  heroTitle: '把课件变成可以提问的学习空间',
  heroLead: '导入 PDF slides，在任意一页上框选一块区域提问。讲解、问答和笔记都保存在一个 .gloss 文件里。',
  noClaude: ['没有找到 ', ' 命令。可以浏览已有内容，但不能生成讲解或提问。请先安装 Claude Code 并登录。'],
  notLoggedIn: ['Claude Code 还没有登录。在终端运行 ', ' 并用 /login 登录后，才能生成讲解和提问。'],
  dropHint: '把 PDF 拖到这里，或点击选择文件',
  recent: '最近打开',
  fileMissing: '文件已不存在',
  untitled: '（无标题）',
  openExisting: '打开已有的 .gloss 文件',
  filePath: '文件路径',
  courseNotOpen: '这门课当前没有打开。请从首页的“最近打开”重新打开。',

  // library (§13.2)
  library: '课件库',
  newFolder: '新建文件夹',
  folderName: '文件夹名称',
  importPdf: '导入 PDF',
  importInto: (name: string) => `把 PDF 拖到这里，导入到「${name}」`,
  foldersH: '文件夹',
  lecturesH: '讲次',
  kindCourse: (n: number) => `课程 · ${n} 讲`,
  kindGroup: (n: number) => `${n} 个文件夹`,
  kindEmpty: '空文件夹',
  lectureMeta: (slides: number, q: number) => `${slides} 页${q ? ` · ${q} 个问题` : ''}`,
  mixedNote: '这个课程文件夹里还有子文件夹。对这个文件夹出 pre-quiz 时，子文件夹里的讲次也会算进去。',
  emptyFolder: '这个文件夹是空的。把 PDF 拖进来导入，或者新建子文件夹。',
  reorderHint: '拖动可以调整讲次顺序；拖到文件夹上可以移进去。',
  actions: '更多操作',
  rename: '重命名',
  newName: '新名称',
  moveTo: '移动到…',
  moveUp: '上移',
  moveDown: '下移',
  deleteFolder: '删除文件夹',
  deleteFolderConfirm: (name: string) => `删除空文件夹「${name}」？`,
  moveTitle: (name: string) => `把「${name}」移动到`,
  unreadable: '无法读取这个文件',

  // pre-quiz (§13.5–13.6)
  preQuiz: '考前自测',
  newQuiz: '出一份自测',
  quizMeta: (n: number, answered: number, correct: number) => `${n} 题 · 已做 ${answered} · 答对 ${correct}`,
  quizCount: '题数',
  quizTypes: '题型',
  typeName: { mcq: '选择题', short: '简答题', derivation: '推导 / 计算' } as Record<string, string>,
  quizFocus: '侧重',
  focusWeak: ['卡住过的地方', '大约 70% 考你问过、追问过、记过笔记的地方'] as [string, string],
  focusBalanced: ['均匀覆盖', '每一讲的主要主题都考到'] as [string, string],
  focusUncovered: ['还没碰过的', '主要考你还没问过的主题'] as [string, string],
  quizLectures: '考哪几讲',
  quizCost: '出题要读很多页，比一次提问消耗更多额度，通常要一分钟左右。可以先离开这个页面，出好后会出现在列表里。',
  generate: '开始出题',
  quizGenerating: 'Claude 正在出题…',
  quizRetry: '有几道题没通过检查，正在重出…',
  quizVerifying: '正在对照课件核对答案…',
  quizReading: (what: string) => `正在看${what}`,
  toolSlide: (l: string, p: string) => ` ${l} 第 ${p} 页`,
  quizToolSearch: (q: string) => `：搜索「${q}」`,
  toolQuestions: (l: string) => ` ${l} 的问答`,
  toolNotes: (l: string) => ` ${l} 的笔记`,
  toolOutline: (l: string) => ` ${l} 的结构`,
  toolLectures: '讲次列表',
  toolProfile: '你的学习记录',
  quizFailed: '出题失败：',
  quizShort: (n: number, req: number) => `只有 ${n} 道题通过了检查（要求 ${req} 道）。`,
  deleteQuizConfirm: (title: string) => `删除自测「${title}」和作答记录？`,
  quizNotFound: '找不到这份自测，可能已经删除了。',
  questionOf: (i: number, n: number) => `第 ${i} / ${n} 题`,
  difficultyName: ['容易', '中等', '难'],
  needsReview: '核对答案时发现可能有问题：',
  yourAnswer: '你的答案（只给自己看，不会自动批改）',
  showAnswer: '看答案',
  referenceAnswer: '参考答案',
  explanationH: '解析',
  whyH: '为什么考这道',
  markRight: '我答对了',
  markWrong: '我答错了',
  correctMsg: '答对了',
  wrongMsg: (letter: string) => `答错了，正确答案是 ${letter}`,
  markedRight: '标记为答对',
  markedWrong: '标记为答错',
  sourcesH: '出处',
  openSource: (label: string, title: string, page: number) => `${label} · ${title} · 第 ${page} 页`,
  sourceMissing: '这一讲已经不在这个文件夹里了',
  prevQ: '上一题',
  nextQ: '下一题',
  finish: '看结果',
  resultsH: '结果',
  score: (c: number, n: number) => `答对 ${c} / ${n}`,
  unmarkedCount: (n: number) => `${n} 道没有标对错`,
  byTopic: '按知识点',
  toReview: '要复习的题',
  allRight: '全部答对。',
  startOver: '重新做一遍',
  backToFolder: '返回文件夹',

  // overview & mind map
  exportMd: '导出 Markdown',
  exportMdTitle: '把思维导图、讲解、问答和笔记导出成一个 Markdown 文件',
  regenMapConfirm: '让 Claude 重新整理思维导图？现有的主题划分会被替换（问答和笔记不受影响）。',
  regenMap: '重新生成导图',
  mapBuilding: 'Claude 正在整理这节课的结构，生成后这里会变成思维导图。可以先点开任意一页。',
  mapFailed: '思维导图生成失败：',
  mapHelp: '点开任意一页 slide。黄色数字是这一页上的提问数，铅笔表示这一页有笔记。拖动画布平移，滚轮或右下角按钮缩放。',
  mapStats: (pages: number, topics: number) => `${pages} 页，${topics} 个主题`,
  expandPages: (n: number) => `展开 ${n} 页`,

  // transcript
  uploadTranscript: '上传 transcript',
  uploadTranscriptTitle: '上传老师讲课的文字稿（.txt / .vtt / .srt），讲解和问答会参考对应的段落',
  transcriptPages: (a: number, n: number) => `Transcript · ${a}/${n} 页`,
  transcriptInfo: (name: string, a: number) =>
    `${name}：已对齐到 ${a} 页。之后生成的讲解和新的问答会参考对应的段落；已有的讲解可以在讲解面板里"重新生成"。`,
  realign: '重新对齐',
  replaceFile: '换一个文件',
  deleteTranscriptConfirm: '删除 transcript 和所有对齐结果？已生成的讲解不受影响。',
  aligning: 'Claude 正在把 transcript 对齐到每一页…',
  alignFailed: 'transcript 对齐失败：',
  aligned: (n: number) => `transcript 已对齐到 ${n} 页。`,

  // workspace
  map: '导图',
  prevPage: '上一页',
  nextPage: '下一页',
  sessionDrawer: '后台 session',
  selectHint: '在 slide 上拖动框选任意区域，比如一个公式，就能针对它提问',
  slideAlt: (p: number, title: string) => `第 ${p} 页 slide：${title}`,
  resizeSlide: '调整 slide 和下方区域的大小',
  resizePanels: '调整讲解和笔记的大小',
  generatedBy: (model: string) => `由 ${model} 生成`,
  noTranscript: '没有上传 transcript，讲解只基于 slide 内容',
  hasSpan: '这一页有对应的 transcript 段落',
  noSpan: 'transcript 里没有对应这一页的内容',
  hideSpan: '收起 transcript',
  showSpan: 'transcript 片段',
  regenerate: '重新生成',
  explainQuestions: '关于讲解的提问',
  notesFor: (p: number) => `第 ${p} 页的笔记`,
  notesPlaceholder: '这一页的笔记。可以自己写，也可以从问答里把回答摘过来。',
  askAboutThis: '就这段提问',
  clipped: (p: number) => `已摘到第 ${p} 页的笔记`,
  explainQueued: '排队中，马上开始生成讲解…',
  explainRunning: 'Claude 正在生成这一页的讲解…',
  explainFailed: '讲解生成失败：',
  explainNone: '这一页的讲解还没有生成。讲解在第一次打开时生成，之后保存在文件里。',
  explainGenerate: '生成讲解',
  noteSaved: '已保存',
  noteSaving: '正在保存…',
  noteFailed: '保存失败，请检查后台是否在运行',

  // layout menu
  layout: '布局',
  arrange: '排列',
  stack: '上下',
  side: '左右',
  show: '显示',
  layoutTip: '拖动区域之间的分隔线可以调整大小，双击分隔线恢复这一处的默认比例。',
  resetLayout: '恢复默认布局',

  // questions
  askQuote: '关于选中的这段讲解，你想问什么？',
  askRegion: '关于框选的这块，你想问什么？',
  askPlaceholder: '比如：这个公式里的求和范围是什么意思？',
  ask: '提问',
  newQuestion: '新问题',
  question: (n: number) => `问题 ${n}`,
  openQuestion: (n: number, title: string) => `打开问题 ${n}：${title}`,
  answering: 'Claude 正在回答…',
  queued: (n: number | null) =>
    `排队中：这一页的 session 正在回答${n ? `问题 ${n}` : '另一个问题'}，同一个 session 一次只处理一条消息。`,
  deleteQuestion: '删除这个问题',
  deleteQuestionConfirm: (n: number) => `删除问题 ${n} 和它的全部回答？`,
  minimize: '缩小',
  minimizeQuestion: '缩小这个问题',
  clipToNotes: '摘到笔记',
  answerFailed: '回答失败：',
  followUpWait: '等这条回答完成后再追问…',
  followUp: '继续追问…',
  followUpIn: (n: number) => `在问题 ${n} 里继续追问`,
  sendFollowUp: '发送追问',
  noteClip: (n: number, q: string) => `【问题 ${n}】${q}`,
  toolPage: (p: string) => `正在查看第 ${p} 页…`,
  toolSearch: (q: string) => `正在课件里搜索「${q}」…`,
  toolCourse: '正在查阅整门课的内容…',
  toolFiles: '正在查阅课件…',

  // session drawer
  drawerLabel: '后台 session 日志',
  drawerTitle: (p: number) => `第 ${p} 页的 Claude session`,
  drawerNoSession: '（还没有）',
  drawerClose: '关闭 session 日志',
  drawerIntro: '在界面上，每个问题是一个独立的对话框；在后台，这一页的所有问答按时间顺序进入同一个 Claude session。每条消息发出前都会加上 thread 标签，Claude 才知道哪句追问属于哪个问题。',
  drawerEmpty: '这一页还没有提问。在 slide 上框选一块区域试试。',
  drawerContext: '上下文',
  drawerContextMsg: (p: number) => `第 ${p} 页的图片、文字和讲解，在 session 开头发送一次。`,
  drawerAnswer: (n: number) => `Claude 的回答（显示在问题 ${n} 的对话框里）`,
  drawerSent: '发给 Claude',
}

export type Messages = typeof zh

const en: Messages = {
  cancel: 'Cancel',
  retry: 'Retry',
  delete: 'Delete',
  open: 'Open',
  opening: 'Opening…',
  explanation: 'Explanation',
  notes: 'Notes',
  autosaved: 'Everything saves automatically',
  page: (p) => `Slide ${p}`,
  pageAria: (p, n, noted) => `Slide ${p}${n ? `, ${n} question${n > 1 ? 's' : ''}` : ''}${noted ? ', has notes' : ''}`,
  switchTo: '中文',
  switchLabel: '切换到中文',

  backendDown: "Can't reach the Gloss backend. Make sure `uv run gloss` is running.",
  pdfOnly: 'Only PDF files are supported.',
  importing: (name) => `Importing ${name}…`,
  savedLocally: 'Everything is saved locally',
  heroTitle: 'Turn lecture slides into a study space you can ask questions in',
  heroLead: 'Import PDF slides and select any region on a slide to ask about it. Explanations, Q&A and notes are all kept in one .gloss file.',
  noClaude: ['The ', " command wasn't found. You can browse existing courses, but not generate explanations or ask questions. Install Claude Code and log in first."],
  notLoggedIn: ["Claude Code isn't logged in. Run ", ' in a terminal and log in with /login to generate explanations and ask questions.'],
  dropHint: 'Drop a PDF here, or click to choose a file',
  recent: 'Recent',
  fileMissing: 'File no longer exists',
  untitled: '(untitled)',
  openExisting: 'Open an existing .gloss file',
  filePath: 'File path',
  courseNotOpen: 'This course is not open right now. Reopen it from "Recent" on the home page.',

  library: 'Library',
  newFolder: 'New folder',
  folderName: 'Folder name',
  importPdf: 'Import PDF',
  importInto: (name) => `Drop a PDF here to import it into “${name}”`,
  foldersH: 'Folders',
  lecturesH: 'Lectures',
  kindCourse: (n) => `Course · ${n} lecture${n === 1 ? '' : 's'}`,
  kindGroup: (n) => `${n} folder${n === 1 ? '' : 's'}`,
  kindEmpty: 'Empty',
  lectureMeta: (slides, q) => `${slides} slides${q ? ` · ${q} question${q === 1 ? '' : 's'}` : ''}`,
  mixedNote: 'This course folder also has subfolders. A pre-quiz on this folder includes the lectures in them too.',
  emptyFolder: 'This folder is empty. Drop a PDF here to import it, or create a subfolder.',
  reorderHint: 'Drag lectures to reorder them, or onto a folder to move them there.',
  actions: 'More actions',
  rename: 'Rename',
  newName: 'New name',
  moveTo: 'Move to…',
  moveUp: 'Move up',
  moveDown: 'Move down',
  deleteFolder: 'Delete folder',
  deleteFolderConfirm: (name) => `Delete the empty folder “${name}”?`,
  moveTitle: (name) => `Move “${name}” to`,
  unreadable: "Can't read this file",

  preQuiz: 'Pre-quiz',
  newQuiz: 'New pre-quiz',
  quizMeta: (n, answered, correct) => `${n} questions · ${answered} answered · ${correct} right`,
  quizCount: 'Questions',
  quizTypes: 'Question types',
  typeName: { mcq: 'Multiple choice', short: 'Short answer', derivation: 'Derivation / calculation' },
  quizFocus: 'Focus',
  focusWeak: ['Where I got stuck', 'About 70% on what you asked about, followed up on or took notes on'],
  focusBalanced: ['Even coverage', "Every lecture's main topics"],
  focusUncovered: ["What I haven't touched", "Mostly topics you haven't asked about yet"],
  quizLectures: 'Lectures',
  quizCost: 'Generating reads many slides, so it uses more quota than a question and usually takes about a minute. You can leave this page; the quiz appears in the list when it is ready.',
  generate: 'Generate',
  quizGenerating: 'Claude is writing the questions…',
  quizRetry: 'A few questions failed the checks; rewriting them…',
  quizVerifying: 'Checking each answer against the slides…',
  quizReading: (what) => `Reading ${what}`,
  toolSlide: (l, p) => `${l} slide ${p}`,
  quizToolSearch: (q) => `search results for “${q}”`,
  toolQuestions: (l) => `your questions in ${l}`,
  toolNotes: (l) => `your notes in ${l}`,
  toolOutline: (l) => `the outline of ${l}`,
  toolLectures: 'the lecture list',
  toolProfile: 'your study history',
  quizFailed: 'Quiz generation failed: ',
  quizShort: (n, req) => `Only ${n} of ${req} questions passed the checks.`,
  deleteQuizConfirm: (title) => `Delete the pre-quiz “${title}” and your answers?`,
  quizNotFound: 'This pre-quiz no longer exists.',
  questionOf: (i, n) => `Question ${i} of ${n}`,
  difficultyName: ['Easy', 'Medium', 'Hard'],
  needsReview: 'The answer check flagged this question: ',
  yourAnswer: 'Your answer (just for you; it is not graded automatically)',
  showAnswer: 'Show answer',
  referenceAnswer: 'Answer',
  explanationH: 'Explanation',
  whyH: 'Why this question',
  markRight: 'I got it right',
  markWrong: 'I got it wrong',
  correctMsg: 'Correct',
  wrongMsg: (letter) => `Not quite — the answer is ${letter}`,
  markedRight: 'Marked right',
  markedWrong: 'Marked wrong',
  sourcesH: 'Source',
  openSource: (label, title, page) => `${label} · ${title} · slide ${page}`,
  sourceMissing: 'This lecture is no longer in this folder',
  prevQ: 'Previous',
  nextQ: 'Next',
  finish: 'See results',
  resultsH: 'Results',
  score: (c, n) => `${c} of ${n} right`,
  unmarkedCount: (n) => `${n} not marked`,
  byTopic: 'By topic',
  toReview: 'To review',
  allRight: 'All correct.',
  startOver: 'Start over',
  backToFolder: 'Back to folder',

  exportMd: 'Export Markdown',
  exportMdTitle: 'Export the mind map, explanations, Q&A and notes as one Markdown file',
  regenMapConfirm: 'Have Claude reorganize the mind map? The current topics will be replaced (Q&A and notes are kept).',
  regenMap: 'Regenerate map',
  mapBuilding: 'Claude is working out the structure of this lecture; the mind map will appear here. You can open any slide meanwhile.',
  mapFailed: 'Mind map generation failed: ',
  mapHelp: 'Open any slide. Yellow numbers count the questions on a slide; a pencil means it has notes. Drag to pan; scroll or use the buttons at the bottom right to zoom.',
  mapStats: (pages, topics) => `${pages} slides, ${topics} topics`,
  expandPages: (n) => `Show ${n} slides`,

  uploadTranscript: 'Upload transcript',
  uploadTranscriptTitle: 'Upload the lecture transcript (.txt / .vtt / .srt); explanations and answers will use the matching passages',
  transcriptPages: (a, n) => `Transcript · ${a}/${n} slides`,
  transcriptInfo: (name, a) =>
    `${name}: aligned to ${a} slides. New explanations and questions will use the matching passages; use "Regenerate" in the explanation panel to update existing ones.`,
  realign: 'Re-align',
  replaceFile: 'Replace file',
  deleteTranscriptConfirm: 'Delete the transcript and its alignment? Existing explanations are kept.',
  aligning: 'Claude is aligning the transcript to the slides…',
  alignFailed: 'Transcript alignment failed: ',
  aligned: (n) => `Transcript aligned to ${n} slides.`,

  map: 'Map',
  prevPage: 'Previous slide',
  nextPage: 'Next slide',
  sessionDrawer: 'Session log',
  selectHint: 'Drag over any part of the slide, such as a formula, to ask about it',
  slideAlt: (p, title) => `Slide ${p}: ${title}`,
  resizeSlide: 'Resize the slide and the panels below',
  resizePanels: 'Resize the explanation and notes',
  generatedBy: (model) => `Generated by ${model}`,
  noTranscript: 'No transcript uploaded; the explanation is based on the slide only',
  hasSpan: 'This slide has a matching transcript passage',
  noSpan: 'Nothing in the transcript matches this slide',
  hideSpan: 'Hide transcript',
  showSpan: 'Transcript passage',
  regenerate: 'Regenerate',
  explainQuestions: 'Questions about the explanation',
  notesFor: (p) => `Notes for slide ${p}`,
  notesPlaceholder: 'Notes for this slide. Write your own, or clip answers here from the Q&A.',
  askAboutThis: 'Ask about this',
  clipped: (p) => `Clipped to the notes for slide ${p}`,
  explainQueued: 'Queued; the explanation will start shortly…',
  explainRunning: 'Claude is writing the explanation for this slide…',
  explainFailed: 'Explanation failed: ',
  explainNone: "This slide's explanation hasn't been generated yet. It's generated the first time you open the slide, then saved in the file.",
  explainGenerate: 'Generate explanation',
  noteSaved: 'Saved',
  noteSaving: 'Saving…',
  noteFailed: 'Save failed; check that the backend is running',

  layout: 'Layout',
  arrange: 'Arrange',
  stack: 'Stacked',
  side: 'Side by side',
  show: 'Show',
  layoutTip: 'Drag the dividers between panels to resize them; double-click a divider to reset it.',
  resetLayout: 'Reset layout',

  askQuote: 'What would you like to ask about this passage?',
  askRegion: 'What would you like to ask about this region?',
  askPlaceholder: 'e.g. What does the range of this sum mean?',
  ask: 'Ask',
  newQuestion: 'New question',
  question: (n) => `Question ${n}`,
  openQuestion: (n, title) => `Open question ${n}: ${title}`,
  answering: 'Claude is answering…',
  queued: (n) =>
    `Queued: this slide's session is answering ${n ? `question ${n}` : 'another question'}; a session handles one message at a time.`,
  deleteQuestion: 'Delete this question',
  deleteQuestionConfirm: (n) => `Delete question ${n} and all its answers?`,
  minimize: 'Minimize',
  minimizeQuestion: 'Minimize this question',
  clipToNotes: 'Clip to notes',
  answerFailed: 'Answer failed: ',
  followUpWait: 'Wait for this answer to finish before following up…',
  followUp: 'Ask a follow-up…',
  followUpIn: (n) => `Follow up on question ${n}`,
  sendFollowUp: 'Send follow-up',
  noteClip: (n, q) => `[Question ${n}] ${q}`,
  toolPage: (p) => `Looking at slide ${p}…`,
  toolSearch: (q) => `Searching the slides for “${q}”…`,
  toolCourse: 'Reading through the whole course…',
  toolFiles: 'Looking through the course files…',

  drawerLabel: 'Session log',
  drawerTitle: (p) => `Claude session for slide ${p}`,
  drawerNoSession: '(none yet)',
  drawerClose: 'Close session log',
  drawerIntro: "In the interface each question is its own dialog; behind the scenes all of a slide's Q&A goes, in order, into one Claude session. Each message is prefixed with a thread tag so Claude knows which question a follow-up belongs to. (Tags are internal and stay in Chinese.)",
  drawerEmpty: 'No questions on this slide yet. Try selecting a region of the slide.',
  drawerContext: 'Context',
  drawerContextMsg: (p) => `The image, text and explanation of slide ${p}, sent once at the start of the session.`,
  drawerAnswer: (n) => `Claude's answer (shown in question ${n}'s dialog)`,
  drawerSent: 'Sent to Claude',
}

export const MESSAGES: Record<Locale, Messages> = { zh, en }

const HINT_KEY = 'gloss.locale' // first-paint hint only; the backend preference is the source of truth

function initialLocale(): Locale {
  try {
    const v = localStorage.getItem(HINT_KEY)
    if (v === 'zh' || v === 'en') return v
  } catch { /* storage unavailable */ }
  return 'en' // default interface language
}

const Ctx = createContext<{ locale: Locale; t: Messages; setLocale: (l: Locale) => void }>({
  locale: 'en', t: en, setLocale: () => {},
})

export function LangProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLoc] = useState<Locale>(initialLocale)

  const apply = useCallback((l: Locale) => {
    setLoc(l)
    document.documentElement.lang = l === 'zh' ? 'zh-CN' : 'en'
    try { localStorage.setItem(HINT_KEY, l) } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    api.prefs().then((p) => {
      if (p.ui_language) apply(p.ui_language)
      else apply('en') // no preference yet: English
    }).catch(() => {})
    document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setLocale = useCallback((l: Locale) => {
    apply(l)
    api.setPrefs(l).catch(() => {})
  }, [apply])

  const value = useMemo(() => ({ locale, t: MESSAGES[locale], setLocale }), [locale, setLocale])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useLang = () => useContext(Ctx)
export const useT = () => useContext(Ctx).t

/** The 中文 / EN toggle in the top bar. */
export function LangSwitch() {
  const { locale, t, setLocale } = useLang()
  return (
    <button className="btn ghost lang" aria-label={t.switchLabel} title={t.switchLabel}
      onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}>{t.switchTo}</button>
  )
}
