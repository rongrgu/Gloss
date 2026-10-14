import 'katex/dist/katex.min.css'
import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

const remark = [remarkMath]
const rehype = [[rehypeKatex, { throwOnError: false, strict: 'ignore' }]] as never

/** Claude's answers and explanations: Markdown with $inline$ and $$display$$ math (claude_workspace/CLAUDE.md). */
export const Markdown = memo(function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={'md' + (className ? ' ' + className : '')}>
      <ReactMarkdown remarkPlugins={remark} rehypePlugins={rehype}>{text}</ReactMarkdown>
    </div>
  )
})
