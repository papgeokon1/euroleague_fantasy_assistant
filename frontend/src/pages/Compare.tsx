import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api'
import type { ComparedPlayer, ListedPlayer, Position } from '../api'
import { Card, ErrorBox, NewsBadge, Opp, PlayerSearch, RangeBar, Spinner } from '../components/ui'
import { fmt, pct } from '../format'

const MAX = 6

interface Row {
  label: string
  hint?: string
  value: (p: ComparedPlayer) => number | null | undefined
  show: (p: ComparedPlayer) => ReactNode
  better?: 'high' | 'low'
}

const ROWS: Row[] = [
  { label: 'Price', value: (p) => p.price, show: (p) => fmt(p.price), better: 'low' },
  { label: 'Match', value: () => null, show: (p) => <Opp p={p} /> },
  { label: 'Turn', value: () => null, show: (p) => `T${p.turn}` },
  { label: 'Chance of playing', value: (p) => p.p_play, show: (p) => pct(p.p_play), better: 'high' },
  { label: 'Range if he plays', hint: '10–90%', value: () => null, show: (p) => <RangeBar p={p} /> },
  { label: 'Expected this round', value: (p) => p.expected, show: (p) => fmt(p.expected), better: 'high' },
  { label: 'Upside (90%)', value: (p) => p.high, show: (p) => fmt(p.high), better: 'high' },
  {
    label: 'Next 3 rounds',
    hint: 'expected, summed',
    value: (p) => p.next_rounds.reduce((s, r) => s + r.expected, 0),
    show: (p) => (
      <span title={p.next_rounds.map((r) => `R${r.round}: ${fmt(r.expected)}`).join(' · ')}>
        {fmt(p.next_rounds.reduce((s, r) => s + r.expected, 0))}
      </span>
    ),
    better: 'high',
  },
  { label: 'Points per credit', value: (p) => p.per_credit, show: (p) => fmt(p.per_credit, 2), better: 'high' },
  { label: 'Score to keep price', value: (p) => p.keep_price_at, show: (p) => fmt(p.keep_price_at), better: 'low' },
  { label: 'Chance price rises', value: (p) => p.p_price_rise, show: (p) => pct(p.p_price_rise), better: 'high' },
  { label: 'Owned by', value: (p) => p.popularity, show: (p) => pct(p.popularity) },
  {
    label: 'News',
    value: () => null,
    show: (p) => (p.news ? <NewsBadge status={p.news.status} title={`${p.news.reason} (${p.news.source})`} /> : '–'),
  },
]

export default function Compare() {
  const [picked, setPicked] = useState<ListedPlayer[]>([])
  const ids = picked.map((p) => p.id)
  const add = (p: ListedPlayer) => !ids.includes(p.id) && ids.length < MAX && setPicked([...picked, p])
  const result = useQuery({
    queryKey: ['compare', ids],
    queryFn: () => api.compare(ids),
    enabled: ids.length > 0,
  })

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Compare players</h1>
        <p className="text-sm text-slate-500">Pick up to {MAX} players. The best value in each row is highlighted.</p>
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-2">
          {picked.map((p) => (
            <span key={p.id} className="flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1 text-sm">
              {p.name}
              <button onClick={() => setPicked(picked.filter((x) => x.id !== p.id))} className="text-slate-500 hover:text-red-600">
                ×
              </button>
            </span>
          ))}
          {picked.length < MAX && <PlayerSearch onPick={add} exclude={ids} />}
        </div>
      </Card>

      {result.isError && <ErrorBox error={result.error} />}
      {result.isFetching && <Spinner />}
      {result.data && result.data.length > 0 && <Table players={result.data} />}

      <Browse onAdd={add} picked={ids} />
    </div>
  )
}

function Table({ players }: { players: ComparedPlayer[] }) {
  return (
    <Card>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th />
              {players.map((p) => (
                <th key={p.id} className="px-3 pb-2 text-left">
                  <div className="font-semibold text-slate-900">{p.name}</div>
                  <div className="text-xs font-normal text-slate-500">{p.position} · {p.team}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {ROWS.map((row) => {
              const values = players.map(row.value).filter((v): v is number => typeof v === 'number')
              const best = row.better === 'high' ? Math.max(...values) : row.better === 'low' ? Math.min(...values) : null
              return (
                <tr key={row.label}>
                  <td className="py-2 pr-3 text-slate-600">
                    {row.label}
                    {row.hint && <div className="text-xs text-slate-400">{row.hint}</div>}
                  </td>
                  {players.map((p) => {
                    const v = row.value(p)
                    const top = best !== null && values.length > 1 && v === best
                    return (
                      <td key={p.id} className={`px-3 ${top ? 'font-semibold text-emerald-700' : ''}`}>
                        {row.show(p)}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

const POSITIONS: { value: Position | ''; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'G', label: 'Guards' },
  { value: 'F', label: 'Forwards' },
  { value: 'C', label: 'Centers' },
  { value: 'HC', label: 'Coaches' },
]

function Browse({ onAdd, picked }: { onAdd: (p: ListedPlayer) => void; picked: number[] }) {
  const [position, setPosition] = useState<Position | ''>('')
  const [sort, setSort] = useState('expected')
  const [maxPrice, setMaxPrice] = useState('')
  const list = useQuery({
    queryKey: ['players', position, sort, maxPrice],
    queryFn: () => api.players({ position, sort, max_price: maxPrice || undefined, limit: 25 }),
  })
  return (
    <Card
      title="Top projections"
      right={
        <div className="flex flex-wrap gap-2 text-sm">
          <select value={position} onChange={(e) => setPosition(e.target.value as Position | '')} className="rounded-lg border border-slate-300 px-2 py-1">
            {POSITIONS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded-lg border border-slate-300 px-2 py-1">
            <option value="expected">Expected points</option>
            <option value="value">Points per credit</option>
            <option value="upside">Upside</option>
          </select>
          <input value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} placeholder="Max price" inputMode="decimal" className="w-24 rounded-lg border border-slate-300 px-2 py-1" />
        </div>
      }
    >
      {list.isPending ? (
        <Spinner />
      ) : list.isError ? (
        <ErrorBox error={list.error} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-3">Player</th>
                <th className="pr-3">Match</th>
                <th className="pr-3 text-right">Price</th>
                <th className="pr-3 text-right">Plays</th>
                <th className="pr-3">Range</th>
                <th className="pr-3 text-right">Expected</th>
                <th className="pr-3 text-right">Per credit</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {list.data.map((p) => (
                <tr key={p.id}>
                  <td className="py-1.5 pr-3">
                    <span className="font-medium">{p.name}</span>{' '}
                    <span className="text-xs text-slate-500">{p.position} · {p.team}</span> <NewsBadge status={p.news} />
                  </td>
                  <td className="pr-3"><Opp p={p} /></td>
                  <td className="pr-3 text-right">{fmt(p.price)}</td>
                  <td className="pr-3 text-right">{pct(p.p_play)}</td>
                  <td className="pr-3"><RangeBar p={p} /></td>
                  <td className="pr-3 text-right font-semibold">{fmt(p.expected)}</td>
                  <td className="pr-3 text-right">{fmt(p.per_credit, 2)}</td>
                  <td>
                    <button
                      disabled={picked.includes(p.id) || picked.length >= MAX}
                      onClick={() => onAdd(p)}
                      className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50 disabled:opacity-40"
                    >
                      + compare
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
