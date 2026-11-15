// Bills in: a distributor bill photo -> lines checked -> "Is this Oil?" -> confirm -> costs saved.
import { expect, test, type Page } from '@playwright/test'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

async function shopThatSoldRiceSugarOil(page: Page) {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.goto('/')
  await page.getByRole('button', { name: /^(Type instead|Type the order)$/ }).click()
  await page.getByRole('textbox', { name: 'Type the order' }).fill('rice 200, sugar 100, oil 165')
  await page.getByRole('button', { name: 'Make bill' }).click()
  await page.getByTestId('confirm').click()
  await expect(page.getByRole('img', { name: /Paytm QR for/ })).toBeVisible()
  await page.goto('/') // back to the counter; the QR stays on the Soundbox
}

/** A live reader, faked around the real server: the upload link and the PUT are stubbed (the sandbox has no
 *  photo reader), and "uploaded" reads the named sample bill. Returns the content types the phone uploaded. */
async function fakeLiveUpload(page: Page, sample: string, shape?: (bill: Record<string, unknown>) => Record<string, unknown>) {
  const uploads: string[] = []
  await page.route('**/v1/purchases/uploads', async (route) => {
    const body = JSON.parse(route.request().postData() ?? '{}')
    await route.fulfill({ json: { purchase: { id: 'pur_upload' }, upload: { url: '/fake-upload', method: 'PUT', headers: { 'Content-Type': body.content_type } } } })
  })
  await page.route('**/fake-upload', async (route) => {
    uploads.push(route.request().headers()['content-type'] ?? '')
    await route.fulfill({ status: 200, body: '' })
  })
  await page.route('**/v1/purchases/pur_upload/uploaded', async (route) => {
    const res = await route.fetch({
      url: route.request().url().replace('/pur_upload/uploaded', '/photo'),
      headers: { ...route.request().headers(), 'content-type': 'image/jpeg', 'x-laabam-sample': sample },
    })
    const bill = await res.json()
    await route.fulfill({ status: 202, json: shape ? shape(bill) : bill })
  })
  return uploads
}

function line(page: Page, text: string) {
  return page.getByTestId('purchase-line').filter({ hasText: text })
}

test('sample bill: matched, asked once, misread caught, costs saved', async ({ page }) => {
  await shopThatSoldRiceSugarOil(page)
  await page.getByRole('link', { name: 'Bills in' }).click()
  // no photo reader in e2e: the screen says so, and the samples are a separate choice
  await expect(page.getByTestId('vision-off')).toContainText('no reading service is set up')
  await page.getByTestId('sample-bill-printed').click()

  await expect(page.getByTestId('distributor')).toHaveText('Sri Lakshmi Agencies')
  await expect(page.getByTestId('purchase-lines').getByTestId('purchase-line')).toHaveCount(4)
  // lines that need the owner come first, with the question and two actions
  await expect(line(page, 'SUNFLWR OIL')).toContainText('Is this the “oil” you bill?')
  await expect(page.getByRole('button', { name: 'Sugar 25 kg × ₹42' })).toBeVisible() // matched by itself: checks out
  const confirm = page.getByTestId('confirm-costs')
  await expect(confirm).toBeDisabled()
  await expect(confirm).toHaveText('Check 3 lines to confirm')

  await line(page, 'SUNFLWR OIL').getByRole('button', { name: 'Yes, oil' }).click()
  await line(page, 'PONNI RICE').getByRole('button', { name: /^Yes, / }).click()
  await expect(line(page, 'TOOR DAL').getByRole('textbox')).toHaveValue('Toor dal') // a clean name, ready to add
  await line(page, 'TOOR DAL').getByRole('button', { name: 'Add' }).click()
  await expect(confirm).toHaveText('Confirm, save costs')
  await expect(page.getByRole('button', { name: /\+ 2 more lines, all check out/ })).toBeVisible() // folded, as drawn
  await page.getByRole('button', { name: /\+ 2 more lines/ }).click()
  await expect(line(page, 'Toor dal')).toBeVisible()
  await confirm.click()
  await expect(page.getByTestId('costs-saved')).toBeVisible()

  // the list shows it done
  await page.getByRole('button', { name: 'Back' }).click()
  await expect(page.getByTestId('purchase-row').first()).toContainText('4 lines · done')

  // a bill where the reader got one amount wrong: the line check stops it, one tap fixes it
  await page.getByTestId('sample-bill-misread').click()
  await expect(page.getByText(/Lines add up to ₹\S+, but the bill says/)).toBeVisible()
  await expect(line(page, 'CHANNA DAL')).toContainText('4 × ₹96 is ₹384. Which is right?')
  await line(page, 'CHANNA DAL').getByRole('button', { name: 'It’s ₹384' }).click()
  await expect(line(page, 'CHANNA DAL')).not.toContainText('Which is right?')
  await expect(page.getByText(/Lines add up to/)).toHaveCount(0)

  // name it, then edit it by hand from the lines that check out; a wrong amount is caught again
  await line(page, 'CHANNA DAL').getByRole('button', { name: 'Add' }).click()
  await page.getByRole('button', { name: /^Channa dal 4 pack × ₹96/ }).click()
  await page.getByRole('dialog').getByLabel('Amount (₹)').fill('390')
  await page.getByRole('dialog').getByRole('button', { name: 'Save line' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('4 × ₹96 is ₹384. Which is right?')).toBeVisible()
})

test('a real photo is shrunk and uploaded once', async ({ page }) => {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.route('**/api/health', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), vision: 'live' } })
  })
  const uploads = await fakeLiveUpload(page, 'handwritten')
  await page.goto('/')
  await page.getByRole('link', { name: 'Bills in' }).click()
  await expect(page.getByRole('button', { name: /Take a photo of a bill/ })).toBeVisible()
  await page.getByTestId('bill-photo-input').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: PNG })
  await expect(page.getByTestId('distributor')).toHaveText('Ganesh Provisions')
  await expect(line(page, 'Milk')).toBeVisible()
  expect(uploads).toHaveLength(1)
  expect(uploads[0]).toMatch(/^image\//)
})

