import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import './App.css'

type TestSummary = {
  id?: string
  name: string
  description?: string
  filePath: string
  createdAt: string
  updatedAt: string
  stepCount: number
  labels: string[]
  disabled: boolean
}

function fmtDate(iso: string) {
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}

function App() {
  const [tests, setTests] = useState<TestSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [summary, setSummary] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const evtRef = useRef<EventSource | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/tests')
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: TestSummary[]) => {
        if (!cancelled) setTests(data)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [])

  const total = useMemo(() => (tests ? tests.length : 0), [tests])

  function selectTest(id?: string) {
    if (!id) return
    setSelectedId(id)
    setSummary("")
    setSummaryError(null)
    setSummaryLoading(true)
    // Close any existing stream
    evtRef.current?.close()
    const es = new EventSource(`/api/tests/${id}/summary/stream`)
    evtRef.current = es
    setStreaming(true)
    es.onmessage = (ev) => {
      try {
        const chunk = JSON.parse(ev.data) as string
        setSummary((prev) => (prev ?? "") + chunk)
        setSummaryLoading(false)
      } catch {
        // Ignore parse errors
      }
    }
    es.addEventListener('done', () => {
      setStreaming(false)
      es.close()
    })
    es.addEventListener('error', (ev) => {
      setStreaming(false)
      setSummaryLoading(false)
      setSummaryError(typeof ev === 'string' ? ev : 'Streaming error')
      es.close()
    })
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      evtRef.current?.close()
    }
  }, [evtRef])

  return (
    <div className="container">
      <header>
        <h1>Momentic Tests</h1>
        <div className="sub">Repository view</div>
      </header>

      {error && <div className="error">Failed to load: {error}</div>}

      {tests === null ? (
        <div className="loading">Loading tests…</div>
      ) : tests.length === 0 ? (
        <div className="empty">No tests found in <code>tests/</code>.</div>
      ) : (
        <>
          <div className="summary">{total} tests</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Steps</th>
                  <th>Created</th>
                  <th>Updated</th>
                  <th>Path</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {tests.map((t) => (
                  <tr key={t.filePath} onClick={() => selectTest(t.id)} style={{ cursor: t.id ? 'pointer' : 'default' }}>
                    <td>
                      <div className="name">{t.name}</div>
                      {t.description && <div className="desc">{t.description}</div>}
                    </td>
                    <td>{t.stepCount}</td>
                    <td>{fmtDate(t.createdAt)}</td>
                    <td>{fmtDate(t.updatedAt)}</td>
                    <td><code>{t.filePath}</code></td>
                    <td>
                      {t.disabled ? (
                        <span className="badge danger">Disabled</span>
                      ) : (
                        <span className="badge ok">Active</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="summary-panel">
            <div className="summary-header">
              <div className="summary-title">AI Summary</div>
              {streaming && <div className="desc">Streaming…</div>}
            </div>
            {!selectedId && <div className="empty">Select a test to see its summary.</div>}
            {selectedId && summaryLoading && <div className="loading">Summarizing…</div>}
            {selectedId && summaryError && <div className="error">{summaryError}</div>}
            {selectedId && !summaryLoading && !summaryError && summary && (
              <div>
                <ReactMarkdown>{summary}</ReactMarkdown>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default App
