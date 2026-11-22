import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { localTime } from '../format'

const NAV = [
  { to: '/', label: 'My team' },
  { to: '/optimize', label: 'Optimize' },
  { to: '/compare', label: 'Compare' },
  { to: '/chat', label: 'Chat' },
]

function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

function countdown(iso: string, now: number) {
  const diff = new Date(iso).getTime() - now
  if (diff <= 0) return 'started'
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h ${m}m`
}

function RoundBanner() {
  const status = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: 60_000 })
  const now = useNow()
  const s = status.data
  if (status.isError) return <span className="text-sm text-red-200">{(status.error as Error).message}</span>
  if (!s) return <span className="text-sm text-white/70">Loading round…</span>
  const next = s.turns.find((t) => !t.started)
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-white/90">
      <span className="font-semibold text-white">Round {s.round}</span>
      {next ? (
        <span>
          Turn {next.number} starts {localTime(next.start)}{' '}
          <span className="rounded bg-white/15 px-1.5 py-0.5 font-medium">in {countdown(next.start, now)}</span>
        </span>
      ) : (
        <span>All turns started</span>
      )}
      <span className="text-white/60">Prices synced {localTime(s.prices_synced_at)}</span>
    </div>
  )
}

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="bg-slate-900">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-6">
            <span className="text-lg font-bold text-white">
              <span className="text-brand">EL</span> Fantasy
            </span>
            <nav className="flex gap-1">
              {NAV.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.to === '/'}
                  className={({ isActive }) =>
                    `rounded-md px-3 py-1.5 text-sm font-medium ${
                      isActive ? 'bg-brand text-white' : 'text-white/80 hover:bg-white/10 hover:text-white'
                    }`
                  }
                >
                  {n.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <RoundBanner />
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
