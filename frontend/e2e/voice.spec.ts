// Push-to-talk. Chromium's fake microphone gives a real recording; the server has no speech service in
// e2e, so the upload is tagged with a sample clip name on its way and the offline provider answers.
import { expect, test } from '@playwright/test'

test.use({
  permissions: ['microphone'],
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
})

test.beforeEach(async ({ page }) => {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
})

test('demo without a speech service plays a sample order only when asked, and labels it', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('voice-off')).toContainText('no speech service is set up')
  await page.getByTestId('sample-voice').click()
  await expect(page.getByTestId('checklist')).toBeVisible()
  await expect(page.getByTestId('checklist').locator('li')).toHaveCount(3)
  await expect(page.getByTestId('bill-total')).toHaveText('₹465')
  await expect(page.getByTestId('sample-notice')).toHaveText('Sample order from the demo. This is not what you said.')
  await page.reload()
  await expect(page.getByTestId('sample-notice')).toBeVisible() // still labelled after a refresh
})

// Regression (2026-11-13): with no speech service, holding "Hold to bill" opened no mic, uploaded nothing,
// and the server answered with the next sample order, shown as if the owner had said it.
test('with no speech service the counter says so and never passes a sample off as the owner’s order', async ({ page }) => {
  const uploads: string[] = []
  page.on('request', (r) => r.url().endsWith('/v1/bills/voice') && uploads.push(r.headers()['x-laabam-sample-order'] ?? 'none'))
  let micOpened = false
  await page.exposeFunction('__micOpened', () => (micOpened = true))
  await page.addInitScript(() => {
    const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    navigator.mediaDevices.getUserMedia = (c) => {
      ;(window as unknown as { __micOpened: () => void }).__micOpened()
      return gum(c)
    }
  })
  await page.goto('/')
  await expect(page.getByTestId('voice-off')).toBeVisible()
  await expect(page.getByTestId('push-to-talk')).toHaveCount(0) // no button that looks like it listens
  await expect(page.getByRole('button', { name: 'Hold to bill' })).toHaveCount(0)

  // holding the big button types instead; nothing is recorded or uploaded
  const big = page.getByRole('button', { name: 'Type the order' })
  const box = (await big.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(800)
  await page.mouse.up()
  await expect(page.getByRole('dialog', { name: 'Type the order' })).toBeVisible()
  expect(uploads).toEqual([])
  expect(micOpened).toBe(false)

  // and the server refuses a real recording rather than swapping in a sample
  const token = (await (await page.request.post('/sandbox/session', { data: { history: false } })).json()).access_token
  const r = await page.request.post('/v1/bills/voice', {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'audio/webm' },
    data: Buffer.concat([Buffer.from([0x1a, 0x45, 0xdf, 0xa3]), Buffer.alloc(4000, 1)]), // a real WebM header
  })
  expect(r.status()).toBe(503)
})

test('a voice reply that came from sample speech is labelled, never shown as heard', async ({ page }) => {
  await page.route('**/api/health', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), voice: 'live' } })
  })
  // a misconfigured server answering a recording with a sample: the app must say so
  await page.route('**/v1/bills/voice', (route) =>
    route.continue({ headers: { ...route.request().headers(), 'x-laabam-clip': 'en_order' } }),
  )
  await page.goto('/')
  const ptt = page.getByTestId('push-to-talk')
  const box = (await ptt.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(1000)
  await page.mouse.up()
  await expect(page.getByTestId('checklist')).toBeVisible()
  await expect(page.getByTestId('sample-notice')).toBeVisible()
})

test('hold to talk records, uploads the audio once, and shows the checklist', async ({ page }) => {
  // pretend a speech service is set up, so the real push-to-talk button shows
  await page.route('**/api/health', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), voice: 'live' } })
  })
  const uploads: { type: string; bytes: number }[] = []
  await page.route('**/v1/bills/voice', async (route) => {
    const req = route.request()
    uploads.push({ type: req.headers()['content-type'] ?? '', bytes: req.postDataBuffer()?.length ?? 0 })
    await route.continue({ headers: { ...req.headers(), 'x-laabam-clip': 'en_order' } })
  })
  await page.goto('/')
  const ptt = page.getByTestId('push-to-talk')
  await expect(ptt).toContainText('Hold to bill')

  const box = (await ptt.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  // the Listening screen takes over while the button is held; letting go anywhere stops
  await expect(page.getByRole('button', { name: 'Let go to check' })).toBeVisible()
  await page.waitForTimeout(1200)
  await page.mouse.up()

  await expect(page.getByTestId('checklist')).toBeVisible()
  await expect(page.getByTestId('bill-total')).toHaveText('₹465')
  expect(uploads).toHaveLength(1)
  expect(uploads[0].type).toMatch(/^audio\//)
  expect(uploads[0].bytes).toBeGreaterThan(1000)
})

test('a quick press asks the owner to hold; the keyboard toggles recording', async ({ page }) => {
  await page.route('**/api/health', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), voice: 'live' } })
  })
  await page.route('**/v1/bills/voice', (route) =>
    route.continue({ headers: { ...route.request().headers(), 'x-laabam-clip': 'en_kg' } }),
  )
  await page.goto('/')
  const ptt = page.getByTestId('push-to-talk')
  await ptt.click()
  await expect(page.getByRole('status')).toContainText(/Hold the button while you speak|mic is ready/)

  // Space/Enter: once to start, again to stop
  await page.getByTestId('push-to-talk').focus()
  await page.keyboard.press('Enter')
  const stop = page.getByRole('button', { name: 'Tap to check' })
  await expect(stop).toBeFocused()
  await expect(stop).toHaveAttribute('aria-pressed', 'true')
  await page.waitForTimeout(800)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('checklist')).toBeVisible()
  await expect(page.getByTestId('checklist').locator('li')).toHaveCount(2)
})
