import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Optional polling for external/network volumes that miss native events.
  server: {
    watch: process.env.PPA_EDA_WATCH_POLLING === '1'
      ? { usePolling: true, interval: 500 }
      : undefined,
  },
})
