// The cheatsheet's Word-like editor (DESIGN.md §16.4): TipTap with Markdown in and out, so the sheet is
// still stored as Markdown (AI edits, versions) while the student only sees formatted text.
// Formulas are our own two nodes, rendered with the KaTeX the rest of the app already uses
// (@tiptap/extension-mathematics wants an older KaTeX).

import { Node, mergeAttributes, type AnyExtension } from '@tiptap/core'
import { Markdown } from '@tiptap/markdown'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import StarterKit from '@tiptap/starter-kit'
import katex from 'katex'

/** Fired when a formula is clicked; the page opens a small editor for it. */
export const MATH_EDIT = 'gloss-math-edit'
export interface MathEditDetail { pos: number; latex: string; block: boolean }

function renderMath(el: HTMLElement, latex: string, display: boolean) {
  try {
    katex.render(latex || '\\square', el, { displayMode: display, throwOnError: false, output: 'html' })
  } catch {
    el.textContent = latex
  }
}

function mathView(block: boolean) {
  return ({ node, getPos }: { node: { attrs: Record<string, unknown> }; getPos: () => number | undefined }) => {
    const dom = document.createElement(block ? 'div' : 'span')
    dom.className = block ? 'math-node block' : 'math-node'
    dom.contentEditable = 'false'
    let latex = String(node.attrs.latex ?? '')
    renderMath(dom, latex, block || !!node.attrs.display)
    dom.addEventListener('click', (e) => {
      e.preventDefault()
      const pos = getPos()
      if (pos === undefined) return
      window.dispatchEvent(new CustomEvent<MathEditDetail>(MATH_EDIT, { detail: { pos, latex, block } }))
    })
    return {
      dom,
      update: (n: { type: unknown; attrs: Record<string, unknown> }) => {
        if ((n as { type: { name: string } }).type.name !== (block ? 'blockMath' : 'inlineMath')) return false
        latex = String(n.attrs.latex ?? '')
        renderMath(dom, latex, block || !!n.attrs.display)
        return true
      },
    }
  }
}

// `$x$` inline; `$$x$$` inside a line stays inline but displayed. Not `$5 and $6`: no space after the opening $.
const INLINE = /^\$(?!\$)(?!\s)((?:\\.|[^\\$\n])+?)\$(?!\d)/
const INLINE_DISPLAY = /^\$\$(?!\s*\n)((?:\\.|[^\\$])+?)\$\$/
const BLOCK = /^\$\$[ \t]*\n?([\s\S]+?)\n?[ \t]*\$\$[ \t]*(?:\n+|$)/

export const InlineMath = Node.create({
  name: 'inlineMath',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes: () => ({ latex: { default: '' }, display: { default: false } }),
  parseHTML: () => [{ tag: 'span[data-type="inline-math"]', getAttrs: (el) => ({ latex: (el as HTMLElement).dataset.latex ?? '' }) }],
  renderHTML: ({ node, HTMLAttributes }) =>
    ['span', mergeAttributes(HTMLAttributes, { 'data-type': 'inline-math', 'data-latex': node.attrs.latex })],
  addNodeView() { return mathView(false) as never },
  renderText: ({ node }) => `$${node.attrs.latex}$`, // selected text sent to Gloss keeps the formula
  markdownTokenizer: {
    name: 'inlineMath',
    level: 'inline',
    start: (src: string) => src.indexOf('$'),
    tokenize: (src: string) => {
      const d = INLINE_DISPLAY.exec(src)
      if (d) return { type: 'inlineMath', raw: d[0], text: d[1].trim(), display: true }
      const m = INLINE.exec(src)
      if (m) return { type: 'inlineMath', raw: m[0], text: m[1], display: false }
      return undefined
    },
  },
  parseMarkdown: (token) => ({ type: 'inlineMath', attrs: { latex: token.text ?? '', display: !!(token as { display?: boolean }).display } }),
  renderMarkdown: (node) => (node.attrs?.display ? `$$${node.attrs?.latex ?? ''}$$` : `$${node.attrs?.latex ?? ''}$`),
})

export const BlockMath = Node.create({
  name: 'blockMath',
  group: 'block',
  atom: true,
  selectable: true,
  addAttributes: () => ({ latex: { default: '' } }),
  parseHTML: () => [{ tag: 'div[data-type="block-math"]', getAttrs: (el) => ({ latex: (el as HTMLElement).dataset.latex ?? '' }) }],
  renderHTML: ({ node, HTMLAttributes }) =>
    ['div', mergeAttributes(HTMLAttributes, { 'data-type': 'block-math', 'data-latex': node.attrs.latex })],
  addNodeView() { return mathView(true) as never },
  renderText: ({ node }) => `$$${node.attrs.latex}$$`,
  markdownTokenizer: {
    name: 'blockMath',
    level: 'block',
    start: (src: string) => src.indexOf('$$'),
    tokenize: (src: string) => {
      const m = BLOCK.exec(src)
      return m ? { type: 'blockMath', raw: m[0], text: m[1].trim() } : undefined
    },
  },
  parseMarkdown: (token) => ({ type: 'blockMath', attrs: { latex: token.text ?? '' } }),
  renderMarkdown: (node) => `$$\n${node.attrs?.latex ?? ''}\n$$`,
})

export function sheetExtensions(): AnyExtension[] {
  return [
    StarterKit.configure({ link: { openOnClick: false }, codeBlock: false }),
    Table.configure({ resizable: false }),
    TableRow, TableHeader, TableCell,
    InlineMath, BlockMath,
    Markdown,
  ]
}

/** Markdown as stored: the serializer pads tables and leaves extra blank lines around them. */
export function tidyMarkdown(md: string): string {
  return md.replace(/\n{3,}/g, '\n\n').trim() + '\n'
}
