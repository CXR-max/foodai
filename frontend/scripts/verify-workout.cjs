/**
 * 运动模块验货脚本：
 * 1) 运动首页
 * 2) 运动报告（带对比）
 * 3) 演示模式：倒计时 → 实时轨迹 → 长按结束 → 保存 → 新报告
 * 运行前：后端 8000、前端 5173 已启动，backend/_tok.txt 有有效 token
 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:5173'
const OUT = path.resolve(__dirname, '../../docs/screenshots')
const TOK_FILE = path.resolve(__dirname, '../../backend/_tok.txt')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const token = fs.readFileSync(TOK_FILE, 'utf-8').trim()
  fs.mkdirSync(OUT, { recursive: true })
  const errors = []

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    protocolTimeout: 180000,
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  })

  const page = await browser.newPage()
  await page.setUserAgent(
    'Mozilla/5.0 (Linux; Android 13; Xiaomi 12S Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
  )
  page.on('pageerror', (e) => errors.push('[pageerror] ' + String(e).slice(0, 220)))
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().includes('favicon')) errors.push('[console] ' + m.text().slice(0, 220))
  })

  const ctx = browser.defaultBrowserContext()
  await ctx.overridePermissions(BASE, ['geolocation'])
  await page.setGeolocation({ latitude: 31.230416, longitude: 121.473701, accuracy: 6 })

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.evaluate((t) => localStorage.setItem('token', t), token)

  async function shot(name, wait = 0) {
    if (wait) await sleep(wait)
    await page.screenshot({ path: path.join(OUT, name) })
    const info = await page.evaluate(() => ({
      url: location.pathname,
      text: document.body.innerText.replace(/\s+/g, ' ').slice(0, 150),
      tiles: document.querySelectorAll('img.leaflet-tile-loaded').length,
      paths: document.querySelectorAll('.leaflet-overlay-pane path').length,
      canvas: document.querySelectorAll('.leaflet-overlay-pane canvas').length,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }))
    console.log(`${name} | url=${info.url} | tiles=${info.tiles} paths=${info.paths} canvas=${info.canvas} overflow=${info.overflow}`)
    console.log(`         ${info.text}`)
  }

  // 1. 运动首页
  await page.goto(BASE + '/workout', { waitUntil: 'domcontentloaded', timeout: 30000 })
  await shot('w-01-home.png', 3200)

  // 2. 报告（已有轨迹 + 上次对比）
  await page.goto(BASE + '/workout/3', { waitUntil: 'domcontentloaded', timeout: 30000 })
  await shot('w-02-report.png', 4500)

  // 3. 演示模式：倒计时 / 实时轨迹
  await page.goto(BASE + '/workout/run?demo=1&sport=' + encodeURIComponent('跑步'), {
    waitUntil: 'domcontentloaded', timeout: 30000,
  })
  await shot('w-03-countdown.png', 1000)
  await shot('w-04-live.png', 7000)
  await shot('w-05-live2.png', 12000)

  // 4. 长按结束 → 保存 → 新报告
  const btn = await page.$('.act-stop')
  if (btn) {
    const box = await btn.boundingBox()
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    await sleep(1000)
    await page.mouse.up()
    await sleep(1200)
    // 太短会弹确认框：点“保存”
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('.el-message-box button')]
        .find((x) => x.textContent && x.textContent.includes('保存'))
      if (b) b.click()
    })
    await sleep(4500)
  }
  await shot('w-06-saved.png', 800)

  await browser.close()
  console.log('\n=== JS errors ===')
  console.log(errors.length ? [...new Set(errors)].join('\n') : 'none')
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
