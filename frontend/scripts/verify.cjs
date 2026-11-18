/**
 * 生产预览版截图 + 地图校验
 * - 手机视口 390×844
 * - 统计高德瓦片请求成功数，确认 Leaflet 底图真的加载了
 * 运行：node verify.cjs
 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:4173'
const OUT = path.resolve(__dirname, '../../docs/screenshots')
const TOK_FILE = path.resolve(__dirname, '../../backend/_tok.txt')

async function main() {
  const token = fs.readFileSync(TOK_FILE, 'utf-8').trim()
  fs.mkdirSync(OUT, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  })

  const page = await browser.newPage()
  await page.setUserAgent(
    'Mozilla/5.0 (Linux; Android 13; Xiaomi 12S Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
  )

  let tileOk = 0
  let tileFail = 0
  page.on('response', (r) => {
    const u = r.url()
    if (u.includes('autonavi.com')) {
      if (r.status() === 200) tileOk++
      else tileFail++
    }
  })

  page.on('console', (m) => {
    if (m.type() === 'error') console.log('  [console error]', m.text().slice(0, 160))
  })
  page.on('pageerror', (e) => console.log('  [page error]', String(e).slice(0, 160)))

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.evaluate((t) => localStorage.setItem('token', t), token)

  async function shot(route, file, waitMs = 3200) {
    tileOk = 0
    tileFail = 0
    try {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 30000 })
    } catch (e) {
      console.log(`  [nav warn] ${route}`)
    }
    await new Promise((r) => setTimeout(r, waitMs))
    const info = await page.evaluate(() => ({
      title: document.title,
      text: document.body.innerText.slice(0, 80).replace(/\n/g, ' '),
      tiles: document.querySelectorAll('img.leaflet-tile').length,
      loaded: document.querySelectorAll('img.leaflet-tile-loaded').length,
      poly: document.querySelectorAll('.leaflet-overlay-pane path').length,
      canvas: document.querySelectorAll('.leaflet-overlay-pane canvas').length,
    }))
    await page.screenshot({ path: path.join(OUT, file) })
    console.log(
      `${file} | title=${info.title} | 瓦片DOM=${info.tiles} 已加载=${info.loaded} | 网络200=${tileOk} 失败=${tileFail} | 轨迹path=${info.poly} canvas=${info.canvas}`
    )
    console.log(`         text: ${info.text}`)
  }

  // 跑步页有退出拦截，放最后
  await shot('/workout', 'v2-01-workout-home.png')
  await shot('/workout/1', 'v2-02-workout-report.png', 4500)
  await shot('/checkin', 'v2-03-checkin.png')
  await shot('/dashboard', 'v2-04-dashboard.png')

  // 登录页单独截（未登录态最真实）
  await page.evaluate(() => localStorage.clear())
  tileOk = 0
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded', timeout: 30000 })
  await new Promise((r) => setTimeout(r, 2500))
  await page.screenshot({ path: path.join(OUT, 'v2-05-login.png') })
  console.log('v2-05-login.png | 登录页首屏')

  await browser.close()
  console.log('done')
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
