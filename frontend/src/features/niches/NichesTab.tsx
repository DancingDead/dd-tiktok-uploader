import { useState } from "react"
import { toast } from "sonner"
import { Plus } from "lucide-react"

import { api, type AppState } from "@/lib/api"
import { PageHeader } from "@/components/PageHeader"
import { promptText } from "@/components/confirm"
import { NicheDetail } from "./NicheDetail"

export function NichesTab({
  state,
  refresh,
}: {
  state: AppState
  refresh: () => Promise<void>
}) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selected = state.niches.find((n) => n.id === selectedId) ?? null

  const create = async () => {
    const name = await promptText({ title: "Nouvelle niche", placeholder: "ex. Naruto Édits" })
    if (!name) return
    try {
      const { id } = await api.createNiche(name)
      await refresh()
      setSelectedId(id)
      toast.success("niche « " + name + " » créée")
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Niches"
        subtitle="Chaque niche est un univers de contenu : sa banque de clips, ses presets de montage, sa cadence."
      />

      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}
      >
        {state.niches.map((n) => {
          const active = n.id === selectedId
          return (
            <button
              key={n.id}
              type="button"
              onClick={() => setSelectedId(active ? null : n.id)}
              className={
                "flex flex-col gap-2 rounded-xl border bg-card p-4 text-left transition-colors hover:border-primary/50 " +
                (active ? "border-primary ring-2 ring-primary" : "border-border")
              }
            >
              <div className="font-semibold">{n.name}</div>
              <div className="text-xs text-muted-foreground">{n.owner || "—"}</div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>{n.clips.length} clips</span>
                <span>{n.preset_ids.length} preset(s)</span>
                <span>{n.cadence}/j</span>
              </div>
            </button>
          )
        })}

        <button
          type="button"
          onClick={create}
          className="flex min-h-[96px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-card/50 p-4 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
        >
          <Plus className="size-5" />
          Nouvelle niche
        </button>
      </div>

      {selected && (
        <NicheDetail
          key={selected.id}
          niche={selected}
          state={state}
          refresh={refresh}
          onDeleted={() => setSelectedId(null)}
        />
      )}
    </div>
  )
}
