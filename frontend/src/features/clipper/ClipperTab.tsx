import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { Plus, Scissors, Sparkles, Trash2, Upload } from "lucide-react"

import { api, type AppState, type ClipperSource } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { PageHeader } from "@/components/PageHeader"
import { Input } from "@/components/ui/input"
import { IconButton } from "@/components/IconButton"
import { JobLog } from "@/components/JobLog"
import { confirm } from "@/components/confirm"
import { SourceDetail } from "./SourceDetail"

const STATUS_LABEL: Record<ClipperSource["status"], string> = {
  pending: "à analyser",
  transcribing: "transcription…",
  analyzing: "analyse…",
  rendering: "rendu…",
  done: "prêt",
  failed: "échec",
}

const STATUS_VARIANT: Record<
  ClipperSource["status"],
  "default" | "secondary" | "outline" | "destructive"
> = {
  pending: "secondary",
  transcribing: "outline",
  analyzing: "outline",
  rendering: "outline",
  done: "default",
  failed: "destructive",
}

const RUNNING_STATUSES: ClipperSource["status"][] = ["transcribing", "analyzing", "rendering"]

export function ClipperTab({
  state,
  refresh,
}: {
  state: AppState
  refresh: () => Promise<void>
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [newLink, setNewLink] = useState("")
  const [importJobId, setImportJobId] = useState<string | null>(null)
  const [inboxFiles, setInboxFiles] = useState<string[]>([])

  const [selectedId, setSelectedId] = useState<number | null>(null)
  // Un job d'analyse à la fois par source, indexé par id.
  const [analyzeJobs, setAnalyzeJobs] = useState<Record<number, string | null>>({})
  // Garde posée AU CLIC, avant la réponse serveur : `source.status` ne passe
  // à transcribing/analyzing/rendering qu'après refresh(), donc entre le clic
  // et ce refresh, `busy` (dérivé du statut serveur) vaut encore false et un
  // second clic partirait — même motif que `busyId` dans SourceDetail/VideoLibrary.
  const [analyzeClicked, setAnalyzeClicked] = useState<Set<number>>(new Set())

  const loadInbox = useCallback(async () => {
    try {
      const { files } = await api.clipperInbox()
      setInboxFiles(files)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }, [])

  // Récupère les téléchargements déjà présents (import lancé avant un rechargement).
  useEffect(() => {
    loadInbox()
  }, [loadInbox])

  const upload = async (file: File) => {
    setUploading(true)
    try {
      await api.uploadClipperSource(file)
      await refresh()
      toast.success("source ajoutée")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  const importLink = async () => {
    const url = newLink.trim()
    if (!url) return
    try {
      const { job_id } = await api.linkClipperSource(url)
      setImportJobId(job_id)
      setNewLink("")
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const onImportDone = useCallback(
    (status: "done" | "failed") => {
      if (status === "done") {
        loadInbox()
      } else {
        toast.error("échec de l'import — voir le journal")
      }
    },
    [loadInbox]
  )

  const promote = async (name: string) => {
    try {
      await api.promoteClipperInbox(name)
      await refresh()
      setInboxFiles((files) => files.filter((f) => f !== name))
      toast.success("source ajoutée")
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const toggle = (id: number) => setSelectedId((cur) => (cur === id ? null : id))

  const analyze = async (id: number) => {
    if (analyzeClicked.has(id)) return
    setAnalyzeClicked((ids) => new Set(ids).add(id))
    try {
      const { job_id } = await api.runClipperSource(id)
      setAnalyzeJobs((jobs) => ({ ...jobs, [id]: job_id }))
      setSelectedId(id)
    } catch (e) {
      setAnalyzeClicked((ids) => {
        const next = new Set(ids)
        next.delete(id)
        return next
      })
      toast.error((e as Error).message)
    }
  }

  const onAnalyzeDone = useCallback(
    (id: number, status: "done" | "failed") => {
      refresh()
      // Le JobLog est démonté/remonté à chaque pliage de la ligne (il n'est
      // rendu que quand `expanded`) : un jobId laissé en place se rejouerait
      // en entier — nouveau tick, nouveau "done", nouveau toast — à chaque
      // réouverture. Un job terminé n'a plus de journal à suivre.
      setAnalyzeJobs((jobs) => {
        const next = { ...jobs }
        delete next[id]
        return next
      })
      setAnalyzeClicked((ids) => {
        const next = new Set(ids)
        next.delete(id)
        return next
      })
      if (status === "failed") toast.error("l'analyse a échoué — voir le journal")
      else toast.success("analyse terminée")
    },
    [refresh]
  )

  const removeSource = async (id: number, title: string) => {
    const ok = await confirm({
      title: "Supprimer la source ?",
      description: `« ${title} » et ses clips seront effacés du disque.`,
    })
    if (!ok) return
    try {
      await api.deleteClipperSource(id)
      if (selectedId === id) setSelectedId(null)
      await refresh()
      toast.success("source supprimée")
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Clipper"
        subtitle="Une vidéo longue parlée devient des shorts classés par pertinence."
      />

      <Card>
        <CardHeader>
          <CardTitle>Ajouter une source</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <input
                ref={fileRef}
                type="file"
                accept="video/*"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) upload(file)
                }}
              />
              <Button onClick={() => fileRef.current?.click()} disabled={uploading}>
                <Upload /> {uploading ? "Envoi…" : "Uploader une vidéo"}
              </Button>
            </div>

            <div className="space-y-2">
              <div className="flex gap-2">
                <Input
                  value={newLink}
                  onChange={(e) => setNewLink(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && importLink()}
                  placeholder="Colle un lien YouTube…"
                />
                <Button variant="secondary" onClick={importLink}>
                  <Plus /> Importer
                </Button>
              </div>
            </div>
          </div>

          <JobLog jobId={importJobId} onDone={onImportDone} />

          {inboxFiles.length > 0 && (
            <div className="space-y-2 border-t pt-4">
              <p className="text-sm text-muted-foreground">Téléchargé(s), en attente d'ajout :</p>
              <ul className="space-y-1">
                {inboxFiles.map((name) => (
                  <li
                    key={name}
                    className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm"
                  >
                    <span className="flex-1 truncate font-mono text-xs text-muted-foreground">
                      {name}
                    </span>
                    <Button size="sm" variant="secondary" onClick={() => promote(name)}>
                      <Plus /> Ajouter
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {state.clipper_sources.length === 0 ? (
        <Empty className="border border-dashed">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Scissors />
            </EmptyMedia>
            <EmptyTitle>Aucune source</EmptyTitle>
            <EmptyDescription>Ajoute une vidéo longue pour commencer.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="space-y-3">
          {state.clipper_sources.map((source) => {
            const expanded = selectedId === source.id
            const busy = RUNNING_STATUSES.includes(source.status)
            return (
              <div key={source.id} className="rounded-lg border bg-card">
                <div
                  className="flex cursor-pointer items-center gap-3 px-4 py-3"
                  onClick={() => toggle(source.id)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{source.title}</span>
                      <Badge variant={STATUS_VARIANT[source.status]}>
                        {STATUS_LABEL[source.status]}
                      </Badge>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {source.clips.length} clip(s)
                    </span>
                    {source.status === "failed" && source.error && (
                      <p className="text-xs text-destructive">{source.error}</p>
                    )}
                  </div>
                  <IconButton
                    tip="Analyser"
                    className="text-muted-foreground"
                    disabled={busy || analyzeClicked.has(source.id)}
                    onClick={(e) => {
                      e.stopPropagation()
                      analyze(source.id)
                    }}
                  >
                    <Sparkles />
                  </IconButton>
                  <IconButton
                    tip="Supprimer la source"
                    className="text-muted-foreground"
                    onClick={(e) => {
                      e.stopPropagation()
                      removeSource(source.id, source.title)
                    }}
                  >
                    <Trash2 />
                  </IconButton>
                </div>

                {expanded && (
                  <div className="space-y-3 border-t px-4 py-3">
                    <JobLog
                      jobId={analyzeJobs[source.id] ?? null}
                      onDone={(status) => onAnalyzeDone(source.id, status)}
                    />
                    <SourceDetail clips={source.clips} refresh={refresh} />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
