// Icon paths from docs/mockup.html.
const PATHS = {
  back: '<path d="M15 18l-6-6 6-6"/>',
  next: '<path d="M9 18l6-6-6-6"/>',
  min: '<path d="M5 12h14"/>',
  chev: '<path d="M6 9l6 6 6-6"/>',
  log: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  select: '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
  layout: '<rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M3.5 12.5h17M12 12.5V20"/>',
  pen: '<path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3z"/>',
  send: '<path d="M5 12h13M13 6l6 6-6 6"/>',
  map: '<circle cx="5" cy="12" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="18" cy="18" r="2.2"/><path d="M7.2 12c4.5 0 4-6 8.6-6M7.2 12c4.5 0 4 6 8.6 6"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, className, style }: { name: IconName; className?: string; style?: React.CSSProperties }) {
  return (
    <svg viewBox="0 0 24 24" className={'icon' + (className ? ' ' + className : '')} style={style}
      aria-hidden="true" dangerouslySetInnerHTML={{ __html: PATHS[name] }} />
  )
}

export function Brand() {
  return (
    <span className="brand">
      <Icon name="map" style={{ color: 'var(--accent)', strokeWidth: 2 }} />
      Gloss
    </span>
  )
}
