const puppeteer = require('puppeteer-core')
const path = require('path')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = process.env.BASE || 'https://comply-stick-rachel-juvenile.trycloudflare.com'
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
  page.on('console', (m) => { if (m.type() === 'error') logs.push('console ' + m.text().slice(0, 160)) })

  await page.goto(BASE + '/login', { waitUntil: 'networkidle2', timeout: 90000 })
  await page.evaluate(async () => {
    const r = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'demo_xiaoming', password: 'demo123456' }),
    })
    const d = await r.json()
    localStorage.setItem('token', d.token)
  })
  await page.goto(BASE + '/m/sport', { waitUntil: 'networkidle2', timeout: 90000 })
  await new Promise((r) => setTimeout(r, 3500))
  await page.screenshot({ path: path.join(OUT, 'm-sport-final.png') })
  console.log('sport url:', page.url())
  console.log(await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').slice(0, 220)))

  // 打开补录抽屉，确认日期字段存在
  await page.evaluate(() => {
    const a = [...document.querySelectorAll('a')].find((x) => x.textContent.includes('跑步机补录'))
    if (a) a.click()
  })
  await new Promise((r) => setTimeout(r, 1200))
  await page.screenshot({ path: path.join(OUT, 'm-manual-final.png') })
  console.log('drawer has time:', await page.evaluate(() => document.body.innerText.includes('时间')))
  console.log('logs:', logs.length ? logs : 'none')
  await browser.close()
}
main().catch((e) => { console.error('FAILED', e); process.exit(1) })
