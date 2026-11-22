import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import type { ReviewRound } from '../api'
import { Card, ErrorBox, Spinner, Stat } from '../components/ui'
import { fmt, pct } from '../format'

function avg(rows: ReviewRound[], key: keyof ReviewRound) {
  const v = rows.map((r) => r[key]).filter((x): x is number => typeof x === 'number')
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null
}

/** Points per round: you, the model's expectation for your lineup, and the top 10. */
function PointsChart({ rows }: { rows: ReviewRound[] }) {
  const max = Math.max(...rows.flatMap((r) => [r.you ?? 0, r.top10_avg ?? 0, r.you_projected ?? 0]), 1)
  const h = (v: number | null) => `${((v ?? 0) / max) * 100}%`
  return (
    <div>
      <div className="flex h-48 items-end gap-2">
        {rows.map((r) => (
          <div key={r.round} className="flex h-full flex-1 flex-col justify-end">
            <div className="flex h-full items-end gap-0.5">
              <div className="flex-1 rounded-t bg-brand" style={{ height: h(r.you) }} title={`You: ${fmt(r.you)}`} />
              <div className="flex-1 rounded-t bg-slate-400" style={{ height: h(r.you_projected) }} title={`Expected for your lineup: ${fmt(r.you_projected)}`} />
              <div className="flex-1 rounded-t bg-slate-800" style={{ height: h(r.top10_avg) }} title={`Top-10 of the round: ${fmt(r.top10_avg)}`} />
            </div>
            <div className="mt-1 text-center text-xs text-slate-500">R{r.round}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-slate-600">
        <span><span className="mr-1 inline-block h-2 w-3 rounded bg-brand" />You</span>
        <span><span className="mr-1 inline-block h-2 w-3 rounded bg-slate-400" />Model's expectation for your lineup</span>
        <span><span className="mr-1 inline-block h-2 w-3 rounded bg-slate-800" />Top-10 managers of the round (average)</span>
      </div>
    </div>
  )
}

export default function Season() {
  const q = useQuery({ queryKey: ['review'], queryFn: api.review })
  if (q.isPending) return <Spinner />
  if (q.isError) return <ErrorBox error={q.error} />
  const { rounds, latest } = q.data
  if (!rounds.length) {
    return <Card>No reviewed rounds yet. Run <code>elf review --backfill</code>; after that each round is reviewed automatically.</Card>
  }
  const a = latest?.accuracy

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Season review</h1>
        <p className="text-sm text-slate-500">
          After every round: how good the projections were, and how your score compares with the model and the top managers.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Your average" value={fmt(avg(rounds, 'you'))} hint="points per round" />
        <Stat label="Expected for your lineups" value={fmt(avg(rounds, 'you_projected'))} hint="model, per round" />
        <Stat label="Projection error" value={fmt(avg(rounds, 'mae'), 2)} hint="average per player (MAE)" />
        <Stat label="Inside 10–90% range" value={pct(avg(rounds, 'within_10_90'))} hint="target 80%" />
      </div>

      <Card title="Points per round">
        <PointsChart rows={rounds} />
        <p className="mt-3 text-xs text-slate-500">
          Each round's top 10 out of ~470,000 managers are partly the luckiest that round, so they're a high bar. Your final
          lineups include changes made during the round, after early scores were known.
        </p>
      </Card>

      {latest && (
        <Card title={`Round ${latest.round} in detail`}>
          <div className="grid gap-4 text-sm md:grid-cols-2">
            <div className="space-y-1">
              <p>You scored <b>{fmt(latest.your_points)}</b>; the model expected <b>{fmt(latest.your_projected)}</b> for your lineup.</p>
              {latest.recommended_points !== undefined && (
                <p>The recommended plan would have scored <b>{fmt(latest.recommended_points)}</b>.</p>
              )}
              {latest.benchmark && (
                <p>Round's best: {fmt(latest.benchmark.top1)}; top-10 average {fmt(latest.benchmark.top10_avg)}.</p>
              )}
              {a && (
                <p className="text-slate-600">
                  Projections: error {fmt(a.mae, 2)} per player, {pct(a.within_10_90)} inside the range ({pct(a.below_10)} below,{' '}
                  {pct(a.above_90)} above); predicted {pct(a.p_play_predicted)} would play, {pct(a.p_play_actual)} did.
                </p>
              )}
              <p className="text-xs text-slate-400">Predictions: {latest.prediction_source ?? '–'}</p>
            </div>
            <div>
              <h3 className="mb-1 font-medium text-slate-700">Biggest surprises in your lineup</h3>
              <ul className="space-y-1">
                {(latest.surprises ?? []).map((s) => (
                  <li key={s.player_id} className="flex justify-between">
                    <span>{s.name} <span className="text-slate-400">({s.slot})</span></span>
                    <span>
                      expected {fmt(s.expected)}, scored{' '}
                      <b className={(s.points ?? 0) >= s.expected ? 'text-emerald-700' : 'text-red-700'}>{fmt(s.points)}</b>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}

      <Card title="Every round">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-3">Round</th>
                <th className="pr-3 text-right">You</th>
                <th className="pr-3 text-right">Expected</th>
                <th className="pr-3 text-right">Our plan</th>
                <th className="pr-3 text-right">Top-10 avg</th>
                <th className="pr-3 text-right">Error</th>
                <th className="pr-3 text-right">In range</th>
                <th>Predictions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rounds.map((r) => (
                <tr key={r.round}>
                  <td className="py-1.5 pr-3">R{r.round}</td>
                  <td className="pr-3 text-right font-semibold">{fmt(r.you)}</td>
                  <td className="pr-3 text-right">{fmt(r.you_projected)}</td>
                  <td className="pr-3 text-right">{fmt(r.recommended)}</td>
                  <td className="pr-3 text-right">{fmt(r.top10_avg)}</td>
                  <td className="pr-3 text-right">{fmt(r.mae, 2)}</td>
                  <td className="pr-3 text-right">{pct(r.within_10_90)}</td>
                  <td className="text-xs text-slate-500">{r.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
