import { createContext, useContext } from 'react'

// Single shared Context that exposes every piece of state + every handler
// declared in the App shell. Tab + panel components read from it via
// useCircuit() so the logic stays in App.jsx and only the JSX moves.
export const CircuitContext = createContext(null)

export function useCircuit() {
  const ctx = useContext(CircuitContext)
  if (!ctx) throw new Error('useCircuit must be used within a CircuitContext.Provider')
  return ctx
}