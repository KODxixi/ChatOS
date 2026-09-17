import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@gary-ui': path.resolve('C:/AI/UI/gary-ui'),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8912',
        changeOrigin: true,
      },
      '/agent-api': {
        target: 'http://127.0.0.1:8913',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/agent-api/, ''),
      },
    },
  },
})
