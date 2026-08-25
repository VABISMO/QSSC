import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base so the production build loads its assets correctly both when
// served from a subpath (e.g. a GitHub Pages project page at
// <user>.github.io/QSSC/) and from a root origin. The API base is separate and
// configured at build time via VITE_API_BASE (see frontend/src/api.js).
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8011'
    }
  }
})