import { useEffect, useRef, useState } from "react"
import { api } from "@/lib/api"

type Props = {
  jobId: string | null
  onDone: (status: "done" | "failed") => void
}

// Au-delà de N erreurs de suivi CONSÉCUTIVES, on arrête de sonder et on signale
// l'échec : sans ce plafond, un serveur injoignable laissait le job « en cours »
// pour toujours, sans jamais rien dire à l'utilisateur.
const MAX_CONSECUTIVE_ERRORS = 5

// Suit un job de fond (téléchargement) par polling /api/jobs/<id>, comme
// followJob() dans l'app actuelle.
export function JobLog({ jobId, onDone }: Props) {
  const [log, setLog] = useState<string[]>([])
  const preRef = useRef<HTMLPreElement>(null)
  // onDone passe par une ref : le polling ne doit dépendre QUE de jobId. Sinon
  // une callback recréée par le parent à chaque render relance l'effet, ce qui
  // vidait le log affiché et redémarrait le suivi en cours de route.
  const onDoneRef = useRef(onDone)
  useEffect(() => {
    onDoneRef.current = onDone
  })

  useEffect(() => {
    if (!jobId) return
    let stopped = false
    let errors = 0
    setLog([])
    const tick = async () => {
      try {
        const job = await api.job(jobId)
        if (stopped) return
        errors = 0
        setLog(job.log)
        if (job.status !== "running") {
          onDoneRef.current(job.status)
          return
        }
      } catch {
        errors += 1
        if (errors >= MAX_CONSECUTIVE_ERRORS) {
          if (!stopped) {
            setLog((lines) => [...lines, "— suivi interrompu : serveur injoignable"])
            onDoneRef.current("failed")
          }
          return
        }
      }
      if (!stopped) setTimeout(tick, 1500)
    }
    tick()
    return () => {
      stopped = true
    }
  }, [jobId])

  // Auto-scroll vers le bas à chaque nouvelle ligne (le dernier log reste visible).
  useEffect(() => {
    const el = preRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [log])

  if (!jobId) return null
  // Hauteur FIXE (h-48) + scroll : la div ne « saute » plus quand on passe de
  // « démarrage… » aux logs, ni quand ils s'allongent.
  return (
    <pre
      ref={preRef}
      className="mt-2 h-48 overflow-auto rounded-md border bg-black/40 p-3 font-mono text-xs whitespace-pre-wrap text-muted-foreground"
    >
      {log.join("\n") || "démarrage…"}
    </pre>
  )
}
