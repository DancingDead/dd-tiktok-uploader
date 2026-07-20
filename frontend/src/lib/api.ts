// Client fetch minimal vers les endpoints JSON de Flask (proxifiés par Vite).
// Le cookie de session est géré par le navigateur ; on ne gère ici que JSON,
// erreurs et le cas 401 (non connecté).

export type Asset = { name: string; size_mb: number }
export type Job = { name: string; status: "running" | "done" | "failed"; log: string[] }

export type Subtitles = { enabled?: boolean; preprompt?: string; lines?: string[] }

export type Video = {
  id: number
  status: "proposed" | "approved" | "rejected" | "posted" | "failed"
  seed: number
  track: string
  caption: string
  subtitles: Subtitles
  created_at: string
  exists: boolean
}

export type Niche = {
  id: number
  name: string
  slug: string
  owner: string
  cadence: number
  caption_template: string
  hashtags: string[]
  preset_ids: number[]
  tracks: string[]
  clips: string[]
  subtitles: Subtitles
  videos: Video[]
}

export type Overrides = {
  effects?: { zoom?: boolean; flash?: boolean; shake?: boolean; speed?: boolean }
  accents?: { rgb?: boolean; glitch?: boolean | number }
  delogo?: boolean
  chrono?: boolean
  min_presence?: number
  cut_mode?: string
  section?: string
  cut_every?: number
  buildup?: number
  strobe_beats?: number
  color_grade?: string
  grain?: number
  clip_speed?: number
  subtitles?: { font?: string }
}

export type Preset = { id: number; name: string; overrides: Overrides }

export type Settings = Required<
  Pick<
    Overrides,
    | "effects"
    | "accents"
    | "delogo"
    | "chrono"
    | "min_presence"
    | "cut_mode"
    | "cut_every"
    | "buildup"
    | "strobe_beats"
  >
>

export type AppState = {
  member: string
  niches: Niche[]
  presets: Preset[]
  links: string
  clip_links: string
  tracks: Asset[]
  clips: Asset[]
  settings: Settings
  jobs: Record<string, Job>
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function req<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(path, opts)
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new ApiError((data as { error?: string }).error || r.statusText, r.status)
  return data as T
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

const patch = (body: unknown): RequestInit => ({ ...json(body), method: "PATCH" })

async function upload(path: string, file: File) {
  const form = new FormData()
  form.append("file", file)
  return req<{ name: string }>(path, { method: "POST", body: form })
}

export const api = {
  // Auth & état
  login: (name: string, password: string) =>
    req<{ member: string }>("/api/login", json({ name, password })),
  logout: () => req("/api/logout", { method: "POST" }),
  state: () => req<AppState>("/api/state"),

  // Catalogue — sons & clips
  uploadTrack: (file: File) => upload("/api/tracks", file),
  uploadClip: (file: File) => upload("/api/clips", file),
  deleteTrack: (name: string) =>
    req(`/api/tracks/${encodeURIComponent(name)}`, { method: "DELETE" }),
  deleteClip: (name: string) =>
    req(`/api/clips/${encodeURIComponent(name)}`, { method: "DELETE" }),
  saveLinks: (text: string) => req("/api/links", json({ text })),
  saveClipLinks: (text: string) => req("/api/clip-links", json({ text })),
  downloadTracks: () => req<{ job_id: string }>("/api/download", json({})),
  downloadClips: () => req<{ job_id: string }>("/api/clips/download", json({})),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),

  // Niches
  createNiche: (name: string) => req<{ id: number }>("/api/niches", json({ name })),
  updateNiche: (id: number, fields: Partial<Niche>) => req(`/api/niches/${id}`, patch(fields)),
  deleteNiche: (id: number) => req(`/api/niches/${id}`, { method: "DELETE" }),
  generateNiche: (id: number, count: number) =>
    req<{ job_id: string }>(`/api/niches/${id}/generate`, json({ count })),

  // Vidéos
  videoUrl: (id: number, download = false) => `/api/videos/${id}${download ? "?dl=1" : ""}`,
  setVideoStatus: (id: number, status: Video["status"]) =>
    req(`/api/videos/${id}/status`, json({ status })),
  deleteVideo: (id: number) => req(`/api/videos/${id}`, { method: "DELETE" }),

  // Presets
  createPreset: (name: string, overrides: Overrides) =>
    req<{ id: number }>("/api/presets", json({ name, overrides })),
  updatePreset: (id: number, name: string, overrides: Overrides) =>
    req(`/api/presets/${id}`, patch({ name, overrides })),
  deletePreset: (id: number) => req(`/api/presets/${id}`, { method: "DELETE" }),

  // Réglages par défaut
  saveSettings: (settings: Settings) => req("/api/settings", json(settings)),
}
