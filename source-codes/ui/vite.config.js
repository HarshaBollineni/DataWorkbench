import path from "path"
import fs from "node:fs"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import tailwindcss from "@tailwindcss/vite"

const appVersion = fs.readFileSync(path.resolve(__dirname, "../VERSION"), "utf8").trim()

export default defineConfig({
  server: {
    port: 5175,
    strictPort: true,
  },
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    react(),
    tailwindcss(), // This injects the native Tailwind v4 compiler
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
})
