import { useCallback, useState } from "react"
import { toast } from "sonner"

import type { AppState } from "@/lib/api"
import { api } from "@/lib/api"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { JobLog } from "@/components/JobLog"
import { PageHeader } from "@/components/PageHeader"
import { AssetSection } from "./AssetSection"
import { TrimDialog } from "./TrimDialog"

type Props = {
  state: AppState
  refresh: () => Promise<void>
}

// Ressources partagées du label : les niches y piochent sons et clips.
export function Catalogue({ state, refresh }: Props) {
  // Clip dont la modale de rognage est ouverte.
  const [trimming, setTrimming] = useState<string | null>(null)
  // Rognage en cours : le nom sert à neutraliser les actions de sa ligne.
  const [trimJob, setTrimJob] = useState<{ id: string; name: string } | null>(null)

  const onTrimDone = useCallback(
    (status: "done" | "failed") => {
      refresh()
      // Un job terminé n'a plus de journal à suivre : le laisser en place le
      // rejouerait en entier (nouveau tick, nouveau « done », nouveau toast) au
      // prochain remontage du JobLog — motif d'`analyzeJobs` dans ClipperTab.
      setTrimJob(null)
      if (status === "done") toast.success("clip rogné")
      else toast.error("le rognage a échoué — voir le journal")
    },
    [refresh]
  )

  return (
    <>
    <PageHeader
      title="Catalogue"
      subtitle="Les ressources partagées du label — les niches y piochent leurs sons et leurs clips."
    />
    <Tabs defaultValue="sons" className="w-full">
      <TabsList>
        <TabsTrigger value="sons">Sons</TabsTrigger>
        <TabsTrigger value="clips">Clips</TabsTrigger>
      </TabsList>

      <TabsContent value="sons" className="pt-4">
        <AssetSection
          assets={state.tracks}
          linksText={state.links}
          accept=".wav,.mp3,.flac,.m4a,.ogg,.aiff"
          linkNote="Depuis YouTube (vidéo ou playlist) — « Télécharger » récupère l'audio en mp3 dans tracks/."
          downloadLabel="Télécharger les sons"
          emptyLabel="aucun son"
          onUpload={api.uploadTrack}
          onDelete={api.deleteTrack}
          onSaveLinks={api.saveLinks}
          onDownload={api.downloadTracks}
          refresh={refresh}
        />
      </TabsContent>

      <TabsContent value="clips" className="pt-4">
        <AssetSection
          assets={state.clips}
          linksText={state.clip_links}
          accept=".mp4,.mov,.m4v,.mkv,.webm,.avi,.jpg,.jpeg,.png,.webp"
          linkNote="Depuis YouTube (vidéo ou playlist) — « Télécharger » récupère la vidéo ≤1080p dans clips/."
          downloadLabel="Télécharger les clips"
          emptyLabel="aucun clip ni image"
          onUpload={api.uploadClip}
          onDelete={api.deleteClip}
          onSaveLinks={api.saveClipLinks}
          onDownload={api.downloadClips}
          refresh={refresh}
          onTrim={setTrimming}
          busyName={trimJob?.name ?? null}
        />
      </TabsContent>
    </Tabs>

    {/* Hors des Tabs, donc toujours monté : un rognage dure environ deux
        minutes, et Radix démonte le contenu de l'onglet inactif — suivre le
        job depuis l'onglet Clips le laisserait tomber au premier passage sur
        Sons (JobLog arrête son polling au démontage). */}
    {trimJob && (
      <div className="pt-4">
        <p className="text-sm text-muted-foreground">Rognage de « {trimJob.name} » en cours…</p>
        <JobLog jobId={trimJob.id} onDone={onTrimDone} />
      </div>
    )}

    <TrimDialog
      name={trimming}
      onClose={() => setTrimming(null)}
      // `trimming` porte encore le nom : la modale appelle onStarted AVANT onClose.
      onStarted={(jobId) => trimming && setTrimJob({ id: jobId, name: trimming })}
    />
    </>
  )
}
