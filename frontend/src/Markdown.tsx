import 'katex/dist/katex.min.css'
import { memo } from 'react'
import { defaultUrlTransform } from 'react-markdown'
import { glossLink, go } from './route'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

const remark = [remarkMath]
const rehype = [[rehypeKatex, { throwOnError: false, strict: 'ignore' }]] as never

// Study summaries link to slides and questions with gloss: URLs (§15.3); everything else is sanitized as usual.
const keepGloss = (url: string) => (url.startsWith('gloss:') ? url : defaultUrlTransform(url))
const components = {
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
    const r = href ? glossLink(href) : null
    if (!r) return <a href={href} target="_blank" rel="noreferrer">{children}</a>
    return <a href={href} onClick={(e) => { e.preventDefault(); go(r) }}>{children}</a>
  },
}

/** Claude's answers and explanations: Markdown with $inline$ and $$display$$ math (claude_workspace/CLAUDE.md). */
export const Markdown = memo(function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={'md' + (className ? ' ' + className : '')}>
      <ReactMarkdown remarkPlugins={remark} rehypePlugins={rehype} urlTransform={keepGloss} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
})
