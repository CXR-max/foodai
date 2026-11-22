const puppeteer = require('puppeteer-core')
const path = require('path')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = process.env.BASE || 'http://172.20.10.7:4173'
const OUT = path.resolve(__dirname, '../../docs/screenshots')

async function main() {
  const browser = await puppeteer.launch({
    executablePath: EDGE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  })
  const page = await browser.newPage()
  const logs = []
  page.on('pageerror', (e) => logs.push('pageerror ' + String(e).slice(0, 160)))
  page.on('requestfailed', (r) => logs.push('reqfail ' + r.url().slice(0, 120) + ' ' + (r.failure()?.errorText || '')))
  page.on('response', (r) => {
    if (r.url().includes('/api/')) logs.push(`api ${r.status()} ${r.url().slice(0, 120)}`)
  })

  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded', timeout: 60000 })
  await new Promise((r) => setTimeout(r, 2500))
  await page.evaluate(() => localStorage.removeItem('token'))
  await page.reload({ waitUntil: 'domcontentloaded' })
  await new Promise((r) => setTimeout(r, 2000))

  await page.type('input[placeholder="用户名"]', 'demo_xiaoming')
  await page.type('input[placeholder="密码"]', 'demo123456')
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('登 录'))
    if (b) b.click()
  })
  await new Promise((r) => setTimeout(r, 5000))
  await page.screenshot({ path: path.join(OUT, 'login-test.png') })
  console.log('url:', page.url())
  console.log('token:', await page.evaluate(() => !!localStorage.getItem('token')))
  console.log('body:', await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').slice(0, 160)))
  console.log('logs:', logs.slice(-10))
  await browser.close()
}
main().catch((e) => { console.error('FAILED', e); process.exit(1) })
