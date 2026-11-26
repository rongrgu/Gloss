import { useEffect, useState } from 'react'

export type Route =
  | { name: 'home'; folder: string } // folder: library-relative path, '' = the library root (§13.2)
  | { name: 'quiz'; folder: string; qid: string } // a quiz stored in that folder (§13.6)
  | { name: 'sheet'; folder: string; sid: string } // a cheatsheet stored in that folder (§16.4)
  | { name: 'overview'; cid: string }
  | { name: 'digest'; cid: string } // the lecture's study summary (§15.3)
  | { name: 'slide'; cid: string; page: number; thread?: string } // thread: open that question's dialog

const decodePath = (p: string) => p.split('/').map(decodeURIComponent).join('/')
const encodePath = (p: string) => p.split('/').map(encodeURIComponent).join('/')

export function parseHash(hash: string): Route {
  const q = hash.match(/^#\/q\/([0-9a-f]+)(?:\/(.+))?$/)
  if (q) return { name: 'quiz', qid: q[1], folder: q[2] ? decodePath(q[2]) : '' }
  const s = hash.match(/^#\/s\/([0-9a-f]+)(?:\/(.+))?$/)
  if (s) return { name: 'sheet', sid: s[1], folder: s[2] ? decodePath(s[2]) : '' }
  const f = hash.match(/^#\/f\/(.+)$/)
  if (f) return { name: 'home', folder: decodePath(f[1]) }
  const d = hash.match(/^#\/c\/([^/]+)\/digest$/)
  if (d) return { name: 'digest', cid: decodeURIComponent(d[1]) }
  const m = hash.match(/^#\/c\/([^/]+)(?:\/p\/(\d+)(?:\/t\/([0-9a-f]+))?)?$/)
  if (!m) return { name: 'home', folder: '' }
  const cid = decodeURIComponent(m[1])
  if (!m[2]) return { name: 'overview', cid }
  return m[3] ? { name: 'slide', cid, page: Number(m[2]), thread: m[3] } : { name: 'slide', cid, page: Number(m[2]) }
}

export function href(r: Route): string {
  if (r.name === 'home') return r.folder ? `#/f/${encodePath(r.folder)}` : '#/'
  if (r.name === 'quiz') return `#/q/${r.qid}${r.folder ? '/' + encodePath(r.folder) : ''}`
  if (r.name === 'sheet') return `#/s/${r.sid}${r.folder ? '/' + encodePath(r.folder) : ''}`
  const base = `#/c/${encodeURIComponent(r.cid)}`
  if (r.name === 'digest') return `${base}/digest`
  return r.name === 'slide' ? `${base}/p/${r.page}${r.thread ? `/t/${r.thread}` : ''}` : base
}

/** `gloss:<course id>/<page>[/<thread id>]` links in a study summary (§15.3) -> an app route. */
export function glossLink(url: string): Route | null {
  const m = url.match(/^gloss:([^/]+)\/(\d+)(?:\/([0-9a-f]+))?$/)
  if (!m) return null
  const r: Route = { name: 'slide', cid: m[1], page: Number(m[2]) }
  return m[3] ? { ...r, thread: m[3] } : r
}

export function go(r: Route, replace = false) {
  if (replace) history.replaceState(null, '', href(r))
  else location.hash = href(r)
  if (replace) window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(location.hash))
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

// Course ids are only valid while the server has the file open. Remember id -> path
// so a link still works after the server restarts.
const KEY = 'gloss.paths'
export function rememberPath(cid: string, path: string) {
  try {
    const m = JSON.parse(localStorage.getItem(KEY) || '{}')
    m[cid] = path
    localStorage.setItem(KEY, JSON.stringify(m))
  } catch {
    /* storage unavailable */
  }
}
export function pathFor(cid: string): string | null {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}')[cid] ?? null
  } catch {
    return null
  }
}
