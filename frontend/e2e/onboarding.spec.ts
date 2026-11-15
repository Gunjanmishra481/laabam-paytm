// First run: a new shop goes Welcome -> Setup -> Counter (DESIGN.md §3). The seeded demo shop skips it and
// is labelled as sample data. Settings (sandbox) resets to either.
import { expect, test } from '@playwright/test'

test('a new shop: Welcome, then Setup, then an empty Counter', async ({ page }) => {
  await page.goto('/?fresh=1')
  await expect(page.getByTestId('welcome')).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/welcome')
  expect(new URL(page.url()).search).toBe('') // the flag is taken off, so a reload keeps this shop

  // the screen reads in the language being picked
  await page.getByRole('radio', { name: /தமிழ்/ }).check()
  await expect(page.getByRole('heading', { level: 1 })).toContainText('உங்கள் கடை')
  await page.getByRole('radio', { name: /English/ }).check()
  await page.getByRole('button', { name: 'Continue' }).click()

  await expect(page.getByTestId('setup')).toBeVisible()
  await expect(page.getByText('Step 2 of 2')).toBeVisible()
  // a new shop can't skip ahead to the Counter
  await page.goto('/')
  await expect(page.getByTestId('setup')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start billing' })).toBeDisabled()
  await page.getByLabel('Shop name').fill('Anbu Stores')
  await page.getByLabel('UPI ID for payments').fill('not a upi id')
  await page.getByRole('button', { name: 'Start billing' }).click()
  await expect(page.getByRole('alert')).toContainText("doesn't look like a UPI ID")
  await page.getByLabel('UPI ID for payments').fill('anbu@okaxis')
  await page.getByLabel('When do you close?').selectOption('21:30')
  await page.getByRole('button', { name: 'Start billing' }).click()

  await expect(page.getByRole('heading', { name: 'Anbu Stores' })).toBeVisible()
  await expect(page.getByTestId('sample-data')).toHaveCount(0) // a real shop: no sample label
  await expect(page.getByText('No bills yet today.')).toBeVisible()
  await expect(page.getByTestId('today-sales')).toHaveText('₹0')

  await page.reload() // still the same new shop, set up
  await expect(page.getByRole('heading', { name: 'Anbu Stores' })).toBeVisible()
})

test('the seeded demo shop is labelled as sample data; Settings resets to either shop', async ({ page }) => {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Demo Kirana' })).toBeVisible()
  await expect(page.getByTestId('sample-data')).toHaveText('Sample data')

  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByTestId('settings')).toBeVisible()
  await expect(page.getByText('demo.kirana@sandbox')).toBeVisible()

  // privacy: keeping recordings isn't available, and the app says so rather than pretending
  await page.getByRole('switch', { name: 'Keep voice recordings' }).click()
  await expect(page.getByRole('alert')).toContainText("isn't available yet")
  await expect(page.getByRole('switch', { name: 'Keep voice recordings' })).toHaveAttribute('aria-checked', 'false')

  await page.getByTestId('reset-fresh').click()
  await expect(page.getByTestId('welcome')).toBeVisible()

  await page.goto('/settings') // onboarding comes first for a new shop
  await expect(page.getByTestId('welcome')).toBeVisible()
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.goto('/settings')
  await page.getByTestId('reset-sample').click()
  await expect(page.getByRole('heading', { name: 'Demo Kirana' })).toBeVisible()
  await expect(page.getByTestId('sample-data')).toBeVisible()
})

test('demo page: "Start as a new shop" runs onboarding in the phone', async ({ page }) => {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.goto('/demo')
  const phone = page.getByLabel("Owner's phone")
  await expect(phone.getByRole('heading', { name: 'Demo Kirana' })).toBeVisible()
  await page.getByTestId('demo-fresh').click()
  await expect(phone.getByTestId('welcome')).toBeVisible()
  await expect(page).toHaveURL(/\/demo$/)
})

test('Settings: what Laabam keeps, a copy of the data, and deleting the shop (cancellable)', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Demo Kirana' })).toBeVisible()
  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByTestId('privacy-notice')).toContainText('No customer names or phone numbers')

  const download = page.waitForEvent('download')
  await page.getByTestId('export-data').click()
  expect((await download).suggestedFilename()).toBe('laabam-data.zip')

  await page.getByTestId('delete-shop').click()
  const confirm = page.getByRole('button', { name: 'Delete shop' })
  await expect(confirm).toBeDisabled() // until the name is typed
  await page.getByLabel('Shop name').fill('Demo Kirana')
  await confirm.click()
  await expect(page.getByTestId('deletion-due')).toContainText('Your shop will be deleted on')
  await page.getByTestId('keep-shop').click()
  await expect(page.getByTestId('deletion-due')).toHaveCount(0)
  await expect(page.getByTestId('delete-shop')).toBeVisible()
})
