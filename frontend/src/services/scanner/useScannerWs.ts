import { useEffect, useRef } from "react"
import type { ScanCandidate, ScannerWsFrame } from "./types"

const BACKOFF = [500, 1500, 5000, 15000] as const

interface UseScannerWsOptions {
  scanId: string
  onCandidate: (c: ScanCandidate) => void
  onCommentaryReady: (canonicalId: string, commentary: string) => void
  onRunCompleted: (runId: string, count: number) => void
}

export function useScannerWs({
  scanId,
  onCandidate,
  onCommentaryReady,
  onRunCompleted,
}: UseScannerWsOptions): void {
  const wsRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const mountedRef = useRef(true)
  const onCandidateRef = useRef(onCandidate)
  const onCommentaryRef = useRef(onCommentaryReady)
  const onCompletedRef = useRef(onRunCompleted)

  useEffect(() => {
    onCandidateRef.current = onCandidate
    onCommentaryRef.current = onCommentaryReady
    onCompletedRef.current = onRunCompleted
  })

  useEffect(() => {
    mountedRef.current = true

    function connect() {
      if (!mountedRef.current) return
      const protocol = window.location.protocol === "https:" ? "wss" : "ws"
      const url = `${protocol}://${window.location.host}/ws/scanner/runs/${scanId}`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onmessage = (e) => {
        try {
          const frame = JSON.parse(e.data as string) as ScannerWsFrame
          if (frame.v !== 1) return
          if (frame.type === "candidate") onCandidateRef.current(frame.candidate)
          if (frame.type === "commentary_ready") {
            onCommentaryRef.current(frame.canonical_id, frame.commentary)
          }
          if (frame.type === "run_completed") {
            onCompletedRef.current(frame.run_id, frame.candidate_count)
          }
        } catch {
          // ignore malformed frames
        }
      }

      ws.onopen = () => {
        attemptRef.current = 0
      }

      ws.onclose = () => {
        if (!mountedRef.current) return
        const delay = BACKOFF[Math.min(attemptRef.current, BACKOFF.length - 1)]
        attemptRef.current++
        setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      mountedRef.current = false
      wsRef.current?.close()
    }
  }, [scanId])
}
