// Client fetch minimal vers les endpoints JSON de Flask (proxifiés par Vite).
// Le cookie de session est géré par le navigateur ; on ne gère ici que JSON,
// erreurs et le cas 401 (non connecté).

export type Asset = { name: string; size_mb: number }
export type Job = { name: string; status: "running" | "done" | "failed"; log: string[] }

export type AppState = {
  member: string
  links: string
  clip_links: string
  tracks: Asset[]
  clips: Asset[]
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

export const api = {
  login: (name: string, password: string) =>
    req<{ member: string }>("/api/login", json({ name, password })),
  logout: () => req("/api/logout", { method: "POST" }),
  state: () => req<AppState>("/api/state"),

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
}

async function upload(path: string, file: File) {
  const form = new FormData()
  form.append("file", file)
  return req<{ name: string }>(path, { method: "POST", body: form })
}
