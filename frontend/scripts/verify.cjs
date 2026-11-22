/**
 * 移动端截图 + 地图校验（生产预览版）
 * 运行：node verify.cjs
 * 前置：后端 8000、预览 4173 在跑；backend/_tok.txt 是演示账号 token
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
    if (r.url().includes('autonavi.com')) {
      if (r.status() === 200) tileOk++
      else tileFail++
    }
  })
  page.on('console', (m) => {
    if (m.type() === 'error') console.log('  [console error]', m.text().slice(0, 160))
  })
  page.on('pageerror', (e) => console.log('  [page error]', String(e).slice(0, 200)))

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.evaluate((t) => localStorage.setItem('token', t), token)

  async function shot(route, file, waitMs = 3500) {
    tileOk = 0
    tileFail = 0
    try {
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 30000 })
    } catch {
      console.log(`  [nav warn] ${route}`)
    }
    await new Promise((r) => setTimeout(r, waitMs))
    const info = await page.evaluate(() => ({
      text: document.body.innerText.slice(0, 90).replace(/\n/g, ' '),
      tiles: document.querySelectorAll('img.leaflet-tile').length,
      loaded: document.querySelectorAll('img.leaflet-tile-loaded').length,
      poly: document.querySelectorAll('.leaflet-overlay-pane path').length,
    }))
    await page.screenshot({ path: path.join(OUT, file) })
    console.log(
      `${file} | 瓦片DOM=${info.tiles} 已加载=${info.loaded} 网络200=${tileOk} 失败=${tileFail} 轨迹path=${info.poly}`
    )
    console.log(`         text: ${info.text}`)
  }

  await shot('/m/sport', 'v3-01-sport.png')
  await shot('/m/schedule', 'v3-02-schedule.png')
  await shot('/m/plan', 'v3-03-plan.png')
  await shot('/workout/5', 'v3-04-report.png', 5000)
  await shot('/m/me', 'v3-05-me.png')

  await browser.close()
  console.log('done')
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
