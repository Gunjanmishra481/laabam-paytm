// Profit, Item and its sources, Alerts on the seeded Demo Kirana month, after Sri Balaji's new bill (the oil story).
import { expect, test, type Page } from '@playwright/test'

export async function demoWithOilBill(page: Page) {
  const session = await (await page.request.post('/sandbox/reset', { data: { reset: true } })).json()
  const auth = { Authorization: `Bearer ${session.access_token}` }
  const bill = await (
    await page.request.post('/v1/purchases/photo', {
      headers: { ...auth, 'Content-Type': 'image/jpeg', 'X-Laabam-Sample': 'balaji' },
      data: Buffer.alloc(0),
    })
  ).json()
  expect((await page.request.post(`/v1/purchases/${bill.id}/confirm`, { headers: auth })).ok()).toBeTruthy()
  // the worker scans for alerts after a confirmed bill
  await expect
    .poll(async () => (await (await page.request.get('/v1/alerts', { headers: auth })).json()).map((a: { kind: string }) => a.kind))
    .toContain('price_rise')
  return auth
}

test('profit: about, coverage, bars for week and month only, items, no-cost link', async ({ page }) => {
  await demoWithOilBill(page)
  await page.goto('/profit')
  const card = page.getByTestId('profit-card')
  await expect(card).toContainText('Profit, about')
  await expect(card).toContainText(/Counts \d+% of this week’s UPI payments\. Figures are illustrative\./)
  await expect(page.getByRole('img', { name: /Profit by day this week/ })).toBeVisible()
  const oil = page.getByTestId('profit-item').filter({ hasText: 'Sunflower oil' })
  await expect(oil).toContainText('5.5%')
  await expect(page.getByTestId('no-cost-link')).toHaveText(/7 items with no cost yetAdd a bill/)

  await page.getByRole('radio', { name: 'Today' }).click()
  await expect(page.getByRole('img', { name: /Profit by/ })).toHaveCount(0) // no bars for one day
  await page.getByRole('radio', { name: 'Month' }).click()
  await expect(page.getByRole('img', { name: /Profit by week this month/ })).toBeVisible()

  await page.getByRole('radio', { name: 'This week' }).click()
  await page.getByTestId('no-cost-link').click()
  await expect(page).toHaveURL(/\/bills-in$/)
})

test('item and its sources: every number opens the bill line or the voice bills behind it', async ({ page }) => {
  await demoWithOilBill(page)
  await page.goto('/profit')
  await page.getByTestId('profit-item').filter({ hasText: 'Sunflower oil' }).click()
  await expect(page.getByTestId('profit-per-unit')).toHaveText('₹9')
  await expect(page.getByText('profit a litre, down from ₹17')).toBeVisible()
  await expect(page.getByTestId('sold-note')).toContainText(/You sold about \d+ litres this week\. At the old cost you’d have made about ₹\d+ more\./)
  const bills = page.getByTestId('source-bill')
  await expect(bills.nth(0)).toContainText('Bill 4471, line 4: 72 × ₹156')
  await expect(bills.nth(1)).toContainText('Bill 4302, line 6: 120 × ₹148')
  await expect(page.getByTestId('cheaper')).toContainText('Kumar Traders billed you ₹149 a litre')

  await page.getByTestId('source-voice').click()
  const sheet = page.getByRole('dialog')
  await expect(sheet.getByText('“oil 165”').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)

  await bills.nth(0).click()
  await expect(page.getByTestId('distributor')).toHaveText('Sri Balaji Agencies')
})

