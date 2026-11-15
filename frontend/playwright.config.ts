import { defineConfig, devices } from '@playwright/test'

// Runs against a running Laabam API that serves the built app with LAABAM_SANDBOX=1, e.g.
//   cd frontend && npm run build
//   cd ../backend && LAABAM_SANDBOX=1 LAABAM_DATABASE_URL=sqlite:///e2e.db python -m uvicorn laabam.app:app --port 8000
//   cd ../frontend && npx playwright test
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // every spec shares the one sandbox demo shop (and resets it)
  reporter: process.env.CI ? 'list' : 'line',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'phone', use: { ...devices['Pixel 7'] } }],
})
