import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { Crosshair } from "lucide-react"

import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

// En dessous, il n'y a plus de plan à monter — et le serveur refuserait de
// toute façon. Le bouton reste désactivé tant qu'on est sous ce plancher.
const MIN_DURATION = 1

const tenth = (v: number) => Math.round(v * 10) / 10

type Props = {
  name: string | null
  onClose: () => void
  onStarted: (jobId: string) => void
}

// Rognage du début et de la fin d'un clip du catalogue. DESTRUCTIF : le fichier
// est réécrit, d'où la confirmation explicite plutôt qu'un simple bouton.
export function TrimDialog({ name, onClose, onStarted }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  // Bornes gardées en texte : un champ vidé (l'utilisateur efface avant de
  // retaper) doit rester vide, pas se réécrire tout seul en 0.
  const [start, setStart] = useState("0")
  const [end, setEnd] = useState("")
  const [sending, setSending] = useState(false)

  // Le composant reste monté entre deux ouvertures : sans ce reset, les bornes
  // du clip précédent s'appliqueraient au suivant.
  useEffect(() => {
    setStart("0")
    setEnd("")
    setSending(false)
  }, [name])

  const startVal = Number.parseFloat(start)
  const endVal = Number.parseFloat(end)
  const valid = Number.isFinite(startVal) && Number.isFinite(endVal) && startVal >= 0
  const duration = valid ? endVal - startVal : NaN
  const tooShort = !valid || duration < MIN_DURATION

  // « prendre ici » : évite d'avoir à noter des timecodes ailleurs pour les
  // recopier — on place la lecture puis on capture la position.
  const takeHere = (set: (v: string) => void) => {
    const video = videoRef.current
    if (!video) return
    set(String(tenth(video.currentTime)))
  }

  const submit = async () => {
    if (!name || tooShort || sending) return
    setSending(true)
    try {
      const { job_id } = await api.trimClip(name, startVal, endVal)
      onStarted(job_id)
      onClose()
    } catch (e) {
      // Le serveur valide les bornes contre la durée réelle et rend un message
      // écrit pour être lu : on l'affiche tel quel.
      toast.error((e as Error).message)
      setSending(false)
    }
  }

  return (
    <Dialog open={name !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Rogner le clip</DialogTitle>
          <DialogDescription>
            {name} — place la lecture puis capture les bornes à conserver.
          </DialogDescription>
        </DialogHeader>

        {name && (
          <video
            // Remonté à chaque clip : sans clé, changer la seule `src` peut ne
            // pas relancer le chargement, et `onLoadedMetadata` ne rejouerait
            // pas — le champ de fin resterait sur la durée du clip précédent.
            key={name}
            ref={videoRef}
            controls
            preload="metadata"
            src={api.assetUrl("clips/" + name)}
            className="max-h-[45vh] w-full rounded bg-black"
            onLoadedMetadata={(e) => {
              // Sans ça, il faudrait saisir la fin même pour ne couper que le
              // début — le cas le plus fréquent.
              const d = e.currentTarget.duration
              if (Number.isFinite(d)) setEnd(String(tenth(d)))
            }}
          />
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="trim-start">Début (s)</Label>
            <div className="flex gap-2">
              <Input
                id="trim-start"
                type="number"
                step="0.1"
                min="0"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
              <Button variant="secondary" onClick={() => takeHere(setStart)}>
                <Crosshair /> Prendre ici
              </Button>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="trim-end">Fin (s)</Label>
            <div className="flex gap-2">
              <Input
                id="trim-end"
                type="number"
                step="0.1"
                min="0"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
              <Button variant="secondary" onClick={() => takeHere(setEnd)}>
                <Crosshair /> Prendre ici
              </Button>
            </div>
          </div>
        </div>

        <p className={tooShort ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
          Durée conservée : {valid ? `${duration.toFixed(1)} s` : "—"}
          {tooShort && ` (au moins ${MIN_DURATION} s, la fin après le début)`}
        </p>

        <p className="text-sm text-muted-foreground">
          Le fichier du catalogue sera réécrit. Cette opération est irréversible ; le clip devra
          être réimporté depuis son lien YouTube pour revenir en arrière. Le rognage prend environ
          deux minutes : laisse-le tourner, le journal s'affiche dans le catalogue.
        </p>

        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            Annuler
          </Button>
          <Button variant="destructive" disabled={tooShort || sending} onClick={submit}>
            {sending ? "Lancement…" : "Rogner définitivement"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
