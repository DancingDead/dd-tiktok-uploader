import { useState } from "react"
import { Plus } from "lucide-react"
import type { AppState } from "@/lib/api"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/ui/button"
import { PresetEditor } from "./PresetEditor"

type Props = { state: AppState; refresh: () => Promise<void> }

export function PresetsTab({ state, refresh }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
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
            onClick={() => setSelectedId(null)}
          >
            <Plus /> Nouveau
          </Button>
        </div>

        <PresetEditor
          key={selectedId ?? "new"}
          preset={selected}
          onSaved={(id) => setSelectedId(id)}
          onDeleted={() => setSelectedId(null)}
          refresh={refresh}
        />
      </div>
    </>
  )
}
