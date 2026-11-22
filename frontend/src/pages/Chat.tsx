import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, chat } from '../api'

interface Message {
  role: 'user' | 'assistant' | 'error'
  text: string
  tools?: string[]
}

const TOOL_LABELS: Record<string, string> = {
  get_projections: 'Reading projections',
  compare_players: 'Comparing players',
  explain_projection: 'Explaining a projection',
  get_schedule: 'Checking the schedule',
  get_my_team: 'Reading your team',
  suggest_transfers: 'Running the optimizer',
  get_turn_advice: 'Checking lineup advice',
  get_news: 'Reading logged news',
  refresh_news: 'Searching the web for news',
}

const SUGGESTIONS = [
  'Who should I captain this round?',
  'Which transfers would you make, and why?',
  'Compare the best centers under 12 credits.',
  'Any injury news for my players?',
]

const STORAGE_KEY = 'elf-chat'

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '{}').messages ?? []
    } catch {
      return []
    }
  })
  const [sessionId, setSessionId] = useState<string | null>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '{}').sessionId ?? null
    } catch {
      return null
    }
  })
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [tools, setTools] = useState<string[]>([])
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ messages, sessionId }))
    } catch {
      // storage unavailable: the chat still works, it just won't survive a reload
    }
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sessionId, tools])

  async function send(text: string) {
    if (!text.trim() || busy) return
    setInput('')
    setBusy(true)
    setTools([])
    setMessages((m) => [...m, { role: 'user', text }])
    const used: string[] = []
    await chat(text, sessionId, (e) => {
      if (e.type === 'session') setSessionId(e.session_id)
      if (e.type === 'tool') {
        used.push(e.name)
        setTools([...used])
      }
      if (e.type === 'answer') setMessages((m) => [...m, { role: 'assistant', text: e.text, tools: used }])
      if (e.type === 'error') setMessages((m) => [...m, { role: 'error', text: e.detail }])
    }).catch((err: unknown) => setMessages((m) => [...m, { role: 'error', text: String(err) }]))
    setBusy(false)
    setTools([])
  }

  async function reset() {
    if (sessionId) await api.endChat(sessionId).catch(() => undefined)
    setSessionId(null)
    setMessages([])
  }

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Chat</h1>
          <p className="text-sm text-slate-500">
            Ask about your team. Claude answers with the model's and optimizer's numbers, and can't change your team.
          </p>
        </div>
        <button onClick={reset} disabled={busy} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50">
          New chat
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto rounded-xl border border-slate-200 bg-white p-4">
        {messages.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => send(s)} className="rounded-full border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:border-brand hover:text-brand">
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
            {m.role === 'user' ? (
              <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2 text-sm text-white">{m.text}</div>
            ) : m.role === 'error' ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{m.text}</div>
            ) : (
              <div className="max-w-[90%]">
                {m.tools && m.tools.length > 0 && (
                  <div className="mb-1 text-xs text-slate-400">Used: {m.tools.map((t) => TOOL_LABELS[t] ?? t).join(' · ')}</div>
                )}
                <div className="markdown rounded-2xl rounded-bl-sm bg-slate-50 px-4 py-2 text-sm">
                  <Markdown remarkPlugins={[remarkGfm]}>{m.text}</Markdown>
                </div>
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand" />
            {tools.length ? `${TOOL_LABELS[tools[tools.length - 1]] ?? tools[tools.length - 1]}…` : 'Thinking…'}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
        className="mt-3 flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about players, transfers, captain, news…"
          className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
        />
        <button disabled={busy || !input.trim()} className="rounded-lg bg-brand px-5 py-2 text-sm font-medium text-white hover:bg-brand-dark disabled:opacity-50">
          Send
        </button>
      </form>
    </div>
  )
}
