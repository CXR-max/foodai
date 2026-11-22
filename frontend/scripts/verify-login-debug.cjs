const puppeteer = require('puppeteer-core')
const BASE = process.env.BASE || 'https://separated-measure-conflicts-herbal.trycloudflare.com'

async function main() {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
    defaultViewport: { width: 390, height: 844, isMobile: true, hasTouch: true },
  })
  const page = await browser.newPage()
  const logs = []
  page.on('console', (m) => logs.push(`console.${m.type()} ${m.text().slice(0, 160)}`))
  page.on('pageerror', (e) => logs.push('pageerror ' + String(e).slice(0, 200)))
  page.on('requestfailed', (r) => logs.push('FAIL ' + r.url().slice(0, 160) + ' ' + (r.failure()?.errorText || '')))
  page.on('response', (r) => {
    const u = r.url()
    if (u.includes('/api/') || (u.includes('/assets/') && r.status() >= 400)) {
      logs.push(`RESP ${r.status()} ${u.slice(0, 160)}`)
    }
  })

  await page.goto(BASE + '/login', { waitUntil: 'networkidle2', timeout: 90000 })
  await new Promise((r) => setTimeout(r, 2000))
  await page.evaluate(() => localStorage.clear())
  await page.reload({ waitUntil: 'networkidle2', timeout: 90000 })
  await new Promise((r) => setTimeout(r, 2000))
  await page.type('input[placeholder="用户名"]', 'demo_xiaoming')
  await page.type('input[placeholder="密码"]', 'demo123456')
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('登 录'))
    if (b) b.click()
  })
  for (let i = 0; i < 6; i++) {
    await new Promise((r) => setTimeout(r, 2500))
    console.log(i, 'url=', page.url(), 'token=', await page.evaluate(() => !!localStorage.getItem('token')))
  }
  console.log('body:', await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').slice(0, 200)))
  console.log('logs:\n' + logs.slice(-30).join('\n'))
  await browser.close()
}
main().catch((e) => { console.error('FAILED', e); process.exit(1) })
