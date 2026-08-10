import { useState } from "react"

import type { AppState } from "@/lib/api"
import { api } from "@/lib/api"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { JobLog } from "@/components/JobLog"
import { PageHeader } from "@/components/PageHeader"
import { AssetSection } from "./AssetSection"
import { TrimDialog } from "./TrimDialog"

// Rognages en cours, indexés par nom de clip. Un seul job à la fois ne suffit
// pas : rogner B pendant que A tourne écrasait A, ce qui rendait à sa ligne son
// bouton de suppression alors que son ffmpeg travaillait encore — précisément le
// chemin destructif que le verrouillage ferme. Motif d'`analyzeJobs` (ClipperTab).
export type TrimJobs = Record<string, string>

type Props = {
  state: AppState
  refresh: () => Promise<void>
  trimJobs: TrimJobs
  onTrimStarted: (name: string, jobId: string) => void
}

// Ressources partagées du label : les niches y piochent sons et clips.
export function Catalogue({ state, refresh, trimJobs, onTrimStarted }: Props) {
  // Clip dont la modale de rognage est ouverte.
  const [trimming, setTrimming] = useState<string | null>(null)

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
          busyNames={Object.keys(trimJobs)}
        />
      </TabsContent>
    </Tabs>

    <TrimDialog
      name={trimming}
      onClose={() => setTrimming(null)}
      // `trimming` porte encore le nom : la modale appelle onStarted AVANT onClose.
      onStarted={(jobId) => trimming && onTrimStarted(trimming, jobId)}
    />
    </>
  )
}

// Journal des rognages en cours. Monté par <Shell/> et NON par <Catalogue/> :
// un rognage dure environ deux minutes, et quitter l'onglet démonte le
// Catalogue en entier — le polling de JobLog s'arrête à son démontage, donc
// plus de refresh(), plus de toast, et la ligne resterait verrouillée à vie.
// Seul son affichage est réservé à l'onglet Catalogue (motif de ClipperTab).
export function TrimJobsPanel({
  jobs,
  visible,
  onDone,
}: {
  jobs: TrimJobs
  visible: boolean
  onDone: (name: string, status: "done" | "failed") => void
}) {
  const names = Object.keys(jobs)
  if (names.length === 0) return null
  return (
    <div className={visible ? "pt-4 space-y-3" : "hidden"}>
      {names.map((name) => (
        <div key={name}>
          <p className="text-sm text-muted-foreground">Rognage de « {name} » en cours…</p>
          <JobLog jobId={jobs[name]} onDone={(status) => onDone(name, status)} />
        </div>
      ))}
    </div>
  )
}