test('alerts: one action and Got it per alert, the Counter banner, and the empty state', async ({ page }) => {
  const auth = await demoWithOilBill(page)
  await page.goto('/')
  await expect(page.getByTestId('counter-alert')).toHaveText('Sunflower oil costs ₹8 more a litre')
  await page.getByTestId('counter-alert').click()
  await expect(page.getByRole('heading', { name: 'Things to look at' })).toBeVisible()
  const cards = page.getByTestId('alert')
  await expect(cards.first()).toContainText('Sri Balaji, ')
  await expect(cards.first()).toContainText('₹156, up from ₹148. Profit a litre fell from ₹17 to ₹9.')
  await expect(page.locator('[data-kind="running_out"]')).toContainText('You sell about 14 packs a day')
  await expect(page.locator('[data-kind="low_margin"]')).toContainText('Milk earns 3.7%')
  const n = await cards.count()
  await page.locator('[data-kind="low_margin"]').getByRole('button', { name: 'Got it' }).click()
  await expect(cards).toHaveCount(n - 1)
  await page.reload()
  await expect(cards).toHaveCount(n - 1) // seen stays seen

  await page.locator('[data-kind="price_rise"]').getByRole('button', { name: 'See details' }).click()
  await expect(page.getByTestId('profit-per-unit')).toHaveText('₹9')

  for (const a of await (await page.request.get('/v1/alerts', { headers: auth })).json())
    await page.request.post(`/v1/alerts/${a.id}/seen`, { headers: auth })
  await page.goto('/alerts')
  await expect(page.getByTestId('alerts-empty')).toHaveText('All clear. Laabam checks again after every bill.')
})

test('ask: answer with sources and Listen, a drafted message shared (never sent), a typed question', async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as unknown as { __shared: string[] }).__shared = []
    Object.defineProperty(navigator, 'share', {
      value: async (d: { text: string }) => (window as unknown as { __shared: string[] }).__shared.push(d.text),
    })
  })
  await demoWithOilBill(page)
  await page.goto('/profit')
  await page.getByRole('link', { name: 'Ask why' }).click()
  await page.getByRole('button', { name: 'Why is profit down this week?' }).click()
  const answer = page.getByTestId('answer')
  await expect(answer).toContainText(/Mostly sunflower oil\. It now costs ₹156 a litre, up from ₹148, so you made about ₹\d+ less on it this week\./)
  await expect(answer.locator('b').first()).toHaveText('₹156')
  await expect(answer.getByTestId('source-chip').first()).toHaveText(/^Sri Balaji · \d+ \w+ · line 4$/)
  await expect(answer.getByTestId('source-chip').nth(1)).toHaveText(/^\d+ voice bills$/)
  await expect(answer.getByRole('button', { name: 'Listen' })).toBeVisible()

  await page.getByRole('button', { name: 'Write to Sri Balaji about it' }).click()
  const draft = page.getByTestId('draft-text')
  await expect(draft).toHaveText(/^Namaste\. Sunflower oil 1 L is ₹156 on bill 4471, up from ₹148 on .+\. Another distributor billed ₹149\. Can you do ₹149\?$/)
  await page.getByRole('button', { name: 'Share' }).click()
  expect(await page.evaluate(() => (window as unknown as { __shared: string[] }).__shared)).toEqual([await draft.textContent()])

  await page.getByRole('textbox', { name: 'Type a question' }).fill('What should I reorder?')
  await page.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(page.getByTestId('answer').last()).toContainText('Reorder Biscuits soon. Biscuits will run out by')

  // the source chip opens the distributor bill line it cites
  await page.getByTestId('source-chip').first().click()
  await expect(page.getByTestId('distributor')).toHaveText('Sri Balaji Agencies')
})

test('item detail: Draft a message opens the draft straight away', async ({ page }) => {
  await demoWithOilBill(page)
  await page.goto('/profit')
  await page.getByTestId('profit-item').filter({ hasText: 'Sunflower oil' }).click()
  await page.getByRole('link', { name: 'Draft a message' }).click()
  await expect(page.getByText('Write to Sri Balaji', { exact: true })).toBeVisible()
  await expect(page.getByTestId('draft-text')).toContainText('Can you do ₹149?')
})

