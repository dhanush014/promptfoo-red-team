async function request(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export const api = {
  chat: (threadId, message) => request('POST', '/chat', { thread_id: threadId, message }),
  history: (threadId) => request('GET', `/chat/${threadId}`),
  agent: () => request('GET', '/agent'),
  tickets: (status) => request('GET', status ? `/tickets?status=${status}` : '/tickets'),
  ticket: (id) => request('GET', `/tickets/${id}`),
  approve: (id) => request('POST', `/tickets/${id}/approve`),
  deny: (id) => request('POST', `/tickets/${id}/deny`),
  audit: () => request('GET', '/audit'),
  reset: () => request('POST', '/demo/reset'),
}

export function money(n) {
  const value = Number(n)
  return Number.isInteger(value) ? `$${value}` : `$${value.toFixed(2)}`
}
