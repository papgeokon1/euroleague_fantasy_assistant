import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type { TeamPlayer } from '../api'
import { Card, ErrorBox, NewsBadge, Opp, RangeBar, SlotBadge, Spinner, Stat } from '../components/ui'
import { fmt, pct, SLOT_MULT, SLOT_ORDER } from '../format'

function expectedTotal(players: TeamPlayer[], key: 'slot' | 'recommended') {
  return players.reduce((sum, p) => sum + p.expected * SLOT_MULT[p[key]], 0)
}

export default function Dashboard() {
  const team = useQuery({ queryKey: ['team'], queryFn: api.team, refetchInterval: 5 * 60_000 })

  if (team.isPending) return <Spinner label="Loading your team (the first load trains or loads the model)…" />
  if (team.isError) return <ErrorBox error={team.error} />
  const t = team.data
  const players = [...t.players].sort(
    (a, b) => SLOT_ORDER[a.slot] - SLOT_ORDER[b.slot] || (a.turn ?? 9) - (b.turn ?? 9),
  )
  const now = expectedTotal(t.players, 'slot')
  const best = expectedTotal(t.players, 'recommended')
  const byId = new Map(t.players.map((p) => [p.id, p]))
  const flagged = t.players.filter((p) => p.news && p.news.status !== 'available')

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{t.team_name ?? 'My team'}</h1>
          <p className="text-sm text-slate-500">Your squad as it is in the game right now, with the model's projections.</p>
        </div>
        <button
          onClick={() => team.refetch()}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
        >
          {team.isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Expected this round" value={fmt(now)} hint="with your current lineup" />
        <Stat label="Season points" value={fmt(t.total_points)} />
        <Stat label="Budget" value={fmt(t.budget)} hint="team value + bank" />
        <Stat
          label="Transfers"
          value={`${t.transfers_used ?? 0} / ${t.transfer_limit ?? '–'}`}
          hint={(t.transfer_limit ?? 0) >= 11 ? 'unlimited window' : 'used this round'}
        />
      </div>

      <Card title={t.advice.before_round ? 'Lineup advice before the round' : 'Lineup advice for the next turn'}>
        {t.advice.changes ? (
          <div className="space-y-1 text-sm">
            <p>
              Make the changes marked below:{' '}
              <span className="font-semibold text-emerald-700">+{fmt(t.advice.gain)} expected points</span>{' '}
              ({fmt(now)} → {fmt(best)}).
            </p>
            {t.advice.captain_now !== t.advice.captain_recommended && (
              <p>
                Captain: <span className="line-through">{byId.get(t.advice.captain_now)?.name}</span> →{' '}
                <span className="font-semibold">{byId.get(t.advice.captain_recommended)?.name}</span>
              </p>
            )}
            {t.advice.before_round && (
              <p className="text-slate-500">
                The starting lineup keeps later options open: after turn 1 you can still move the armband or bench a
                weak early score for a later player.
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-600">Your lineup is already the best option. No changes needed.</p>
        )}
      </Card>

      <Card
        title="Squad"
        right={
          <span className="text-xs text-slate-500">
            Bar: points range if he plays (10–90%) · tick = median · dot = expected
          </span>
        }
      >
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
                <th className="pr-3 text-right">Expected</th>
                <th className="pr-3 text-right">Points</th>
                <th>Advice</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {players.map((p) => (
                <tr key={p.id} className={p.recommended !== p.slot ? 'bg-amber-50' : ''}>
                  <td className="py-2 pr-3"><SlotBadge slot={p.slot} /></td>
                  <td className="pr-3">
                    <div className="font-medium text-slate-900">{p.name}</div>
                    <div className="flex items-center gap-1 text-xs text-slate-500">
                      {p.position} · {p.team} <NewsBadge status={p.news?.status} title={p.news?.reason} />
                    </div>
                  </td>
                  <td className="pr-3"><Opp p={p} /></td>
                  <td className="pr-3">T{p.turn}</td>
                  <td className="pr-3 text-right">{fmt(p.price)}</td>
                  <td className="pr-3 text-right">{pct(p.p_play)}</td>
                  <td className="pr-3"><RangeBar p={p} /></td>
                  <td className="pr-3 text-right font-semibold">{fmt(p.expected)}</td>
                  <td className="pr-3 text-right">{p.played ? fmt(p.points) : <span className="text-slate-400">–</span>}</td>
                  <td>{p.recommended !== p.slot && <span className="text-xs font-medium text-amber-700">→ {p.recommended}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {flagged.length > 0 && (
        <Card title="News for your players">
          <ul className="space-y-2 text-sm">
            {flagged.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2">
                <NewsBadge status={p.news?.status} />
                <span className="font-medium">{p.name}</span>
                <span className="text-slate-600">{p.news?.reason}</span>
                <span className="text-xs text-slate-400">({p.news?.source})</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-slate-500">
            News isn't applied to projections by default. You can apply it on the{' '}
            <Link to="/optimize" className="text-brand underline">Optimize</Link> page.
          </p>
        </Card>
      )}
    </div>
  )
}
