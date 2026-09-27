import ApproverPage from './ApproverPage.jsx'
import ChatPage from './ChatPage.jsx'

// Two pages, so a plain path check instead of a router.
export default function App() {
  if (window.location.pathname.startsWith('/approver')) return <ApproverPage />
  if (window.location.pathname !== '/chat') window.history.replaceState(null, '', '/chat')
  return <ChatPage />
}
