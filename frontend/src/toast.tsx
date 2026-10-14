import { useCallback, useRef, useState } from 'react'

export function useToast() {
  const [msg, setMsg] = useState<string | null>(null)
  const timer = useRef<number>(undefined)
  const show = useCallback((m: string) => {
    setMsg(m)
    clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMsg(null), 2200)
  }, [])
  const node = msg ? <div className="toast" role="status">{msg}</div> : null
  return [node, show] as const
}
