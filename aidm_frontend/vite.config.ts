/// <reference types="vitest/config" />
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
    // Vite reports decimal kB; this matches the 662.7 KiB raw chunk budget
    // (the 652.5 kB entry chunk plus 4%).
    chunkSizeWarningLimit: 679,
  },
  test: {
    // One worker per core oversubscribes a machine that is also doing other
    // work, and every test's wall time then balloons past its timeout. Half
    // the cores keeps the suite near its own speed on a busy machine.
    maxWorkers: '50%',
  },
})
