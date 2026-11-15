// The counter flow with text input: order -> checklist -> confirm -> QR -> customer pays -> paid.
import { expect, test, type Page } from '@playwright/test'

let auth: Record<string, string> = {}

async function freshShop(page: Page) {
  // a new demo shop, so learned prices from earlier runs don't leak in; its token for the demo routes
  const s = await (await page.request.post('/sandbox/reset', { data: { reset: true, history: false } })).json()
  auth = { Authorization: `Bearer ${s.access_token}` }
}

async function makeBill(page: Page, order: string) {
  await page.getByRole('button', { name: /^(Type instead|Type the order)$/ }).click()
  await page.getByRole('textbox', { name: 'Type the order' }).fill(order)
  await page.getByRole('button', { name: 'Make bill' }).click()
  await expect(page.getByTestId('checklist')).toBeVisible()
}

async function confirmAndPay(page: Page) {
  const confirmed = page.waitForResponse((r) => r.url().endsWith('/confirm') && r.ok())
  await page.getByTestId('confirm').click()
  const bill = await (await confirmed).json()
  await expect(page.getByRole('img', { name: /Paytm QR for/ })).toBeVisible()
  const pay = await page.request.post(`/sandbox/qr/${bill.qr.order_id}/pay`, { data: {}, headers: auth })
  expect((await pay.json()).result).toBe('succeeded')
  await expect(page.getByTestId('paid')).toBeVisible({ timeout: 5000 })
  return bill
}

test('typed order to paid, then learned prices on the next order', async ({ page }) => {
  await freshShop(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Demo Kirana' })).toBeVisible()

  await makeBill(page, 'rice 200, sugar 100, oil 165')
  await expect(page.getByTestId('checklist').locator('li')).toHaveCount(3)
  await expect(page.getByTestId('bill-total')).toHaveText('₹465')
  await expect(page.getByTestId('confirm')).toHaveText('Confirm ₹465, show QR')
  expect(page.url()).toMatch(/\/bill\/[^/]+$/) // the draft is in the URL, so a refresh keeps it

  const bill = await confirmAndPay(page)
  expect(bill.total_paise).toBe(46500)
  await expect(page.getByTestId('paid')).toContainText('₹465')
  await expect(page.getByTestId('paid')).toContainText('received')

  await page.getByRole('button', { name: 'Next bill' }).click()
  await expect(page.getByTestId('today-sales')).toHaveText('₹465')

  await makeBill(page, 'rice, oil')
  await expect(page.getByText('Price from last time')).toHaveCount(2)
  await expect(page.getByTestId('bill-total')).toHaveText('₹365')
})

test('no QR until the owner confirms; Edit bill takes the QR back', async ({ page }) => {
  await freshShop(page)
  await page.goto('/')
  await makeBill(page, 'rice 200, sugar 100')
  await expect(page.getByRole('img', { name: /Paytm QR for/ })).toHaveCount(0)
  await page.reload()
  await expect(page.getByTestId('bill-total')).toHaveText('₹300') // refresh keeps the draft
  await expect(page.getByRole('img', { name: /Paytm QR for/ })).toHaveCount(0)

  // untick a line: the total updates, and the confirmed amount follows
  await page.getByRole('checkbox', { name: 'Customer took Sugar' }).uncheck()
  await expect(page.getByTestId('bill-total')).toHaveText('₹200')
  await page.getByTestId('confirm').click()
  await expect(page.getByRole('img', { name: 'Paytm QR for ₹200' })).toBeVisible()

  await page.getByRole('button', { name: 'Edit bill' }).click()
  await expect(page.getByTestId('checklist')).toBeVisible()
  await expect(page.getByRole('img', { name: /Paytm QR for/ })).toHaveCount(0)
})

test('a line without a price blocks confirm until the owner types it', async ({ page }) => {
  await freshShop(page)
  await page.goto('/')
  await makeBill(page, 'sugar, rice 200')
  // Fix a line opens by itself for the line that needs a price
  const sheet = page.getByRole('dialog')
  await expect(sheet).toContainText('How much for sugar?')
  await expect(page.getByTestId('confirm')).toBeDisabled()
  await expect(page.getByTestId('confirm')).toHaveText('Fix 1 line to continue')

  await sheet.getByLabel('Amount (₹)').fill('45')
  await sheet.getByRole('button', { name: 'Save line' }).click()
  await expect(sheet).toHaveCount(0)
  await expect(page.getByTestId('bill-total')).toHaveText('₹245')
  await expect(page.getByTestId('confirm')).toBeEnabled()
  await confirmAndPay(page)
})

test('demo view: soundbox shows the QR and announces the payment', async ({ page }) => {
  await freshShop(page)
  await page.goto('/demo')
  const phone = page.getByLabel("Owner's phone")
  await phone.getByRole('button', { name: /^(Type instead|Type the order)$/ }).click()
  await page.getByRole('textbox', { name: 'Type the order' }).fill('rice 200, sugar 100, oil 165')
  await page.getByRole('button', { name: 'Make bill' }).click()
  await expect(page.getByTestId('trace')).toContainText('rice 200')
  await phone.getByTestId('confirm').click()
  await expect(page.getByTestId('soundbox-screen')).toContainText('₹465', { timeout: 5000 })
  await page.getByRole('button', { name: 'Customer pays', exact: true }).click()
  await expect(page.getByTestId('soundbox-screen')).toContainText('₹465 received.', { timeout: 5000 })
  await expect(phone.getByTestId('paid')).toBeVisible({ timeout: 5000 })
  await expect(page).toHaveURL(/\/demo$/)
})
