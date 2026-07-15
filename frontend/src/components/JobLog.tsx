import { useEffect, useState } from "react"
import { api } from "@/lib/api"

type Props = {
  jobId: string | null
  onDone: (status: "done" | "failed") => void
}

// Suit un job de fond (téléchargement) par polling /api/jobs/<id>, comme
// followJob() dans l'app actuelle.
export function JobLog({ jobId, onDone }: Props) {
  const [log, setLog] = useState<string[]>([])

  useEffect(() => {
    if (!jobId) return
    let stopped = false
    setLog([])
    const tick = async () => {
      try {
        const job = await api.job(jobId)
        if (stopped) return
        setLog(job.log)
        if (job.status !== "running") {
          onDone(job.status)
          return
        }
      } catch {
        /* on retente au prochain tick */
      }
      if (!stopped) setTimeout(tick, 1500)
    }
    tick()
    return () => {
      stopped = true
    }
    // onDone est stable (useCallback côté parent)
  }, [jobId, onDone])

  if (!jobId) return null
  return (
    <pre className="mt-2 max-h-48 overflow-auto rounded-md border bg-black/40 p-3 font-mono text-xs whitespace-pre-wrap text-muted-foreground">
      {log.join("\n") || "démarrage…"}
    </pre>
  )
}
