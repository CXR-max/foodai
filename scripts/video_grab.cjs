/* 抓演示视频所需的产品截图素材：手机视口 @3x，关键页用 fullPage 长图 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const OUT = path.resolve(__dirname, '../.tmp_video/shots')
fs.mkdirSync(OUT, { recursive: true })

const BASE = 'http://127.0.0.1:5173'
const token = process.argv[2]

const shots = [
  { name: 'sport', url: '/m/sport', wait: 3200 },
  { name: 'map', url: '/territory', wait: 4200 },
  { name: 'legend', url: '/territory', wait: 3600, click: '.legend' },
  { name: 'detail', url: '/territory/owner/2', wait: 3200, full: true },
  { name: 'settle', url: '/territory/settle/22', wait: 3600, full: true },
]

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: 'new',
    args: ['--no-sandbox', '--font-render-hinting=none'],
  })
  for (const s of shots) {
    const page = await browser.newPage()
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
    await page.evaluateOnNewDocument((t) => localStorage.setItem('token', t), token)
    const errs = []
    page.on('pageerror', (e) => errs.push(e.message))
    await page.goto(BASE + s.url, { waitUntil: 'networkidle2', timeout: 60000 })
    await new Promise((r) => setTimeout(r, s.wait))
    if (s.click) {
      await page.evaluate((sel) => document.querySelector(sel)?.click(), s.click)
      await new Promise((r) => setTimeout(r, 900))
    }
    const file = path.join(OUT, s.name + '.png')
    await page.screenshot({ path: file, fullPage: !!s.full })
    const dim = await page.evaluate(() => ({ w: innerWidth, h: innerHeight, sh: document.documentElement.scrollHeight }))
    console.log(s.name, JSON.stringify(dim), errs.length ? 'ERR:' + errs.join('|') : '')
    await page.close()
  }
  await browser.close()
  console.log('done ->', OUT)
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
