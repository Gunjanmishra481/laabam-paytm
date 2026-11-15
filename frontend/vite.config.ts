import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs on :8000; Vite forwards /api calls to it.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: { '/api': 'http://localhost:8000', '/v1': 'http://localhost:8000', '/sandbox': 'http://localhost:8000' },
  },
})
