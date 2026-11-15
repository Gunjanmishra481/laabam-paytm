import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteSingleFile } from 'vite-plugin-singlefile'

// One self-contained HTML file with the API simulated in the page, for sharing a phone preview.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: 'dist-preview',
    emptyOutDir: true,
    rollupOptions: { input: 'preview.html' },
  },
})
