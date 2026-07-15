import { toast } from "sonner"
import { Check, Clapperboard, Download, Trash2, X } from "lucide-react"

import { api, type Video } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { IconButton } from "@/components/IconButton"
import { confirm } from "@/components/confirm"

const STATUS_LABEL: Record<Video["status"], string> = {
  proposed: "à valider",
  approved: "validée",
  rejected: "rejetée",
  posted: "postée",
  failed: "échec",
}

const STATUS_VARIANT: Record<
  Video["status"],
  "default" | "secondary" | "outline" | "destructive"
> = {
  proposed: "secondary",
  approved: "default",
  rejected: "destructive",
  posted: "outline",
  failed: "destructive",
}

function linesPreview(lines?: string[]): string | null {
  if (!lines || lines.length === 0) return null
  const shown = lines.slice(0, 3).join(" · ")
  return lines.length > 3 ? shown + " …" : shown
}

export function VideoLibrary({
  videos,
  refresh,
}: {
  videos: Video[]
  refresh: () => Promise<void>
}) {
  const setStatus = async (id: number, status: Video["status"], msg: string) => {
    try {
      await api.setVideoStatus(id, status)
      await refresh()
      toast.success(msg)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const remove = async (id: number) => {
    const ok = await confirm({
      description: "Supprimer définitivement cette vidéo ? Le fichier sera effacé du disque.",
    })
    if (!ok) return
    try {
      await api.deleteVideo(id)
      await refresh()
      toast.success("vidéo supprimée")
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  if (videos.length === 0) {
    return (
      <Empty className="border border-dashed">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Clapperboard />
          </EmptyMedia>
          <EmptyTitle>Aucune vidéo</EmptyTitle>
          <EmptyDescription>Sélectionne des sons puis clique « Générer ».</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <div
      className="grid gap-3"
      style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}
    >
      {[...videos].reverse().map((v) => {
        const preview = linesPreview(v.subtitles?.lines)
        return (
          <div key={v.id} className="flex flex-col gap-2 rounded-lg border bg-card p-2">
            {v.exists ? (
              <video
                src={api.videoUrl(v.id)}
                controls
                preload="metadata"
                className="w-full rounded bg-black"
                style={{ aspectRatio: "9/16" }}
              />
            ) : (
              <div
                className="flex w-full items-center justify-center rounded bg-black text-xs text-muted-foreground"
                style={{ aspectRatio: "9/16" }}
              >
                fichier manquant
              </div>
            )}

            <div className="flex items-center justify-between gap-2">
              <Badge variant={STATUS_VARIANT[v.status]}>{STATUS_LABEL[v.status]}</Badge>
              <span className="text-xs text-muted-foreground">seed {v.seed}</span>
            </div>

            {preview && (
              <p className="line-clamp-2 text-xs text-muted-foreground">{preview}</p>
            )}

            <div className="flex flex-wrap items-center gap-1">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setStatus(v.id, "approved", "vidéo validée")}
              >
                <Check /> Valider
              </Button>
              <IconButton
                tip="Rejeter"
                onClick={() => setStatus(v.id, "rejected", "vidéo rejetée")}
              >
                <X />
              </IconButton>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button asChild size="icon" variant="ghost">
                    <a href={api.videoUrl(v.id, true)}>
                      <Download />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Télécharger</TooltipContent>
              </Tooltip>
              <IconButton
                tip="Supprimer la vidéo"
                className="ml-auto text-muted-foreground"
                onClick={() => remove(v.id)}
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
