import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

const appConfig = JSON.parse(readFileSync(new URL('./app.config.json', import.meta.url), 'utf-8'))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: appConfig.devServer?.port || 5173,
    proxy: {
      '/api': {
        target: appConfig.devServer?.proxyTarget || 'http://127.0.0.1:8088',
        changeOrigin: true,
      }
    }
  }
})
