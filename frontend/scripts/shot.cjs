/**
 * 用本机 Edge 在手机视口下渲染页面并截图，产物放到 docs/screenshots/
 * 运行：node shot.cjs
 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:5173'
const OUT = path.resolve(__dirname, '../../docs/screenshots')
const TOK_FILE = path.resolve(__dirname, '../../backend/_tok.txt')

async function main() {
  const token = fs.readFileSync(TOK_FILE, 'utf-8').trim()
  fs.mkdirSync(OUT, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2'],
    defaultViewport: {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    },
  })

  const page = await browser.newPage()
  await page.setUserAgent(
    'Mozilla/5.0 (Linux; Android 13; Xiaomi 12S Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
  )

  // 定位权限：给一个固定坐标，避免页面卡在"等待定位"
  const ctx = browser.defaultBrowserContext()
  await ctx.overridePermissions(BASE, ['geolocation'])
  await page.setGeolocation({ latitude: 31.230416, longitude: 121.473701, accuracy: 8 })

  page.on('console', (m) => {
    const t = m.text()
    if (m.type() === 'error') console.log('  [console error]', t.slice(0, 200))
  })
  page.on('pageerror', (e) => console.log('  [page error]', String(e).slice(0, 200)))

  // 首次打开先写入登录态
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.evaluate((t) => localStorage.setItem('token', t), token)

  async function shot(route, file, waitMs = 2600) {
    try {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 30000 })
    } catch (e) {
      console.log(`  [nav warn] ${route}: ${String(e).slice(0, 80)}`)
    }
    await new Promise((r) => setTimeout(r, waitMs))
    const title = await page.title()
    const text = await page.evaluate(() => document.body.innerText.slice(0, 120))
    await page.screenshot({ path: path.join(OUT, file) })
    console.log(`saved ${file}  | title="${title}" | text="${text.replace(/\n/g, ' ')}"`)
  }

  // 注意：跑步页有「退出拦截」，必须放最后截，否则会卡住后续导航
  await shot('/workout', '01-workout-home.png')
  await shot('/workout/1', '02-workout-report.png', 4200)
  await shot('/checkin', '04-checkin.png', 3000)
  await shot('/dashboard', '05-dashboard.png', 3000)
  await shot('/workout/run', '03-run-live.png', 3500)

  await browser.close()
  console.log('done')
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
