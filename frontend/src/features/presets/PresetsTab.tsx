import { useState } from "react"
import { Plus } from "lucide-react"
import type { AppState, Overrides } from "@/lib/api"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { PresetEditor } from "./PresetEditor"

// Modèles d'ambiance : pré-remplissent l'éditeur à la création.
const PRESET_TEMPLATES: Record<"doux" | "energique", Overrides> = {
  energique: {
    cut_mode: "energy",
    strobe_beats: 16,
    effects: { zoom: true, flash: true, shake: true, speed: true },
    accents: { rgb: true, glitch: true },
    subtitles: { font: "impact" },
  },
  doux: {
    cut_mode: "fixed",
    cut_every: 4,
    strobe_beats: 0,
    effects: { zoom: false, flash: false, shake: false, speed: false },
    accents: { rgb: false, glitch: false },
    subtitles: { font: "douce" },
  },
}

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
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              setSelectedId(null)
              setTemplate(PRESET_TEMPLATES.doux)
            }}
          >
            Doux
          </Button>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              setSelectedId(null)
              setTemplate(PRESET_TEMPLATES.energique)
            }}
          >
            Énergique
          </Button>
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
