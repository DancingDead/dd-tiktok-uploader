import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { LogOut } from "lucide-react"

import { ApiError, api, type AppState } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Toaster } from "@/components/ui/sonner"
import { Catalogue } from "@/features/catalogue/Catalogue"

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

  if (!ready) return null

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Toaster position="bottom-right" />
      {needLogin || !state ? (
        <Login onDone={refresh} />
      ) : (
        <Shell state={state} refresh={refresh} onLogout={refresh} />
      )}
    </div>
  )
}

function Shell({
  state,
  refresh,
  onLogout,
}: {
  state: AppState
  refresh: () => Promise<void>
  onLogout: () => Promise<void>
}) {
  const logout = async () => {
    await api.logout()
    await onLogout()
  }
  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="inline-block size-2 rounded-full bg-primary" />
            <span className="text-xs font-semibold tracking-[0.22em] uppercase">
              Dancing Dead
            </span>
          </div>
          <h1 className="mt-4 text-2xl font-semibold">Catalogue</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Les ressources partagées du label — les niches y piochent leurs sons et
            leurs clips.{" "}
            <span className="text-xs opacity-60">· PoC React/shadcn</span>
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span>{state.member}</span>
          <Button variant="ghost" size="icon" title="Déconnexion" onClick={logout}>
            <LogOut />
          </Button>
        </div>
      </header>
      <Catalogue state={state} refresh={refresh} />
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
