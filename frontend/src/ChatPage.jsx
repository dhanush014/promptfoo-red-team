import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'

const THREAD_KEY = 'warden-thread-id'

function newThreadId() {
  return crypto.randomUUID().slice(0, 8)
}

function savedThreadId() {
  try {
    return localStorage.getItem(THREAD_KEY) || newThreadId()
  } catch {
    return newThreadId()
  }
}

export default function ChatPage() {
  const [threadId, setThreadId] = useState(savedThreadId)
  const [messages, setMessages] = useState([])
  const [status, setStatus] = useState('done')
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef(null)

  const waiting = status === 'waiting_approval'

  async function loadHistory(id = threadId) {
    const data = await api.history(id)
    setMessages(data.messages)
    setStatus(data.status)
  }

  // Load the conversation when the thread changes, and remember it across reloads.
  useEffect(() => {
    try {
      localStorage.setItem(THREAD_KEY, threadId)
    } catch {
      // private mode etc. - the conversation just won't survive a reload
    }
    loadHistory(threadId).catch((e) => setError(e.message))
  }, [threadId])

  // While waiting for a manager, poll every 2 seconds.
  useEffect(() => {
    if (!waiting) return
    const timer = setInterval(() => loadHistory().catch(() => {}), 2000)
    return () => clearInterval(timer)
  }, [waiting, threadId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending, waiting])

  async function send(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending || waiting) return
    setInput('')
    setError('')
    setMessages((m) => [...m, { role: 'customer', content: text }])
    setSending(true)
    try {
      await api.chat(threadId, text)
      await loadHistory()
    } catch (err) {
      setError(`Something went wrong: ${err.message}`)
    } finally {
      setSending(false)
    }
  }

  function newConversation() {
    setMessages([])
    setStatus('done')
    setError('')
    setThreadId(newThreadId())
  }

  return (
    <div className="mx-auto flex h-screen max-w-2xl flex-col bg-white shadow-sm sm:border-x sm:border-slate-200">
      <header className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 sm:px-5">
        <div>
          <h1 className="text-lg font-semibold">Support Chat</h1>
          <p className="text-xs text-slate-500">Conversation {threadId}</p>
        </div>
        <button
          onClick={newConversation}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          New conversation
        </button>
      </header>

      <main className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-5">
        {messages.length === 0 && !sending && (
          <p className="mt-10 text-center text-sm text-slate-400">
            Ask about an order, or request a refund.
          </p>
        )}

        {messages.map((m, i) => (
          <Message key={i} role={m.role} content={m.content} />
        ))}

        {sending && <p className="text-sm text-slate-400">Agent is typing…</p>}

        {waiting && (
          <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-amber-500" />
            Waiting for approval…
          </div>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}
        <div ref={bottomRef} />
      </main>

      <form onSubmit={send} className="flex gap-2 border-t border-slate-200 px-4 py-3 sm:px-5">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={waiting}
          placeholder={waiting ? 'Waiting for a manager to approve…' : 'Type a message…'}
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500 disabled:cursor-not-allowed disabled:bg-slate-100"
          autoFocus
        />
        <button
          type="submit"
          disabled={waiting || sending || !input.trim()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  )
}

function Message({ role, content }) {
  const mine = role === 'customer'
  return (
    <div className={`flex flex-col ${mine ? 'items-end' : 'items-start'}`}>
      <span className="mb-1 text-xs font-medium text-slate-500">{mine ? 'You' : 'Agent'}</span>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          mine ? 'rounded-br-sm bg-slate-900 text-white' : 'rounded-bl-sm bg-slate-100 text-slate-800'
        }`}
      >
        {content}
      </div>
    </div>
  )
}
