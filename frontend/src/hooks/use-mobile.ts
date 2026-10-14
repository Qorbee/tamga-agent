import * as React from "react"

const MOBILE_BREAKPOINT = 768
const QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`

function subscribe(onChange: () => void) {
  const mql = window.matchMedia(QUERY)
  mql.addEventListener("change", onChange)
  return () => mql.removeEventListener("change", onChange)
}

// Read through useSyncExternalStore, not set in an effect: the React Compiler's lint rejects a
// setState in an effect body, and the server render assumes a desktop.
export function useIsMobile() {
  return React.useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false
  )
}

const FINE = "(pointer: fine)"

function subscribeFine(onChange: () => void) {
  const mql = window.matchMedia(FINE)
  mql.addEventListener("change", onChange)
  return () => mql.removeEventListener("change", onChange)
}

// Whether the main pointer is a mouse or a pen: a field may take focus by itself there, while on a
// touch screen focus brings the keyboard up over half the screen. The server render assumes touch.
export function useFinePointer() {
  return React.useSyncExternalStore(
    subscribeFine,
    () => window.matchMedia(FINE).matches,
    () => false
  )
}
