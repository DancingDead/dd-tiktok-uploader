import { useEffect, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

type ConfirmOptions = { title?: string; description: string; confirmLabel?: string }
type ConfirmState = ConfirmOptions & { resolve: (v: boolean) => void }
type PromptOptions = { title: string; placeholder?: string; okLabel?: string }
type PromptState = PromptOptions & { resolve: (v: string | null) => void }

let openConfirm: ((s: ConfirmState) => void) | null = null
let openPrompt: ((s: PromptState) => void) | null = null

// confirm() / promptText() impératifs renvoyant une Promise, utilisables depuis
// n'importe quel handler (remplacent confirm()/prompt() natifs). <ConfirmHost/>
// doit être monté une fois à la racine.
export function confirm(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => openConfirm?.({ ...opts, resolve }))
}

export function promptText(opts: PromptOptions): Promise<string | null> {
  return new Promise((resolve) => openPrompt?.({ ...opts, resolve }))
}

export function ConfirmHost() {
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null)
  const [promptState, setPromptState] = useState<PromptState | null>(null)
  const [value, setValue] = useState("")

  useEffect(() => {
    openConfirm = setConfirmState
    openPrompt = (s) => {
      setValue("")
      setPromptState(s)
    }
    return () => {
      openConfirm = null
      openPrompt = null
    }
  }, [])

  const doneConfirm = (v: boolean) => {
    confirmState?.resolve(v)
    setConfirmState(null)
  }
  const donePrompt = (v: string | null) => {
    promptState?.resolve(v)
    setPromptState(null)
  }

  return (
    <>
      <Dialog open={confirmState !== null} onOpenChange={(o) => !o && doneConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirmState?.title ?? "Confirmer"}</DialogTitle>
            <DialogDescription>{confirmState?.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => doneConfirm(false)}>
              Annuler
            </Button>
            <Button variant="destructive" onClick={() => doneConfirm(true)}>
              {confirmState?.confirmLabel ?? "Supprimer"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={promptState !== null} onOpenChange={(o) => !o && donePrompt(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{promptState?.title}</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={value}
            placeholder={promptState?.placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && value.trim() && donePrompt(value.trim())}
          />
          <DialogFooter>
            <Button variant="secondary" onClick={() => donePrompt(null)}>
              Annuler
            </Button>
            <Button disabled={!value.trim()} onClick={() => donePrompt(value.trim() || null)}>
              {promptState?.okLabel ?? "Créer"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
