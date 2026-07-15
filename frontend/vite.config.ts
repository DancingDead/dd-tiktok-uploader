import { fileURLToPath, URL } from "node:url"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// Dev server : proxifie /api vers le Flask local (webui.py) pour que le PoC
// consomme les mêmes endpoints JSON. Le cookie de session transite par le proxy.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // Cible surchargée par VITE_API_TARGET pour tester contre un Flask isolé.
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8765",
        changeOrigin: false,
      },
    },
  },
})
