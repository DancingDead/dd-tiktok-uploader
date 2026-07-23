import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"
import { Download, Plus, Trash2, Upload } from "lucide-react"

import type { Asset } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { confirm } from "@/components/confirm"
import { IconButton } from "@/components/IconButton"
import { JobLog } from "@/components/JobLog"

export type SectionConfig = {
  accept: string
  linkNote: string
  downloadLabel: string
  emptyLabel: string
  onUpload: (f: File) => Promise<unknown>
  onDelete: (name: string) => Promise<unknown>
  onSaveLinks: (text: string) => Promise<unknown>
  onDownload: () => Promise<{ job_id: string }>
}

type Props = SectionConfig & {
  assets: Asset[]
  linksText: string
  refresh: () => Promise<void>
}

const parseLinks = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s && !s.startsWith("#"))

export function AssetSection({
  assets,
  linksText,
  accept,
  linkNote,
  downloadLabel,
  emptyLabel,
  onUpload,
  onDelete,
  onSaveLinks,
  onDownload,
  refresh,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [newLink, setNewLink] = useState("")
  const [jobId, setJobId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const links = parseLinks(linksText)

  const guard = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn()
      await refresh()
      if (ok) toast.success(ok)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  // Un seul contrôle : le bouton ouvre l'explorateur (l'input reste caché) et la
  // sélection déclenche l'envoi. Deux contrôles côte à côte faisaient cliquer le
  // bouton en premier, sans effet visible (le toast d'erreur est à l'opposé de l'écran).
  const upload = async (file: File) => {
    setUploading(true)
    try {
      await guard(() => onUpload(file), "uploadé")
    } finally {
      setUploading(false)
      // Remis à zéro pour que re-choisir LE MÊME fichier redéclenche « change ».
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  const addLink = async () => {
    const url = newLink.trim()
    if (!url) return
    await guard(() => onSaveLinks([...links, url].join("\n") + "\n"), "lien ajouté")
    setNewLink("")
  }

  const removeLink = (url: string) =>
    guard(() => onSaveLinks(links.filter((l) => l !== url).join("\n") + "\n"), "lien retiré")

  const download = async () => {
    try {
      const { job_id } = await onDownload()
      setJobId(job_id)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const onJobDone = useCallback(
    (status: "done" | "failed") => {
      refresh()
      status === "done" ? toast.success("téléchargement terminé") : toast.error("échec — voir le journal")
    },
    [refresh]
  )

  const askDelete = async (name: string) => {
    const ok = await confirm({
      title: "Supprimer du catalogue ?",
      description: `« ${name} » sera effacé du disque et retiré des niches qui l'utilisent.`,
    })
    if (ok) await guard(() => onDelete(name), "supprimé")
  }

  return (
    <div className="space-y-6">
      {/* Upload direct */}
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={fileRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload(file)
          }}
        />
        <Button onClick={() => fileRef.current?.click()} disabled={uploading}>
          <Upload /> {uploading ? "Envoi…" : "Uploader un fichier"}
        </Button>
      </div>

      {/* Liens YouTube */}
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">{linkNote}</p>
        <div className="flex gap-2">
          <Input
            value={newLink}
            onChange={(e) => setNewLink(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addLink()}
            placeholder="Colle un lien YouTube (vidéo ou playlist)…"
          />
          <Button variant="secondary" onClick={addLink}>
            <Plus /> Ajouter
          </Button>
        </div>
        {links.length > 0 && (
          <ul className="space-y-1">
            {links.map((url) => (
              <li
                key={url}
                className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm"
              >
                <span className="flex-1 truncate font-mono text-xs text-muted-foreground">
                  {url}
                </span>
                <IconButton
                  tip="Retirer"
                  className="size-7 text-muted-foreground"
                  onClick={() => removeLink(url)}
                >
                  <Trash2 />
                </IconButton>
              </li>
            ))}
          </ul>
        )}
        <Button onClick={download} disabled={links.length === 0}>
          <Download /> {downloadLabel}
        </Button>
        <JobLog jobId={jobId} onDone={onJobDone} />
      </div>

      {/* Table des assets */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fichier</TableHead>
            <TableHead className="w-24">Taille</TableHead>
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {assets.length === 0 ? (
            <TableRow>
              <TableCell colSpan={3} className="text-center text-muted-foreground">
                {emptyLabel}
              </TableCell>
            </TableRow>
          ) : (
            assets.map((a) => (
              <TableRow key={a.name}>
                <TableCell className="font-medium">{a.name}</TableCell>
                <TableCell className="text-muted-foreground">{a.size_mb} Mo</TableCell>
                <TableCell>
                  <IconButton
                    tip="Supprimer du catalogue"
                    className="size-8 text-muted-foreground"
                    onClick={() => askDelete(a.name)}
                  >
                    <Trash2 />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}
