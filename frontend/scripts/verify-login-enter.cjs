const puppeteer = require('puppeteer-core')
const BASE = process.env.BASE || 'http://172.20.10.7:4173'

async function main() {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
    defaultViewport: { width: 390, height: 844, isMobile: true, hasTouch: true },
  })
  const page = await browser.newPage()
  const logs = []
  page.on('pageerror', (e) => logs.push('pageerror ' + String(e).slice(0, 160)))
  page.on('response', (r) => { if (r.url().includes('/api/')) logs.push(`${r.status()} ${r.url().slice(-40)}`) })
  await page.goto(BASE + '/login', { waitUntil: 'networkidle2', timeout: 60000 })
  await new Promise((r) => setTimeout(r, 1500))
  await page.evaluate(() => localStorage.clear())
  await page.reload({ waitUntil: 'networkidle2' })
  await new Promise((r) => setTimeout(r, 1200))
  await page.type('input[placeholder="用户名"]', 'demo_xiaoming')
  await page.type('input[placeholder="密码"]', 'demo123456')
  await page.focus('input[placeholder="密码"]')
  await page.keyboard.press('Enter')
  await new Promise((r) => setTimeout(r, 5000))
  console.log('url:', page.url())
  console.log('token:', await page.evaluate(() => !!localStorage.getItem('token')))
  console.log('logs:', logs)
  await browser.close()
}
main().catch((e) => { console.error(e); process.exit(1) })
