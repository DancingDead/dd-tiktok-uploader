import { toast } from "sonner"
import { Plus, Trash2 } from "lucide-react"

import type { Asset } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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

// Carte générique de sélection (sons ou clips) : liste les éléments choisis dans
// la niche + le reste du catalogue partagé à ajouter. Ajout/retrait immédiats.
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
  const available = catalogue.filter((a) => !selected.includes(prefix + a.name))

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
        {selected.length === 0 ? (
          <p className="text-sm text-muted-foreground">{emptySelected}</p>
        ) : (
          <ul className="space-y-1">
            {selected.map((path) => (
              <li
                key={path}
                className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm"
              >
                <span className="flex-1 truncate">{basename(path)}</span>
                <IconButton
                  tip="Retirer de la niche"
                  className="size-7 text-muted-foreground"
                  onClick={() => remove(path)}
                >
                  <Trash2 />
                </IconButton>
              </li>
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
          ) : (
            <ul className="space-y-1">
              {available.map((a) => (
                <li
                  key={a.name}
                  className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm"
                >
                  <span className="flex-1 truncate text-muted-foreground">{a.name}</span>
                  <IconButton
                    tip="Ajouter à la niche"
                    className="size-7 text-primary"
                    onClick={() => add(a.name)}
                  >
                    <Plus />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
