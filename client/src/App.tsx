import { useEffect, useMemo, useState } from 'react'
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
  const [summaryInfo, setSummaryInfo] = useState<{cached:boolean, model:string, contentHash:string} | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)

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
    setSummary(null)
    setSummaryInfo(null)
    setSummaryError(null)
    setSummaryLoading(true)
    fetch(`/api/tests/${id}/summary`)
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: {summaryMarkdown: string, model: string, cached: boolean, contentHash: string}) => {
        setSummary(data.summaryMarkdown)
        setSummaryInfo({cached: data.cached, model: data.model, contentHash: data.contentHash})
      })
      .catch((e) => setSummaryError(String(e)))
      .finally(() => setSummaryLoading(false))
  }

  function refreshSummary() {
    if (!selectedId) return
    setSummaryLoading(true)
    setSummaryError(null)
    fetch(`/api/tests/${selectedId}/summary?refresh=true`)
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then((data: {summaryMarkdown: string, model: string, cached: boolean, contentHash: string}) => {
        setSummary(data.summaryMarkdown)
        setSummaryInfo({cached: data.cached, model: data.model, contentHash: data.contentHash})
      })
      .catch((e) => setSummaryError(String(e)))
      .finally(() => setSummaryLoading(false))
  }

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
              <div className="summary-actions">
                <button onClick={refreshSummary} disabled={!selectedId || summaryLoading}>Refresh</button>
              </div>
            </div>
            {!selectedId && <div className="empty">Select a test to see its summary.</div>}
            {selectedId && summaryLoading && <div className="loading">Summarizing…</div>}
            {selectedId && summaryError && <div className="error">{summaryError}</div>}
            {selectedId && !summaryLoading && !summaryError && summary && (
              <div>
                <ReactMarkdown>{summary}</ReactMarkdown>
                {summaryInfo && (
                  <div className="desc" style={{ marginTop: '0.5rem' }}>
                    Model: {summaryInfo.model} • Cached: {summaryInfo.cached ? 'Yes' : 'No'}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default App
