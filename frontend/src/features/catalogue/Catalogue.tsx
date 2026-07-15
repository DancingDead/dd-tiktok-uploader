import type { AppState } from "@/lib/api"
import { api } from "@/lib/api"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PageHeader } from "@/components/PageHeader"
import { AssetSection } from "./AssetSection"

type Props = {
  state: AppState
  refresh: () => Promise<void>
}

// Ressources partagées du label : les niches y piochent sons et clips.
export function Catalogue({ state, refresh }: Props) {
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
          accept=".mp4,.mov,.m4v,.mkv,.webm,.avi"
          linkNote="Depuis YouTube (vidéo ou playlist) — « Télécharger » récupère la vidéo ≤1080p dans clips/."
          downloadLabel="Télécharger les clips"
          emptyLabel="aucun clip"
          onUpload={api.uploadClip}
          onDelete={api.deleteClip}
          onSaveLinks={api.saveClipLinks}
          onDownload={api.downloadClips}
          refresh={refresh}
        />
      </TabsContent>
    </Tabs>
    </>
  )
}
