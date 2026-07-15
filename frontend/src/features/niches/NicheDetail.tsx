import { useState } from "react"
import { toast } from "sonner"
import { Play, Trash2 } from "lucide-react"

import { api, type AppState, type Niche } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { confirm } from "@/components/confirm"
import { JobLog } from "@/components/JobLog"
import { SelectionCard } from "./SelectionCard"
import { VideoLibrary } from "./VideoLibrary"

type Props = {
  niche: Niche
  state: AppState
  refresh: () => Promise<void>
  onDeleted: () => void
}

export function NicheDetail({ niche, state, refresh, onDeleted }: Props) {
  const [caption, setCaption] = useState(niche.caption_template)
  const [hashtagsInput, setHashtagsInput] = useState(niche.hashtags.join(", "))
  const [presetIds, setPresetIds] = useState<number[]>(niche.preset_ids)
  const [subsEnabled, setSubsEnabled] = useState(niche.subtitles?.enabled ?? false)
  const [preprompt, setPreprompt] = useState(niche.subtitles?.preprompt ?? "")
  const [count, setCount] = useState(niche.cadence || 1)
  const [jobId, setJobId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const togglePreset = (id: number, on: boolean) =>
    setPresetIds((ids) => (on ? [...new Set([...ids, id])] : ids.filter((x) => x !== id)))

  const save = async () => {
    setSaving(true)
    try {
      await api.updateNiche(niche.id, {
        cadence: count,
        caption_template: caption,
        hashtags: hashtagsInput
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        preset_ids: presetIds,
        subtitles: { enabled: subsEnabled, preprompt },
      })
      await refresh()
      toast.success("niche enregistrée")
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    const ok = await confirm({
      description: "Supprimer cette niche ? Les fichiers (clips, liens) resteront sur le disque.",
    })
    if (!ok) return
    try {
      await api.deleteNiche(niche.id)
      onDeleted()
      await refresh()
      toast.success("niche supprimée")
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const generate = async () => {
    try {
      const { job_id } = await api.generateNiche(niche.id, count)
      toast.success(`génération de ${count} variante(s) lancée`)
      setJobId(job_id)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <div className="mt-8 space-y-6">
      {/* Carte A — Réglages */}
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3 space-y-0">
          <CardTitle className="flex items-center gap-2">
            {niche.name}
            <Badge variant="secondary">{niche.slug}</Badge>
          </CardTitle>
          <Button variant="ghost" size="icon" title="Supprimer la niche" onClick={remove}>
            <Trash2 />
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="caption">Légende du post</Label>
            <Input
              id="caption"
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="hashtags">Hashtags</Label>
            <Input
              id="hashtags"
              value={hashtagsInput}
              onChange={(e) => setHashtagsInput(e.target.value)}
              placeholder="hardstyle, anime, edit"
            />
          </div>

          <div className="space-y-2">
            <Label>Presets de montage liés (alternés)</Label>
            {state.presets.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                aucun preset — crée-en un dans l'onglet Presets
              </p>
            ) : (
              <div className="space-y-2">
                {state.presets.map((p) => (
                  <label key={p.id} className="flex items-center gap-3 text-sm">
                    <Checkbox
                      checked={presetIds.includes(p.id)}
                      onCheckedChange={(v) => togglePreset(p.id, v === true)}
                    />
                    {p.name}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={subsEnabled}
                onCheckedChange={(v) => setSubsEnabled(v === true)}
              />
              Punchlines incrustées (générées)
            </label>
            <Textarea
              value={preprompt}
              onChange={(e) => setPreprompt(e.target.value)}
              placeholder="pré-prompt des punchlines, ex. « motivation gym, français, percutant, 4 mots max »"
            />
          </div>

          <div className="flex justify-end">
            <Button onClick={save} disabled={saving}>
              Enregistrer la niche
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Carte B — Sons */}
      <SelectionCard
        title="Sons de la niche"
        description="La génération tire un de ces morceaux au hasard par variante. Retrait et ajout sont immédiats ; « retirer » ne supprime pas le fichier du catalogue partagé."
        prefix="tracks/"
        selected={niche.tracks}
        catalogue={state.tracks}
        emptySelected="aucun son — ajoute-en depuis le catalogue ci-dessous"
        emptyCatalogue="catalogue vide — ajoute des morceaux via l'onglet Catalogue"
        addedToast="son ajouté à la niche"
        removedToast="son retiré de la niche"
        onChange={async (tracks) => {
          await api.updateNiche(niche.id, { tracks })
          await refresh()
        }}
      />

      {/* Carte C — Clips */}
      <SelectionCard
        title="Clips de la niche"
        description="La génération tire un de ces morceaux au hasard par variante. Retrait et ajout sont immédiats ; « retirer » ne supprime pas le fichier du catalogue partagé."
        prefix="clips/"
        selected={niche.clips}
        catalogue={state.clips}
        emptySelected="aucun clip — ajoute-en depuis le catalogue ci-dessous"
        emptyCatalogue="catalogue vide — ajoute des clips via l'onglet Catalogue"
        addedToast="clip ajouté à la niche"
        removedToast="clip retiré de la niche"
        onChange={async (clips) => {
          await api.updateNiche(niche.id, { clips })
          await refresh()
        }}
      />

      {/* Carte D — Génération */}
      <Card>
        <CardHeader>
          <CardTitle>Génération</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="number"
              min={1}
              max={20}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              style={{ width: "70px" }}
            />
            <span className="text-sm text-muted-foreground">variante(s)</span>
            <Button onClick={generate}>
              <Play /> Générer
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Chaque variante = un morceau + une seed différents → montage et punchlines distincts.
          </p>
          <JobLog jobId={jobId} onDone={() => refresh()} />

          <div className="space-y-3">
            <h3 className="text-sm font-semibold">
              Bibliothèque — {niche.videos.length} vidéo(s)
            </h3>
            <VideoLibrary videos={niche.videos} refresh={refresh} />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
