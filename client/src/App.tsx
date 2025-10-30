import { useEffect, useMemo, useState } from 'react'
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
                  <th>Disabled</th>
                </tr>
              </thead>
              <tbody>
                {tests.map((t) => (
                  <tr key={t.filePath}>
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
                        <span className="badge danger">True</span>
                      ) : (
                        <span className="badge ok">False</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

export default App