test('evening summary: played on the Soundbox, play/pause, transcript, four figures', async ({ page }) => {
  const auth = await demoWithOilBill(page)
  const me = await (await page.request.get('/v1/me', { headers: auth })).json()
  const played = await (await page.request.post(`/sandbox/summary/${me.id}`, { headers: auth })).json()
  await page.goto('/summary')
  await expect(page.getByText('Played on your Soundbox')).toBeVisible()
  await expect(page.getByTestId('summary-text')).toHaveText(`“${played.text}”`)
  await expect(page.getByTestId('summary-text')).toContainText('Sunflower oil now costs ₹8 more a litre.')
  const tiles = page.getByTestId('summary-figures')
  await expect(tiles).toContainText(/Profit(, about)?/) // 'about' when today has estimated sales (none just after midnight)
  await expect(tiles).toContainText('Sunflower oil cost up')
  await page.getByRole('button', { name: 'Play summary' }).click()
  await expect(page.getByRole('button', { name: 'Pause summary' })).toBeVisible()
  await page.getByRole('button', { name: 'Pause summary' }).click()
  await expect(page.getByRole('button', { name: 'Play summary' })).toBeVisible()
})

test('festival plan: 2x by default (a guess), cash in hand, shortfall, loan offer the owner approves', async ({ page }) => {
  await demoWithOilBill(page)
  await page.goto('/plan')
  await expect(page.getByRole('heading', { name: 'Deepavali stock' })).toBeVisible()
  await expect(page.getByText('in 9 days')).toBeVisible() // the festival date is a setting; the demo's is 9 days out
  await expect(page.getByText('This is a guess you can change.')).toBeVisible()
  await expect(page.getByRole('radio', { name: '2× usual' })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByTestId('plan-total')).toHaveText('₹40,000')
  await expect(page.getByTestId('plan-line').first()).toContainText('Kumar Traders, ₹149 a litre')
  await expect(page.getByTestId('cash-card')).toContainText('₹15,000')
  await expect(page.getByTestId('shortfall')).toHaveText('₹25,000')

  await page.getByRole('radio', { name: '2.5× usual' }).click()
  await expect(page.getByTestId('plan-total')).not.toHaveText('₹40,000')
  await page.getByRole('radio', { name: '2× usual' }).click()
  await expect(page.getByTestId('plan-total')).toHaveText('₹40,000')

  await page.getByTestId('see-loan').click()
  await expect(page.getByTestId('loan-amount')).toHaveText('₹25,000')
  await expect(page.getByText('[Partner’s rate]')).toBeVisible() // terms come from the partner
  const basis = page.getByTestId('loan-basis')
  await expect(basis).toContainText('Deepavali order (2× usual)₹40,000')
  await expect(basis).toContainText('Your cash in hand- ₹15,000')
  await expect(basis).toContainText('Shortfall₹25,000')
  await page.getByTestId('approve-loan').click()
  await expect(page.getByTestId('loan-status')).toContainText('Sent to the lending partner')
  await expect(page.getByTestId('approve-loan')).toHaveCount(0)
})

test('festival plan: editing cash in hand changes the shortfall; enough cash, no loan', async ({ page }) => {
  await demoWithOilBill(page)
  await page.goto('/plan')
  await page.getByTestId('cash-card').getByRole('button', { name: 'Edit' }).click()
  await page.getByRole('dialog').getByLabel('Amount (₹)').fill('30000')
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  await expect(page.getByTestId('shortfall')).toHaveText('₹10,000')
  await page.getByTestId('cash-card').getByRole('button', { name: 'Edit' }).click()
  await page.getByRole('dialog').getByLabel('Amount (₹)').fill('45000')
  await page.getByRole('dialog').getByRole('button', { name: 'Save' }).click()
  await expect(page.getByTestId('cash-card')).toContainText('Your cash in hand covers this order.')
  await expect(page.getByTestId('see-loan')).toHaveCount(0)
})

test('reorder list from the running-out alert', async ({ page }) => {
  await demoWithOilBill(page)
  await page.goto('/alerts')
  await page.locator('[data-kind="running_out"]').getByRole('button', { name: 'Add to reorder' }).click()
  await expect(page.getByRole('heading', { name: 'Reorder this week' })).toBeVisible()
  await expect(page.getByTestId('plan-line').first()).toContainText('Biscuits')
})
