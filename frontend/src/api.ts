// Typed client for the FastAPI backend (src/elfantasy/api). Every number shown in the
// app comes from these endpoints: the projection model, the optimizer and the game API.

export type Position = 'G' | 'F' | 'C' | 'HC'
export type Slot = 'captain' | 'starter' | 'sixth' | 'bench' | 'coach'
export type NewsStatus = 'out' | 'doubtful' | 'questionable' | 'limited' | 'available'

export interface News {
  status: NewsStatus
  reason: string
  source: string
  fetched_at: string
}

export interface Player {
  id: number
  name: string
  position: Position
  team: string
  opponent: string | null
  home: boolean | null
  turn: number | null
  price: number
  p_play: number
  low: number | null // 10% of points if he plays
  median: number
  high: number | null // 90%
  expected: number // p_play x mean
  per_credit: number
  popularity: number | null
}

export interface ListedPlayer extends Player {
  news: NewsStatus | null
}

export interface ComparedPlayer extends Player {
  next_rounds: { round: number; expected: number }[]
  keep_price_at?: number
  p_price_rise?: number
  expected_price_change?: number
  news: News | null
}

export interface TeamPlayer extends Player {
  slot: Slot
  recommended: Slot
  played: boolean
  points: number | null
  news: News | null
}

export interface Team {
  team_name: string | null
  budget: number | null
  total_points: number | null
  transfers_used: number | null
  transfer_limit: number | null
  players: TeamPlayer[]
  advice: {
    before_round: boolean
    gain: number
    captain_now: number
    captain_recommended: number
    changes: boolean
  }
}

export interface Status {
  season: string
  round: number
  deadline: string | null
  turns: { number: number; start: string; last_start: string; started: boolean }[]
  prices_synced_at: string | null
  fantasy_token: boolean
  anthropic_key: boolean
}

export interface OptimizedPlayer extends Player {
  slot: Slot
  new: boolean
}

export interface OptimizeResult {
  round: number
  rounds_planned: number[]
  budget: number
  cost: number
  transfer_limit: number | null
  expected_points: number
  news_applied: boolean
  min_stars: number
  squad: OptimizedPlayer[]
  sell: Player[]
}

export interface OptimizeOptions {
  horizon: number
  sell: number[]
  from_scratch: boolean
  apply_news: boolean
  min_stars: number
}

export interface Job<T> {
  job_id: string
  kind: string
  status: 'pending' | 'running' | 'done' | 'error'
  result: T | null
  error: string | null
}

export interface NewsItem extends News {
  player_id: number
  name: string
  team: string
}

export interface ReviewRound {
  round: number
  source: 'live' | 'reconstructed' | null
  mae: number | null
  bias: number | null
  within_10_90: number | null
  you: number | null
  you_projected: number | null
  recommended: number | null
  top10_avg: number | null
  top1: number | null
}

export interface SeasonReview {
  rounds: ReviewRound[]
  latest: {
    round: number
    prediction_source?: string
    your_points?: number
    your_projected?: number
    recommended_points?: number
    benchmark?: { top1: number; top10_avg: number; top25_avg: number }
    accuracy?: { players: number; mae: number; bias: number; within_10_90: number; below_10: number; above_90: number; p_play_predicted: number; p_play_actual: number }
    surprises?: { player_id: number; name: string; slot: string; expected: number; points: number | null }[]
  } | null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // not JSON: keep the status text
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  status: () => request<Status>('/api/status'),
  team: () => request<Team>('/api/team'),
  players: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v))
    return request<ListedPlayer[]>(`/api/players?${q}`)
  },
  compare: (ids: number[]) =>
    request<ComparedPlayer[]>('/api/compare', { method: 'POST', body: JSON.stringify({ ids }) }),
  news: () => request<NewsItem[]>('/api/news'),
  review: () => request<SeasonReview>('/api/review'),
  optimize: (opts: OptimizeOptions) =>
    request<{ job_id: string }>('/api/optimize', { method: 'POST', body: JSON.stringify(opts) }),
  job: <T>(id: string) => request<Job<T>>(`/api/jobs/${id}`),
  endChat: (sessionId: string) => request<{ ok: boolean }>(`/api/chat/${sessionId}`, { method: 'DELETE' }),
}

export type ChatEvent =
  | { type: 'session'; session_id: string }
  | { type: 'tool'; name: string; input: Record<string, unknown> }
  | { type: 'answer'; text: string; session_id: string }
  | { type: 'error'; detail: string }

/** Send a chat message; calls onEvent for each server-sent event as it arrives. */
export async function chat(
  message: string,
  sessionId: string | null,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // keep the status text
    }
    onEvent({ type: 'error', detail })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let end
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const kind = raw.match(/^event: (.*)$/m)?.[1]
      const data = raw.match(/^data: (.*)$/m)?.[1]
      if (kind && data) onEvent({ type: kind, ...JSON.parse(data) } as ChatEvent)
    }
  }
}
