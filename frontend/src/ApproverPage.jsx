import { useEffect, useState } from 'react'
import { api, money } from './api.js'

// --- how audit rows read and look -------------------------------------------

function actionLabel(row) {
  let args = {}
  try {
    args = JSON.parse(row.args_json)
  } catch {
    // leave args empty
  }
  if (row.tool === 'issue_refund') return `refund ${money(args.amount)}`
  if (row.tool === 'send_email') return `email ${args.to_email}`
  if (row.tool === 'lookup_order') return 'lookup order'
  return row.tool
}

function outcomeLabel(row) {
  if (row.outcome === 'blocked') return `BLOCKED ${row.rule}`
  if (row.outcome === 'needs_approval') return `paused ${row.rule}`
  return row.outcome
}

// Blocked rows red, failed rows orange, approval rows amber, allowed/executed gray.
function outcomeStyle(outcome) {
  if (outcome === 'blocked') return 'bg-red-50 text-red-700'
  if (outcome === 'failed') return 'bg-orange-50 text-orange-700'
  if (['needs_approval', 'approved', 'denied'].includes(outcome)) return 'bg-amber-50 text-amber-800'
  return 'text-slate-500'
}

// Hover text: for a failed row, just the tool's error message (detail is "{'error': '...'}").
function hoverText(row) {
  if (row.outcome === 'failed') {
    const match = (row.detail || '').match(/'error': ['"](.*)['"]\}$/)
    return match ? `Error: ${match[1]}` : row.detail
  }
  return row.detail || ''
}

function timeOf(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
}

// --- page --------------------------------------------------------------------

export default function ApproverPage() {
  const [tab, setTab] = useState('tickets')
  const [agent, setAgent] = useState(null)
  const [tickets, setTickets] = useState([])
  const [audit, setAudit] = useState([])
  const [detailId, setDetailId] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState('')

  async function refresh() {
    try {
      const [pending, rows] = await Promise.all([api.tickets('pending'), api.audit()])
      setTickets(pending)
      setAudit(rows)
      setError('')
    } catch (e) {
      setError(`Can't reach the backend: ${e.message}`)
    }
  }

  useEffect(() => {
    api.agent().then(setAgent).catch(() => {})
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(timer)
  }, [notice])

  async function decide(id, decision) {
    setBusyId(id)
    try {
      const res = decision === 'approve' ? await api.approve(id) : await api.deny(id)
      const status = res.ticket.status
      setNotice(
        status === 'blocked_on_approval'
          ? { tone: 'red', text: `Ticket #${id} was approved but blocked on re-check. Nothing was refunded.` }
          : { tone: status === 'approved' ? 'green' : 'slate', text: `Ticket #${id} ${status}.` },
      )
      setDetailId(null)
    } catch (e) {
      setNotice({ tone: 'red', text: `Ticket #${id}: ${e.message}` })
    } finally {
      setBusyId(null)
      refresh()
    }
  }

  async function resetDemo() {
    if (!window.confirm('Reset the demo? This wipes all tickets, activity and conversations.')) return
    await api.reset()
    setDetailId(null)
    setNotice({ tone: 'slate', text: 'Demo data reset.' })
    refresh()
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-5 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-4">
        <h1 className="text-lg font-semibold">Warden Approvals</h1>
        <div className="flex items-center gap-3">
          <button onClick={resetDemo} className="text-xs text-slate-400 underline-offset-2 hover:text-slate-700 hover:underline">
            Reset demo
          </button>
          <div className="flex rounded-md border border-slate-300 p-0.5 text-sm">
            {['tickets', 'activity'].map((name) => (
              <button
                key={name}
                onClick={() => setTab(name)}
                className={`rounded px-3 py-1 font-medium capitalize ${
                  tab === name ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                {name}
                {name === 'tickets' && tickets.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-amber-400 px-1.5 text-xs text-slate-900">{tickets.length}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      </header>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      {notice && <Notice {...notice} />}

      {tab === 'tickets' ? (
        <div className="mt-5 space-y-5">
          {agent && <IdCard agent={agent} />}
          <TicketList tickets={tickets} busyId={busyId} onDetails={setDetailId} onDecide={decide} />
        </div>
      ) : (
        <Activity rows={audit} />
      )}

      {detailId && (
        <DetailsPopup id={detailId} busy={busyId === detailId} onClose={() => setDetailId(null)} onDecide={decide} />
      )}
    </div>
  )
}

function Notice({ tone, text }) {
  const styles = {
    green: 'border-green-200 bg-green-50 text-green-800',
    red: 'border-red-200 bg-red-50 text-red-700',
    slate: 'border-slate-200 bg-white text-slate-700',
  }
  return <div className={`mt-4 rounded-md border px-3 py-2 text-sm ${styles[tone]}`}>{text}</div>
}

function IdCard({ agent }) {
  return (
    <section className="max-w-sm rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-semibold text-white">
          RB
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Digital Employee</p>
          <p className="font-semibold">{agent.name}</p>
        </div>
      </div>
      <dl className="mt-3 space-y-1.5 text-sm">
        <div className="flex gap-2">
          <dt className="text-slate-500">Supervisor:</dt>
          <dd className="font-medium">{agent.supervisor}</dd>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <dt className="text-slate-500">Can use:</dt>
          {agent.allowed_tools.map((tool) => (
            <dd key={tool} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">
              {tool}
            </dd>
          ))}
        </div>
      </dl>
    </section>
  )
}

function DecisionButtons({ id, busy, onDecide }) {
  return (
    <>
      <button
        onClick={() => onDecide(id, 'approve')}
        disabled={busy}
        className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
      >
        {busy ? 'Working…' : 'Approve'}
      </button>
      <button
        onClick={() => onDecide(id, 'deny')}
        disabled={busy}
        className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
      >
        Deny
      </button>
    </>
  )
}

function TicketList({ tickets, busyId, onDetails, onDecide }) {
  if (tickets.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-400">
        No tickets waiting for approval.
      </p>
    )
  }
  return (
    <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
      {tickets.map((t) => (
        <li key={t.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <span className="font-mono text-sm text-slate-500">#{t.order_id}</span>
          <span className="w-16 font-semibold">{money(t.amount)}</span>
          <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{t.agent_reason}</span>
          <div className="flex gap-2">
            <button
              onClick={() => onDetails(t.id)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Details
            </button>
            <DecisionButtons id={t.id} busy={busyId === t.id} onDecide={onDecide} />
          </div>
        </li>
      ))}
    </ul>
  )
}

function DetailsPopup({ id, busy, onClose, onDecide }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    api.ticket(id).then(setData).catch(() => onClose())
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id])

  const steps = data ? data.audit.filter((row) => row.outcome !== 'executed') : []

  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h2 className="font-semibold">Ticket #{id}</h2>
          <button onClick={onClose} className="text-xl leading-none text-slate-400 hover:text-slate-700" aria-label="Close">
            ×
          </button>
        </div>

        {!data ? (
          <p className="px-5 py-8 text-center text-sm text-slate-400">Loading…</p>
        ) : (
          <div className="space-y-4 px-5 py-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
              <dt className="text-slate-500">Customer</dt>
              <dd>{data.order?.customer_email}</dd>
              <dt className="text-slate-500">Order total</dt>
              <dd>{money(data.order?.total)}</dd>
              <dt className="text-slate-500">Refund asked</dt>
              <dd className="font-semibold">{money(data.ticket.amount)}</dd>
              <dt className="text-slate-500">Rule triggered</dt>
              <dd>
                <span className="font-mono">{data.ticket.rule}</span> {data.rule_name}
              </dd>
              <dt className="text-slate-500">Agent's reason</dt>
              <dd className="italic">"{data.ticket.agent_reason}"</dd>
            </dl>

            <div>
              <p className="mb-1.5 font-medium">What the agent did</p>
              <ol className="space-y-1">
                {steps.map((row, i) => (
                  <li key={row.id} className={`flex justify-between gap-3 rounded px-2 py-1 ${outcomeStyle(row.outcome)}`} title={hoverText(row)}>
                    <span>
                      {i + 1}. {actionLabel(row)}
                    </span>
                    <span className="font-medium">{outcomeLabel(row)}</span>
                  </li>
                ))}
              </ol>
            </div>

            <div className="flex justify-end gap-2 border-t border-slate-200 pt-4">
              {data.ticket.status === 'pending' ? (
                <DecisionButtons id={id} busy={busy} onDecide={onDecide} />
              ) : (
                <span className="text-slate-500">This ticket is {data.ticket.status.replaceAll('_', ' ')}.</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Activity({ rows }) {
  if (rows.length === 0) {
    return <p className="mt-5 text-center text-sm text-slate-400">No activity yet.</p>
  }
  return (
    <div className="mt-5 overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
          <tr>
            <th className="px-3 py-2 font-medium">Time</th>
            <th className="px-3 py-2 font-medium">Agent</th>
            <th className="px-3 py-2 font-medium">Order</th>
            <th className="px-3 py-2 font-medium">Action</th>
            <th className="px-3 py-2 font-medium">Outcome</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={row.id} className={outcomeStyle(row.outcome)} title={hoverText(row)}>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">{timeOf(row.time)}</td>
              <td className="whitespace-nowrap px-3 py-2">{row.agent_name}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono">{row.order_id ? `#${row.order_id}` : '—'}</td>
              <td className="max-w-56 truncate px-3 py-2">{actionLabel(row)}</td>
              <td className="whitespace-nowrap px-3 py-2 font-medium">{outcomeLabel(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
