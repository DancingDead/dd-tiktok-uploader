import { useState } from "react"
import { toast } from "sonner"
import { PageHeader } from "@/components/PageHeader"
import { api, type AppState, type Settings } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// Doit rester aligné sur webui.ALLOWED_WHISPER_MODELS : le serveur refuse en
// 400 tout autre nom (il finit dans un chargement de modèle faster-whisper).
const WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

export function SettingsTab({
  state,
  refresh,
}: {
  state: AppState
  refresh: () => Promise<void>
}) {
  const [local, setLocal] = useState<Settings>(state.settings)
  const [saving, setSaving] = useState(false)

  function setEffect(key: keyof Settings["effects"], value: boolean) {
    setLocal((s) => ({ ...s, effects: { ...s.effects, [key]: value } }))
  }

  function setAccent(key: keyof Settings["accents"], value: boolean) {
    setLocal((s) => ({ ...s, accents: { ...s.accents, [key]: value } }))
  }

  function setClipper(
    key: keyof Settings["clipper"],
    value: string | number | boolean,
  ) {
    setLocal((s) => ({ ...s, clipper: { ...s.clipper, [key]: value } }))
  }

  async function save() {
    setSaving(true)
    try {
      await api.saveSettings(local)
      toast.success("réglages enregistrés")
      await refresh()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Réglages du montage par défaut"
        subtitle="Base appliquée à tous les rendus. Chaque preset s'empile par-dessus ces valeurs."
      />

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Effets</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.zoom}
                onCheckedChange={(v) => setEffect("zoom", v === true)}
              />
              Punch-zoom
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.flash}
                onCheckedChange={(v) => setEffect("flash", v === true)}
              />
              Flash blanc
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.shake}
                onCheckedChange={(v) => setEffect("shake", v === true)}
              />
              Shake
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.speed}
                onCheckedChange={(v) => setEffect("speed", v === true)}
              />
              Slow-mo avant drop
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Accents</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.accents.rgb}
                onCheckedChange={(v) => setAccent("rgb", v === true)}
              />
              RGB split à l'impact
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={!!local.accents.glitch}
                onCheckedChange={(v) => setAccent("glitch", v === true)}
              />
              Micro-glitch
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Cadrage &amp; contenu</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.delogo}
                onCheckedChange={(v) =>
                  setLocal((s) => ({ ...s, delogo: v === true }))
                }
              />
              Gommer le logo (delogo)
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.chrono}
                onCheckedChange={(v) =>
                  setLocal((s) => ({ ...s, chrono: v === true }))
                }
              />
              Chronologie de l'histoire
            </label>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="min_presence">Présence personnages min</Label>
              <Input
                id="min_presence"
                type="number"
                step="0.05"
                min={0}
                max={1}
                className="w-20"
                value={local.min_presence}
                onChange={(e) =>
                  setLocal((s) => ({
                    ...s,
                    min_presence: parseFloat(e.target.value),
                  }))
                }
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Rythme</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="cut_mode">Mode</Label>
              <Select
                value={local.cut_mode}
                onValueChange={(v) =>
                  setLocal((s) => ({ ...s, cut_mode: v }))
                }
              >
                <SelectTrigger id="cut_mode" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="energy">énergie (auto)</SelectItem>
                  <SelectItem value="fixed">fixe</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="cut_every">Coupe tous les … beats (mode fixe)</Label>
              <Input
                id="cut_every"
                type="number"
                min={1}
                className="w-20"
                value={local.cut_every}
                onChange={(e) =>
                  setLocal((s) => ({ ...s, cut_every: Number(e.target.value) }))
                }
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="buildup">Buildup … s</Label>
              <Input
                id="buildup"
                type="number"
                className="w-20"
                value={local.buildup}
                onChange={(e) =>
                  setLocal((s) => ({ ...s, buildup: Number(e.target.value) }))
                }
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="strobe_beats">Strobo après drop … beats</Label>
              <Input
                id="strobe_beats"
                type="number"
                className="w-20"
                value={local.strobe_beats}
                onChange={(e) =>
                  setLocal((s) => ({
                    ...s,
                    strobe_beats: Number(e.target.value),
                  }))
                }
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Clipper</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="whisper_model">Modèle de transcription</Label>
              <Select
                value={local.clipper.whisper_model ?? "small"}
                onValueChange={(v) => setClipper("whisper_model", v)}
              >
                <SelectTrigger id="whisper_model" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WHISPER_MODELS.map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <p className="text-xs text-muted-foreground">
              Plus le modèle est gros, meilleure est la transcription — et plus
              l'analyse est longue : la transcription dure environ une fois la
              durée de la vidéo en « small », plusieurs fois en « large-v3 ».
            </p>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="clip_count">Shorts par source</Label>
              <Input
                id="clip_count"
                type="number"
                min={1}
                max={30}
                className="w-20"
                value={local.clipper.clip_count ?? 8}
                onChange={(e) => setClipper("clip_count", Number(e.target.value))}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="clipper_min_dur">Durée min … s</Label>
              <Input
                id="clipper_min_dur"
                type="number"
                min={3}
                max={180}
                className="w-20"
                value={local.clipper.min_dur ?? 15}
                onChange={(e) => setClipper("min_dur", Number(e.target.value))}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="clipper_max_dur">Durée max … s</Label>
              <Input
                id="clipper_max_dur"
                type="number"
                min={3}
                max={180}
                className="w-20"
                value={local.clipper.max_dur ?? 60}
                onChange={(e) => setClipper("max_dur", Number(e.target.value))}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="clipper_digest_chars">Transcript par appel</Label>
              <Input
                id="clipper_digest_chars"
                type="number"
                min={1000}
                max={60000}
                step={500}
                className="w-24"
                value={local.clipper.digest_chars ?? 6000}
                onChange={(e) => setClipper("digest_chars", Number(e.target.value))}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Taille de transcript envoyée en une fois au modèle : au-delà, la
              transcription est découpée en plusieurs appels. À régler d'après le
              contexte du modèle chargé dans LM Studio, en comptant environ
              2,3 caractères par token (6000 caractères ≈ 2600 tokens).
            </p>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.clipper.speaker_cuts ?? true}
                onCheckedChange={(v) => setClipper("speaker_cuts", v === true)}
              />
              Recadrer sur celui qui parle
            </label>
            <p className="text-xs text-muted-foreground">
              Le cadre suit l'intervenant qui parle et change de personne par une
              coupe franche. Fiable sur une vidéo déjà montée en plans découpés,
              beaucoup moins sur du plan large filmé à l'épaule, où le mouvement
              de caméra couvre le signal. Décoche pour revenir au suivi simple du
              plus grand visage.
            </p>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="clipper_min_shot">Durée minimale d'un plan (s)</Label>
              <Input
                id="clipper_min_shot"
                type="number"
                step="0.1"
                min={0.4}
                max={5}
                className="w-20"
                value={local.clipper.min_shot ?? 1.2}
                onChange={(e) => setClipper("min_shot", Number(e.target.value))}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              En dessous, le cadre clignote ; au-delà, il reste sur quelqu'un qui
              ne parle plus.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="flex justify-end">
        <Button onClick={save} disabled={saving}>
          Enregistrer
        </Button>
      </div>
    </div>
  )
}
