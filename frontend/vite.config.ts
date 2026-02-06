import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/opcheck': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/refresh_opensanctions': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
