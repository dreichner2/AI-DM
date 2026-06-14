import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendProxyTarget =
  process.env.VITE_AIDM_PROXY_TARGET ??
  `http://127.0.0.1:${process.env.AIDM_BACKEND_PORT ?? '5050'}`

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
      },
      '/socket.io': {
        target: backendProxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    // Vite reports decimal kB; this matches the 620 KiB raw chunk budget.
    chunkSizeWarningLimit: 635,
  },
})
