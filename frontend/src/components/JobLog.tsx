import { useEffect, useRef, useState } from "react"
import { api } from "@/lib/api"

type Props = {
  jobId: string | null
  onDone: (status: "done" | "failed") => void
  // Suivi perdu (serveur injoignable) : ce n'est PAS un échec du job, qui
  // continue de tourner côté serveur. Sans cette distinction, un appelant dont
  // l'opération est destructive débloquait son interface au bout de 7,5 s de
  // serveur muet et laissait relancer une opération déjà en cours. Optionnel :
  // les appelants qui ne le fournissent pas gardent l'ancien repli sur
  // onDone("failed"), suffisant pour un téléchargement.
  onLost?: () => void
}

// Au-delà de N erreurs de suivi CONSÉCUTIVES, on arrête de sonder et on le
// signale (via onLost, ou à défaut onDone("failed")) : sans ce plafond, un
// serveur injoignable laissait le job « en cours » pour toujours, sans jamais
// rien dire à l'utilisateur. Ce plafond ne vaut que 7,5 s de silence — bien
// moins qu'un recyclage de waitress ou un hoquet de Tailscale — d'où onLost :
// à cette échelle, « je ne sais plus » est la seule conclusion honnête.
const MAX_CONSECUTIVE_ERRORS = 5

// Suit un job de fond (téléchargement) par polling /api/jobs/<id>, comme
// followJob() dans l'app actuelle.
export function JobLog({ jobId, onDone, onLost }: Props) {
  const [log, setLog] = useState<string[]>([])
  const preRef = useRef<HTMLPreElement>(null)
  // onDone passe par une ref : le polling ne doit dépendre QUE de jobId. Sinon
  // une callback recréée par le parent à chaque render relance l'effet, ce qui
  // vidait le log affiché et redémarrait le suivi en cours de route.
  const onDoneRef = useRef(onDone)
  const onLostRef = useRef(onLost)
  useEffect(() => {
    onDoneRef.current = onDone
    onLostRef.current = onLost
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
            setLog((lines) => [
              ...lines,
              "— suivi interrompu : serveur injoignable. Le job continue peut-être.",
            ])
            if (onLostRef.current) onLostRef.current()
            else onDoneRef.current("failed")
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
