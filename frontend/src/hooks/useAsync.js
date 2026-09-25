// useAsync — fetch-into-state hook for the QSSC dashboard.
//
// Extracted from App.jsx. Wraps an async producer in { data, error, loading,
// reload } with a stale-response guard: when deps change a new fetch starts
// while the old one may still be in flight, so each invocation is tagged and
// resolutions from any but the latest are ignored (a slow old request can
// never overwrite the fresh data). The unmount effect sets the id to -1 so a
// late resolution after unmount is dropped.
import { useState, useCallback, useRef, useEffect } from 'react'

export function useAsync(fn, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const reqId = useRef(0)
  const run = useCallback(() => {
    const id = ++reqId.current
    setLoading(true); setError(null)
    Promise.resolve(fn())
      .then(d => { if (id === reqId.current) setData(d) })
      .catch(e => { if (id === reqId.current) setError(e.message) })
      .finally(() => { if (id === reqId.current) setLoading(false) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(run, [run])
  useEffect(() => () => { reqId.current = -1 }, [])
  return { data, error, loading, reload: run }
}