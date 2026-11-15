import { useEffect, useState } from 'react'

export type Route =
  | { name: 'home'; folder: string } // folder: library-relative path, '' = the library root (§13.2)
  | { name: 'quiz'; folder: string; qid: string } // a pre-quiz stored in that folder (§13.6)
  | { name: 'overview'; cid: string }
  | { name: 'slide'; cid: string; page: number }

const decodePath = (p: string) => p.split('/').map(decodeURIComponent).join('/')
const encodePath = (p: string) => p.split('/').map(encodeURIComponent).join('/')

export function parseHash(hash: string): Route {
  const q = hash.match(/^#\/q\/([0-9a-f]+)(?:\/(.+))?$/)
  if (q) return { name: 'quiz', qid: q[1], folder: q[2] ? decodePath(q[2]) : '' }
  const f = hash.match(/^#\/f\/(.+)$/)
  if (f) return { name: 'home', folder: decodePath(f[1]) }
  const m = hash.match(/^#\/c\/([^/]+)(?:\/p\/(\d+))?$/)
  if (!m) return { name: 'home', folder: '' }
  const cid = decodeURIComponent(m[1])
  return m[2] ? { name: 'slide', cid, page: Number(m[2]) } : { name: 'overview', cid }
}

export function href(r: Route): string {
  if (r.name === 'home') return r.folder ? `#/f/${encodePath(r.folder)}` : '#/'
  if (r.name === 'quiz') return `#/q/${r.qid}${r.folder ? '/' + encodePath(r.folder) : ''}`
  const base = `#/c/${encodeURIComponent(r.cid)}`
  return r.name === 'slide' ? `${base}/p/${r.page}` : base
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
