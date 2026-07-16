import { useState } from "react"
import { api, type Overrides, type Preset } from "@/lib/api"
import { toast } from "sonner"
import { confirm } from "@/components/confirm"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// Polices embarquées (assets/fonts/) — noms logiques côté moteur.
const CAPTION_FONTS = [
  { value: "impact", label: "Impact (edit)" },
  { value: "classique", label: "Classique (TikTok)" },
  { value: "sobre", label: "Sobre" },
  { value: "condensee", label: "Condensée (sport)" },
  { value: "douce", label: "Douce (arrondie)" },
  { value: "elegante", label: "Élégante (fine)" },
] as const

type Props = {
  preset: Preset | null
  template?: Overrides // pré-remplissage à la création (modèles Doux/Énergique)
  onSaved: (id: number) => void
  onDeleted: () => void
  refresh: () => Promise<void>
}

// Case à cocher avec libellé — pour les effets et accents booléens.
function Toggle({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  children: React.ReactNode
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <Checkbox checked={checked} onCheckedChange={(v) => onChange(v === true)} />
      {children}
    </label>
  )
}

// Champ numérique — stocke un number (retombe à 0 si vide/invalide).
function NumberField({
  id,
  label,
  value,
  onChange,
  step,
  min,
  max,
}: {
  id: string
  label: string
  value: number
  onChange: (v: number) => void
  step?: number
  min?: number
  max?: number
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        className="w-32"
        step={step}
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
      />
    </div>
  )
}

export function PresetEditor({ preset, template, onSaved, onDeleted, refresh }: Props) {
  const o = preset?.overrides ?? template ?? {}
  const [name, setName] = useState(preset?.name ?? "")
  const [zoom, setZoom] = useState(o.effects?.zoom ?? false)
  const [flash, setFlash] = useState(o.effects?.flash ?? false)
  const [shake, setShake] = useState(o.effects?.shake ?? false)
  const [speed, setSpeed] = useState(o.effects?.speed ?? false)
  const [rgb, setRgb] = useState(o.accents?.rgb ?? false)
  const [glitch, setGlitch] = useState(o.accents?.glitch ?? false)
  const [delogo, setDelogo] = useState(o.delogo ?? false)
  const [chrono, setChrono] = useState(o.chrono ?? false)
  const [minPresence, setMinPresence] = useState(o.min_presence ?? 0)
  const [cutMode, setCutMode] = useState(o.cut_mode ?? "energy")
  const [cutEvery, setCutEvery] = useState(o.cut_every ?? 2)
  const [buildup, setBuildup] = useState(o.buildup ?? 10)
  const [strobeBeats, setStrobeBeats] = useState(o.strobe_beats ?? 16)
  const [font, setFont] = useState(o.subtitles?.font ?? "impact")
  const [busy, setBusy] = useState(false)

  const isNew = preset === null

  async function save() {
    if (!name.trim()) {
      toast.error("nom requis")
      return
    }
    const overrides: Overrides = {
      effects: { zoom, flash, shake, speed },
      accents: { rgb, glitch },
      delogo,
      chrono,
      min_presence: minPresence,
      cut_mode: cutMode,
      cut_every: cutEvery,
      buildup,
      strobe_beats: strobeBeats,
      subtitles: { font },
    }
    setBusy(true)
    try {
      if (isNew) {
        const { id } = await api.createPreset(name.trim(), overrides)
        await refresh()
        toast.success("enregistré")
        onSaved(id)
      } else {
        await api.updatePreset(preset.id, name.trim(), overrides)
        await refresh()
        toast.success("enregistré")
      }
    } catch {
      toast.error("échec de l'enregistrement")
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (isNew) return
    if (!(await confirm({ description: "Supprimer ce preset ?" }))) return
    setBusy(true)
    try {
      await api.deletePreset(preset.id)
      await refresh()
      toast.success("preset supprimé")
      onDeleted()
    } catch {
      toast.error("échec de la suppression")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex-1 space-y-4">
      <div className="grid gap-1.5">
        <Label htmlFor="preset-name">Nom</Label>
        <Input
          id="preset-name"
          placeholder="ex. strobo hard"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Effets</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={zoom} onChange={setZoom}>
            Punch-zoom
          </Toggle>
          <Toggle checked={flash} onChange={setFlash}>
            Flash blanc
          </Toggle>
          <Toggle checked={shake} onChange={setShake}>
            Shake
          </Toggle>
          <Toggle checked={speed} onChange={setSpeed}>
            Slow-mo avant drop
          </Toggle>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Accents</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={rgb} onChange={setRgb}>
            RGB split à l'impact
          </Toggle>
          <Toggle checked={glitch} onChange={setGlitch}>
            Micro-glitch
          </Toggle>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cadrage & contenu</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={delogo} onChange={setDelogo}>
            Gommer le logo (delogo)
          </Toggle>
          <Toggle checked={chrono} onChange={setChrono}>
            Chronologie de l'histoire
          </Toggle>
          <NumberField
            id="min-presence"
            label="Présence personnages min"
            value={minPresence}
            onChange={setMinPresence}
            step={0.05}
            min={0}
            max={1}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Punchlines</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-1.5">
            <Label>Police</Label>
            <Select value={font} onValueChange={setFont}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CAPTION_FONTS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Rythme</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label>Mode</Label>
            <Select value={cutMode} onValueChange={setCutMode}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="energy">énergie (auto)</SelectItem>
                <SelectItem value="fixed">fixe</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <NumberField
            id="cut-every"
            label="Coupe tous les … beats"
            value={cutEvery}
            onChange={setCutEvery}
            min={1}
          />
          <NumberField
            id="buildup"
            label="Buildup … s"
            value={buildup}
            onChange={setBuildup}
          />
          <NumberField
            id="strobe-beats"
            label="Strobo après drop … beats"
            value={strobeBeats}
            onChange={setStrobeBeats}
          />
        </CardContent>
      </Card>

      <div className="flex items-center gap-2">
        <Button onClick={save} disabled={busy}>
          {isNew ? "Créer le preset" : "Enregistrer"}
        </Button>
        {!isNew && (
          <Button variant="destructive" onClick={remove} disabled={busy}>
            Supprimer
          </Button>
        )}
      </div>
    </div>
  )
}
