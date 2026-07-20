import { useState, type ReactNode } from "react"
import { toast } from "sonner"
import { Play, Plus, Search, Trash2 } from "lucide-react"

import { api, type Asset } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { IconButton } from "@/components/IconButton"

type Props = {
  title: string
  description: string
  prefix: "tracks/" | "clips/"
  selected: string[]
  catalogue: Asset[]
  emptySelected: string
  emptyCatalogue: string
  onChange: (next: string[]) => Promise<void>
  addedToast: string
  removedToast: string
}

const basename = (path: string) => path.replace(/^[^/]+\//, "")

// Une ligne d'asset : nom + aperçu à la demande (on ne charge le média qu'au
// clic, sinon lister 100 clips déclencherait 100 requêtes) + une action.
function AssetRow({
  label,
  assetRef,
  kind,
  actionIcon,
  actionTip,
  actionClass,
  onAction,
  muted,
}: {
  label: string
  assetRef: string
  kind: "audio" | "video"
  actionIcon: ReactNode
  actionTip: string
  actionClass: string
  onAction: () => void
  muted?: boolean
}) {
  const [open, setOpen] = useState(false)
  const url = api.assetUrl(assetRef)
  return (
    <li className="flex flex-col gap-2 rounded-md border bg-card px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className={"flex-1 truncate" + (muted ? " text-muted-foreground" : "")}>{label}</span>
        <IconButton
          tip={kind === "audio" ? "Écouter" : "Aperçu"}
          className="size-7 text-muted-foreground"
          onClick={() => setOpen((o) => !o)}
        >
          <Play />
        </IconButton>
        <IconButton tip={actionTip} className={actionClass} onClick={onAction}>
          {actionIcon}
        </IconButton>
      </div>
      {open &&
        (kind === "audio" ? (
          <audio src={url} controls autoPlay preload="none" className="h-9 w-full" />
        ) : (
          <video
            src={url}
            controls
            autoPlay
            preload="metadata"
            className="max-h-48 w-full rounded bg-black"
          />
        ))}
    </li>
  )
}

// Carte générique de sélection (sons ou clips) : liste les éléments choisis dans
// la niche + le reste du catalogue partagé à ajouter. Ajout/retrait immédiats,
// recherche pour tenir à l'échelle, aperçu pour choisir à l'oreille/à l'œil.
export function SelectionCard({
  title,
  description,
  prefix,
  selected,
  catalogue,
  emptySelected,
  emptyCatalogue,
  onChange,
  addedToast,
  removedToast,
}: Props) {
  const [query, setQuery] = useState("")
  const kind: "audio" | "video" = prefix === "tracks/" ? "audio" : "video"
  const q = query.trim().toLowerCase()
  const match = (name: string) => name.toLowerCase().includes(q)

  const shownSelected = selected.filter((p) => match(basename(p)))
  const available = catalogue.filter((a) => !selected.includes(prefix + a.name))
  const shownAvailable = available.filter((a) => match(a.name))

  const add = async (name: string) => {
    try {
      await onChange([...selected, prefix + name])
      toast.success(addedToast)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const remove = async (path: string) => {
    try {
      await onChange(selected.filter((p) => p !== path))
      toast.success(removedToast)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {(selected.length > 0 || available.length > 0) && (
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Rechercher par nom…"
              className="pl-9"
            />
          </div>
        )}

        {selected.length === 0 ? (
          <p className="text-sm text-muted-foreground">{emptySelected}</p>
        ) : shownSelected.length === 0 ? (
          <p className="text-sm text-muted-foreground">aucun élément sélectionné ne correspond</p>
        ) : (
          <ul className="space-y-1">
            {shownSelected.map((path) => (
              <AssetRow
                key={path}
                label={basename(path)}
                assetRef={path}
                kind={kind}
                actionIcon={<Trash2 />}
                actionTip="Retirer de la niche"
                actionClass="size-7 text-muted-foreground"
                onAction={() => remove(path)}
              />
            ))}
          </ul>
        )}

        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Ajouter depuis le catalogue
          </p>
          {catalogue.length === 0 ? (
            <p className="text-sm text-muted-foreground">{emptyCatalogue}</p>
          ) : available.length === 0 ? (
            <p className="text-sm text-muted-foreground">tout le catalogue est déjà dans la niche</p>
          ) : shownAvailable.length === 0 ? (
            <p className="text-sm text-muted-foreground">aucun résultat pour « {query.trim()} »</p>
          ) : (
            <ul className="space-y-1">
              {shownAvailable.map((a) => (
                <AssetRow
                  key={a.name}
                  label={a.name}
                  assetRef={prefix + a.name}
                  kind={kind}
                  actionIcon={<Plus />}
                  actionTip="Ajouter à la niche"
                  actionClass="size-7 text-primary"
                  onAction={() => add(a.name)}
                  muted
                />
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
