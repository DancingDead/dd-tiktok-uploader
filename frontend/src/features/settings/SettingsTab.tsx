import { useState } from "react"
import { toast } from "sonner"
import { PageHeader } from "@/components/PageHeader"
import { api, type AppState, type Settings } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export function SettingsTab({
  state,
  refresh,
}: {
  state: AppState
  refresh: () => Promise<void>
}) {
  const [local, setLocal] = useState<Settings>(state.settings)
  const [saving, setSaving] = useState(false)

  function setEffect(key: keyof Settings["effects"], value: boolean) {
    setLocal((s) => ({ ...s, effects: { ...s.effects, [key]: value } }))
  }

  function setAccent(key: keyof Settings["accents"], value: boolean) {
    setLocal((s) => ({ ...s, accents: { ...s.accents, [key]: value } }))
  }

  async function save() {
    setSaving(true)
    try {
      await api.saveSettings(local)
      toast.success("réglages enregistrés")
      await refresh()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Réglages du montage par défaut"
        subtitle="Base appliquée à tous les rendus. Chaque preset s'empile par-dessus ces valeurs."
      />

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Effets</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.zoom}
                onCheckedChange={(v) => setEffect("zoom", v === true)}
              />
              Punch-zoom
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.flash}
                onCheckedChange={(v) => setEffect("flash", v === true)}
              />
              Flash blanc
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.shake}
                onCheckedChange={(v) => setEffect("shake", v === true)}
              />
              Shake
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.effects.speed}
                onCheckedChange={(v) => setEffect("speed", v === true)}
              />
              Slow-mo avant drop
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Accents</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.accents.rgb}
                onCheckedChange={(v) => setAccent("rgb", v === true)}
              />
              RGB split à l'impact
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.accents.glitch as boolean}
                onCheckedChange={(v) => setAccent("glitch", v === true)}
              />
              Micro-glitch
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Cadrage &amp; contenu</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.delogo}
                onCheckedChange={(v) =>
                  setLocal((s) => ({ ...s, delogo: v === true }))
                }
              />
              Gommer le logo (delogo)
            </label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={local.chrono}
                onCheckedChange={(v) =>
                  setLocal((s) => ({ ...s, chrono: v === true }))
                }
              />
              Chronologie de l'histoire
            </label>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="min_presence">Présence personnages min</Label>
              <Input
                id="min_presence"
                type="number"
                step="0.05"
                min={0}
                max={1}
                className="w-20"
                value={local.min_presence}
                onChange={(e) =>
                  setLocal((s) => ({
                    ...s,
                    min_presence: parseFloat(e.target.value),
                  }))
                }
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Rythme</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="cut_mode">Mode</Label>
              <Select
                value={local.cut_mode}
                onValueChange={(v) =>
                  setLocal((s) => ({ ...s, cut_mode: v }))
                }
              >
                <SelectTrigger id="cut_mode" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="energy">énergie (auto)</SelectItem>
                  <SelectItem value="fixed">fixe</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="cut_every">Coupe tous les … beats (mode fixe)</Label>
              <Input
                id="cut_every"
                type="number"
                min={1}
                className="w-20"
                value={local.cut_every}
                onChange={(e) =>
                  setLocal((s) => ({ ...s, cut_every: Number(e.target.value) }))
                }
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="buildup">Buildup … s</Label>
              <Input
                id="buildup"
                type="number"
                className="w-20"
                value={local.buildup}
                onChange={(e) =>
                  setLocal((s) => ({ ...s, buildup: Number(e.target.value) }))
                }
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="strobe_beats">Strobo après drop … beats</Label>
              <Input
                id="strobe_beats"
                type="number"
                className="w-20"
                value={local.strobe_beats}
                onChange={(e) =>
                  setLocal((s) => ({
                    ...s,
                    strobe_beats: Number(e.target.value),
                  }))
                }
              />
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="flex justify-end">
        <Button onClick={save} disabled={saving}>
          Enregistrer
        </Button>
      </div>
    </div>
  )
}
