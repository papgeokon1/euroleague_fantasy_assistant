import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import type { ListedPlayer, OptimizeResult } from '../api'
import { Card, ErrorBox, Opp, PlayerSearch, RangeBar, SlotBadge, Spinner, Stat } from '../components/ui'
import { fmt, pct, SLOT_ORDER } from '../format'

export default function Optimize() {
  const [horizon, setHorizon] = useState(3)
  const [fromScratch, setFromScratch] = useState(false)
  const [applyNews, setApplyNews] = useState(false)
  const [minStars, setMinStars] = useState(0)
  const [sell, setSell] = useState<ListedPlayer[]>([])
  const [jobId, setJobId] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: () =>
      api.optimize({
        horizon, sell: sell.map((p) => p.id), from_scratch: fromScratch, apply_news: applyNews, min_stars: minStars,
      }),
    onSuccess: (r) => setJobId(r.job_id),
  })
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.job<OptimizeResult>(jobId!),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && ['done', 'error'].includes(q.state.data.status) ? false : 1500),
  })
  const running = start.isPending || (jobId !== null && !['done', 'error'].includes(job.data?.status ?? 'pending'))
  const result = job.data?.status === 'done' ? job.data.result : null

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Optimize</h1>
        <p className="text-sm text-slate-500">
          The optimizer picks the squad, lineup and captain that score the most expected points over the next rounds,
          within your budget and transfer limit.
        </p>
      </div>

      <Card title="Options">
        <div className="flex flex-wrap items-end gap-6 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-slate-600">Plan ahead</span>
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2"
            >
              {[1, 2, 3, 4, 5].map((h) => (
                <option key={h} value={h}>
                  {h} round{h > 1 ? 's' : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1" title="A preference, not a model recommendation: in backtests, forcing stars cost about 4 points a round">
            <span className="text-slate-600">Minimum stars (14+ credits)</span>
            <select
              value={minStars}
              onChange={(e) => setMinStars(Number(e.target.value))}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2"
            >
              <option value={0}>No rule (recommended)</option>
              {[2, 3, 4].map((n) => (
                <option key={n} value={n}>
                  At least {n}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={fromScratch} onChange={(e) => setFromScratch(e.target.checked)} />
            Ignore my squad (best team from scratch)
          </label>
          <label className="flex items-center gap-2" title="Untested: adjusts projections for players listed out or doubtful">
            <input type="checkbox" checked={applyNews} onChange={(e) => setApplyNews(e.target.checked)} />
            Apply injury news (experimental)
          </label>
        </div>
        <div className="mt-4 space-y-2 text-sm">
          <span className="text-slate-600">Force out (sell these players):</span>
          <div className="flex flex-wrap items-center gap-2">
            {sell.map((p) => (
              <span key={p.id} className="flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1">
                {p.name}
                <button onClick={() => setSell(sell.filter((s) => s.id !== p.id))} className="text-slate-500 hover:text-red-600">
                  ×
                </button>
              </span>
            ))}
            <PlayerSearch onPick={(p) => setSell([...sell, p])} exclude={sell.map((p) => p.id)} placeholder="Add a player to sell…" />
          </div>
        </div>
        <button
          disabled={running}
          onClick={() => start.mutate()}
          className="mt-5 rounded-lg bg-brand px-5 py-2 font-medium text-white hover:bg-brand-dark disabled:opacity-50"
        >
          {running ? 'Optimizing…' : 'Run optimizer'}
        </button>
      </Card>

      {start.isError && <ErrorBox error={start.error} />}
      {job.data?.status === 'error' && <ErrorBox error={job.data.error} />}
      {running && <Spinner label="Solving the plan (usually 15–60 seconds)…" />}
      {result && <Plan r={result} />}
    </div>
  )
}

function Plan({ r }: { r: OptimizeResult }) {
  const squad = [...r.squad].sort((a, b) => SLOT_ORDER[a.slot] - SLOT_ORDER[b.slot] || b.expected - a.expected)
  const buys = r.squad.filter((p) => p.new)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Expected this round" value={fmt(r.expected_points)} hint={`planned over rounds ${r.rounds_planned.join(', ')}`} />
        <Stat label="Squad cost" value={`${fmt(r.cost)} / ${fmt(r.budget)}`} />
        <Stat label="Transfers" value={buys.length} hint={r.transfer_limit === null ? 'unlimited window' : `limit ${r.transfer_limit}`} />
        <Stat
          label="Your rules"
          value={r.min_stars ? `${r.min_stars}+ stars` : 'none'}
          hint={r.news_applied ? 'news applied' : 'news not applied'}
        />
      </div>

      {(buys.length > 0 || r.sell.length > 0) && (
        <Card title="Transfers">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-semibold text-red-700">Sell</h3>
              <ul className="space-y-1 text-sm">
                {r.sell.map((p) => (
                  <li key={p.id} className="flex justify-between">
                    <span>{p.name} <span className="text-slate-500">{p.position} · {p.team}</span></span>
                    <span className="text-slate-500">{fmt(p.price)} cr · {fmt(p.expected)} exp</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-emerald-700">Buy</h3>
              <ul className="space-y-1 text-sm">
                {buys.map((p) => (
                  <li key={p.id} className="flex justify-between">
                    <span>{p.name} <span className="text-slate-500">{p.position} · {p.team}</span></span>
                    <span className="text-slate-500">{fmt(p.price)} cr · {fmt(p.expected)} exp</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}

      <Card title="Recommended squad and lineup">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-3">Slot</th>
                <th className="pr-3">Player</th>
                <th className="pr-3">Match</th>
                <th className="pr-3">Turn</th>
                <th className="pr-3 text-right">Price</th>
                <th className="pr-3 text-right">Plays</th>
                <th className="pr-3">Range</th>
                <th className="text-right">Expected</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {squad.map((p) => (
                <tr key={p.id} className={p.new ? 'bg-emerald-50' : ''}>
                  <td className="py-2 pr-3"><SlotBadge slot={p.slot} /></td>
                  <td className="pr-3">
                    <span className="font-medium text-slate-900">{p.name}</span>{' '}
                    {p.new && <span className="rounded bg-emerald-600 px-1.5 py-0.5 text-xs font-medium text-white">BUY</span>}
                    <div className="text-xs text-slate-500">{p.position} · {p.team}</div>
                  </td>
                  <td className="pr-3"><Opp p={p} /></td>
                  <td className="pr-3">T{p.turn}</td>
                  <td className="pr-3 text-right">{fmt(p.price)}</td>
                  <td className="pr-3 text-right">{pct(p.p_play)}</td>
                  <td className="pr-3"><RangeBar p={p} /></td>
                  <td className="text-right font-semibold">{fmt(p.expected)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
