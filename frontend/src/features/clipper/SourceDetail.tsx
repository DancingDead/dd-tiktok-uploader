import { useState } from "react"
import { toast } from "sonner"
import { Check, Download, Scissors, Trash2, X } from "lucide-react"

import { api, type ClipperClip } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { IconButton } from "@/components/IconButton"
import { confirm } from "@/components/confirm"

const STATUS_LABEL: Record<ClipperClip["status"], string> = {
  proposed: "à valider",
  approved: "validé",
  rejected: "rejeté",
  posted: "posté",
}

const STATUS_VARIANT: Record<
  ClipperClip["status"],
  "default" | "secondary" | "outline" | "destructive"
> = {
  proposed: "secondary",
  approved: "default",
  rejected: "destructive",
  posted: "outline",
}

function fmtTime(s: number): string {
  const total = Math.round(s)
  const m = Math.floor(total / 60)
  const sec = total % 60
  return `${m}:${sec.toString().padStart(2, "0")}`
}

export function SourceDetail({
  clips,
  refresh,
}: {
  clips: ClipperClip[]
  refresh: () => Promise<void>
}) {
  // Une action à la fois par clip : sans ça, un double-clic sur Valider/Rejeter/
  // Supprimer envoyait deux requêtes.
  const [busyId, setBusyId] = useState<number | null>(null)

  const setStatus = async (id: number, status: ClipperClip["status"], msg: string) => {
    setBusyId(id)
    try {
      await api.setClipperClipStatus(id, status)
      await refresh()
      toast.success(msg)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (id: number) => {
    const ok = await confirm({
      description: "Supprimer définitivement ce clip ? Le fichier sera effacé du disque.",
    })
    if (!ok) return
    setBusyId(id)
    try {
      await api.deleteClipperClip(id)
      await refresh()
      toast.success("clip supprimé")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  if (clips.length === 0) {
    return (
      <Empty className="border border-dashed">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Scissors />
          </EmptyMedia>
          <EmptyTitle>Aucun clip</EmptyTitle>
          <EmptyDescription>Lance l'analyse pour extraire des clips de cette source.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <div className="space-y-3">
      {clips.map((c) => {
        const busy = busyId === c.id
        return (
          <div key={c.id} className="flex flex-col gap-3 rounded-lg border bg-card p-3 sm:flex-row">
            <video
              controls
              preload="metadata"
              src={api.clipperClipUrl(c.id)}
              className="max-w-[240px] rounded bg-black"
            />

            <div className="flex flex-1 items-start gap-4">
              <div className="flex flex-col items-center">
                <span className="text-3xl font-semibold">{Math.round(c.score)}</span>
                <span className="text-xs text-muted-foreground">
                  hook {Math.round(c.hook)} · flow {Math.round(c.flow)} · value {Math.round(c.value)}
                </span>
              </div>

              <div className="flex-1 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{c.title}</span>
                  <Badge variant={STATUS_VARIANT[c.status]}>{STATUS_LABEL[c.status]}</Badge>
                </div>
                <p className="text-sm text-muted-foreground">{c.why}</p>
                <p className="text-xs text-muted-foreground">
                  {fmtTime(c.start)} → {fmtTime(c.end)} · {Math.round(c.end - c.start)} s
                </p>
              </div>
            </div>

            <div className="flex items-center gap-1 sm:flex-col">
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => setStatus(c.id, "approved", "clip validé")}
              >
                <Check /> Valider
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setStatus(c.id, "rejected", "clip rejeté")}
              >
                <X /> Rejeter
              </Button>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button asChild size="icon" variant="ghost">
                    <a href={api.clipperClipUrl(c.id, true)}>
                      <Download />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Télécharger</TooltipContent>
              </Tooltip>
              <IconButton
                tip="Supprimer définitivement"
                className="text-destructive/80 hover:text-destructive"
                disabled={busy}
                onClick={() => remove(c.id)}
              >
                <Trash2 />
              </IconButton>
            </div>
          </div>
        )
      })}
    </div>
  )
}
