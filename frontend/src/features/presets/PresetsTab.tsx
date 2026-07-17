import { useState } from "react"
import { Plus } from "lucide-react"
import type { AppState, Overrides } from "@/lib/api"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { PresetEditor } from "./PresetEditor"

// Modèles d'ambiance : pré-remplissent l'éditeur à la création.
const PRESET_TEMPLATES: Record<string, Overrides> = {
  doux: {
    cut_mode: "fixed", cut_every: 4, strobe_beats: 0,
    effects: { zoom: false, flash: false, shake: false, speed: false },
    accents: { rgb: false, glitch: 0 },
    color_grade: "chaud", grain: 0.1, clip_speed: 0.9,
    subtitles: { font: "douce" },
  },
  chill: {
    cut_mode: "energy", strobe_beats: 0,
    effects: { zoom: true, flash: false, shake: false, speed: false },
    accents: { rgb: false, glitch: 0 },
    color_grade: "delave", grain: 0.2, clip_speed: 0.85,
    subtitles: { font: "elegante" },
  },
  energique: {
    cut_mode: "energy", strobe_beats: 16,
    effects: { zoom: true, flash: true, shake: true, speed: true },
    accents: { rgb: true, glitch: 0.35 },
    color_grade: "froid", grain: 0, clip_speed: 1.0,
    subtitles: { font: "impact" },
  },
  cinematique: {
    cut_mode: "energy", strobe_beats: 0,
    effects: { zoom: true, flash: false, shake: false, speed: true },
    accents: { rgb: false, glitch: 0 },
    color_grade: "froid", grain: 0.1, clip_speed: 0.9,
    subtitles: { font: "elegante" },
  },
  retro: {
    cut_mode: "fixed", cut_every: 2, strobe_beats: 0,
    effects: { zoom: false, flash: false, shake: false, speed: false },
    accents: { rgb: true, glitch: 0.7 },
    color_grade: "delave", grain: 0.8, clip_speed: 1.0,
    subtitles: { font: "sobre" },
  },
}

const TEMPLATE_BUTTONS: { key: keyof typeof PRESET_TEMPLATES; label: string }[] = [
  { key: "doux", label: "Doux" },
  { key: "chill", label: "Chill / Lo-fi" },
  { key: "energique", label: "Énergique / Phonk" },
  { key: "cinematique", label: "Cinématique" },
  { key: "retro", label: "Rétro / VHS" },
]

type Props = { state: AppState; refresh: () => Promise<void> }

export function PresetsTab({ state, refresh }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [template, setTemplate] = useState<Overrides | undefined>()
  const presets = state.presets
  const selected = presets.find((p) => p.id === selectedId) ?? null

  return (
    <>
      <PageHeader
        title="Presets de montage"
        subtitle="Des styles nommés (« strobo hard », « posé »…) réutilisables entre niches."
      />
      <div className="flex gap-6">
        <div className="flex w-[180px] min-w-[180px] flex-col gap-2">
          {presets.length === 0 && (
            <p className="text-sm text-muted-foreground">aucun preset</p>
          )}
          {presets.map((p) => {
            const active = p.id === selectedId
            return (
              <button
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                className={`truncate rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                  active
                    ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary"
                    : "border-border hover:bg-accent"
                }`}
              >
                {p.name}
              </button>
            )
          })}
          <Button
            variant="secondary"
            className="w-full"
            onClick={() => {
              setSelectedId(null)
              setTemplate(undefined)
            }}
          >
            <Plus /> Nouveau
          </Button>
          <p className="mt-2 text-xs text-muted-foreground">Partir d'un modèle :</p>
          {TEMPLATE_BUTTONS.map((t) => (
            <Button
              key={t.key}
              variant="outline"
              className="w-full"
              onClick={() => {
                setSelectedId(null)
                setTemplate(PRESET_TEMPLATES[t.key])
              }}
            >
              {t.label}
            </Button>
          ))}
        </div>

        <PresetEditor
          key={selectedId ?? (template ? JSON.stringify(template) : "new")}
          preset={selected}
          template={template}
          onSaved={(id) => {
            setTemplate(undefined)
            setSelectedId(id)
          }}
          onDeleted={() => setSelectedId(null)}
          refresh={refresh}
        />
      </div>
    </>
  )
}
