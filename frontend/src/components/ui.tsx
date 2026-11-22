import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import { fmt } from '../format'
import type { ListedPlayer, NewsStatus, Player, Slot } from '../api'

export function Card({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="font-semibold text-slate-900">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value}</div>
      {hint && <div className="text-xs text-slate-500">{hint}</div>}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand" />
      {label}
    </div>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      {error instanceof Error ? error.message : String(error)}
    </div>
  )
}

const NEWS_STYLE: Record<NewsStatus, string> = {
  out: 'bg-red-100 text-red-700',
  doubtful: 'bg-orange-100 text-orange-700',
  questionable: 'bg-amber-100 text-amber-700',
  limited: 'bg-sky-100 text-sky-700',
  available: 'bg-emerald-100 text-emerald-700',
}

export function NewsBadge({ status, title }: { status: NewsStatus | null | undefined; title?: string }) {
  if (!status) return null
  return (
    <span title={title} className={`rounded px-1.5 py-0.5 text-xs font-medium ${NEWS_STYLE[status]}`}>
      {status}
    </span>
  )
}

const SLOT_STYLE: Record<Slot, string> = {
  captain: 'bg-brand text-white',
  starter: 'bg-slate-800 text-white',
  sixth: 'bg-slate-500 text-white',
  bench: 'bg-slate-200 text-slate-700',
  coach: 'bg-indigo-100 text-indigo-700',
}

export function SlotBadge({ slot }: { slot: Slot }) {
  return (
    <span className={`inline-block w-16 rounded px-1.5 py-0.5 text-center text-xs font-medium ${SLOT_STYLE[slot]}`}>
      {slot === 'captain' ? 'captain ×2' : slot}
    </span>
  )
}

/** Points range if he plays (10–90%), with the median tick and expected points dot. */
export function RangeBar({ p, max = 40 }: { p: Pick<Player, 'low' | 'median' | 'high' | 'expected'>; max?: number }) {
  const x = (v: number) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`
  if (p.low === null || p.high === null) {
    return <span className="text-xs text-slate-400">expected only</span>
  }
  return (
    <div
      className="relative h-4 w-36"
      title={`If he plays: ${fmt(p.low)} – ${fmt(p.high)} (median ${fmt(p.median)}). Expected ${fmt(p.expected)}.`}
    >
      <div className="absolute top-1.5 h-1 w-full rounded bg-slate-100" />
      <div className="absolute top-1 h-2 rounded bg-brand/30" style={{ left: x(p.low), width: `calc(${x(p.high)} - ${x(p.low)})` }} />
      <div className="absolute top-0.5 h-3 w-0.5 bg-brand-dark" style={{ left: x(p.median) }} />
      <div className="absolute top-1 h-2 w-2 -translate-x-1 rounded-full bg-slate-900" style={{ left: x(p.expected) }} />
    </div>
  )
}

export function Opp({ p }: { p: Pick<Player, 'opponent' | 'home'> }) {
  if (!p.opponent) return <span className="text-slate-400">–</span>
  return (
    <span>
      {p.home ? 'vs' : '@'} {p.opponent}
    </span>
  )
}

/** Type-ahead search over all players in the market. */
export function PlayerSearch({
  onPick,
  exclude = [],
  placeholder = 'Search a player…',
}: {
  onPick: (p: ListedPlayer) => void
  exclude?: number[]
  placeholder?: string
}) {
  const [text, setText] = useState('')
  const query = useQuery({
    queryKey: ['search', text],
    queryFn: () => api.players({ search: text, limit: 8 }),
    enabled: text.trim().length >= 2,
  })
  const results = (query.data ?? []).filter((p) => !exclude.includes(p.id))
  return (
    <div className="relative w-full max-w-sm">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
      />
      {text.trim().length >= 2 && results.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
          {results.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => {
                  onPick(p)
                  setText('')
                }}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-slate-50"
              >
                <span>
                  <span className="font-medium">{p.name}</span>{' '}
                  <span className="text-slate-500">
                    {p.position} · {p.team}
                  </span>
                </span>
                <span className="text-slate-500">{fmt(p.price)} cr</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