test('discarding a read bill, from its details', async ({ page }) => {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.goto('/bills-in')
  await page.getByTestId('sample-bill-handwritten').click()
  await page.getByRole('button', { name: /Bill details/ }).click()
  await page.getByTestId('discard-bill').click()
  await expect(page.getByTestId('vision-off')).toBeVisible()
  await expect(page.getByTestId('purchase-row')).toHaveCount(0)
})

// A live reader (Sarvam Document AI) reads on the worker. Here the server reads a sample bill at once, and the
// routes make it look slow: the photo comes back "reading", and the first polls still say so.
async function liveReader(page: Page, first: 'reading' | 'failed') {
  await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })
  await page.route('**/api/health', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), vision: 'live' } })
  })
  await fakeLiveUpload(page, 'handwritten', (bill) => ({
    ...bill,
    status: first,
    lines: [],
    can_retry: first === 'failed',
    read_issues: first === 'failed' ? ['read_failed'] : [],
  }))
  let polls = 0
  const purchase = (u: URL) => u.pathname.startsWith('/v1/purchases/pur_')
  await page.route((u) => purchase(u) && u.pathname.split('/').length === 4, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const res = await route.fetch()
    const bill = await res.json()
    polls += 1
    await route.fulfill({ response: res, json: polls <= 2 ? { ...bill, status: 'reading', lines: [] } : bill })
  })
  await page.route((u) => purchase(u) && u.pathname.endsWith('/retry'), async (route) => {
    const res = await route.fetch({ url: route.request().url().slice(0, -'/retry'.length), method: 'GET' })
    await route.fulfill({ response: res, json: { ...(await res.json()), status: 'reading', lines: [] } })
  })
  await page.goto('/bills-in')
}

test('a live read shows "Reading the bill…" until the worker is done', async ({ page }) => {
  await liveReader(page, 'reading')
  await page.getByTestId('bill-photo-input').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: PNG })
  await expect(page.getByRole('status', { name: 'Reading the bill…' })).toBeVisible()
  await expect(page.getByTestId('distributor')).toHaveText('Ganesh Provisions', { timeout: 15_000 })
  await expect(line(page, 'Milk')).toBeVisible()
})

test('a read that failed offers Try again', async ({ page }) => {
  await liveReader(page, 'failed')
  await page.getByTestId('bill-photo-input').setInputFiles({ name: 'bill.png', mimeType: 'image/png', buffer: PNG })
  await expect(page.getByTestId('read-failed')).toContainText('Couldn’t read this bill')
  await page.getByTestId('read-retry').click()
  await expect(page.getByRole('status', { name: 'Reading the bill…' })).toBeVisible()
  await expect(page.getByTestId('distributor')).toHaveText('Ganesh Provisions', { timeout: 15_000 })
})
