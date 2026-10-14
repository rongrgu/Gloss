/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: `npm run dev` here + `uv run gloss` (port 8765). Build output is served by the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8765' } },
  },
  build: {
    outDir: '../backend/gloss/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500, // local app; one bundle is fine
  },
  test: {
    environment: 'node',
  },
})
