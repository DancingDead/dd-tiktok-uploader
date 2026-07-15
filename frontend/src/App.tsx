import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { Blocks, Film, LogOut, type LucideIcon, Settings2, SlidersHorizontal } from "lucide-react"

import { ApiError, api, type AppState } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Toaster } from "@/components/ui/sonner"
import { ConfirmHost } from "@/components/confirm"
import { Catalogue } from "@/features/catalogue/Catalogue"
import { NichesTab } from "@/features/niches/NichesTab"
import { PresetsTab } from "@/features/presets/PresetsTab"
import { SettingsTab } from "@/features/settings/SettingsTab"

type TabKey = "niches" | "presets" | "catalogue" | "reglages"

const TABS: { key: TabKey; label: string; icon: LucideIcon }[] = [
  { key: "niches", label: "Niches", icon: Blocks },
  { key: "presets", label: "Presets", icon: SlidersHorizontal },
  { key: "catalogue", label: "Catalogue", icon: Film },
  { key: "reglages", label: "Réglages", icon: Settings2 },
]

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [needLogin, setNeedLogin] = useState(false)
  const [ready, setReady] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setState(await api.state())
      setNeedLogin(false)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedLogin(true)
      else toast.error((e as Error).message)
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Toaster position="bottom-right" />
      <ConfirmHost />
      {!ready ? null : needLogin || !state ? (
        <Login onDone={refresh} />
      ) : (
        <Shell state={state} refresh={refresh} />
      )}
    </div>
  )
}

function Shell({ state, refresh }: { state: AppState; refresh: () => Promise<void> }) {
  const [tab, setTab] = useState<TabKey>("niches")

  const logout = async () => {
    await api.logout()
    await refresh()
  }

  return (
    <div className="flex min-h-screen">
      <nav className="flex w-56 shrink-0 flex-col gap-1 border-r bg-card/40 p-4">
        <div className="mb-4 flex items-center gap-2 px-2">
          <span className="inline-block size-2 rounded-full bg-primary" />
          <span className="text-xs font-semibold tracking-[0.22em] uppercase">
            Dancing Dead
          </span>
        </div>
        <div className="mb-3 flex items-center justify-between px-2 text-xs text-muted-foreground">
          <span className="truncate">{state.member}</span>
          <button
            className="text-muted-foreground transition-colors hover:text-foreground"
            title="Déconnexion"
            onClick={logout}
          >
            <LogOut className="size-4" />
          </button>
        </div>
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors",
              tab === key
                ? "bg-primary/10 font-medium text-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <main className="flex-1 overflow-x-hidden px-8 py-10">
        <div className="mx-auto max-w-4xl">
          {tab === "niches" && <NichesTab state={state} refresh={refresh} />}
          {tab === "presets" && <PresetsTab state={state} refresh={refresh} />}
          {tab === "catalogue" && <Catalogue state={state} refresh={refresh} />}
          {tab === "reglages" && <SettingsTab state={state} refresh={refresh} />}
        </div>
      </main>
    </div>
  )
}

function Login({ onDone }: { onDone: () => Promise<void> }) {
  const [name, setName] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")

  const submit = async () => {
    try {
      await api.login(name, password)
      setError("")
      await onDone()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-center text-sm font-semibold tracking-[0.22em] uppercase">
            Dancing Dead
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            placeholder="Membre"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            type="password"
            placeholder="Mot de passe"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <Button className="w-full" onClick={submit}>
            Se connecter
          </Button>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>
    </div>
  )
}
