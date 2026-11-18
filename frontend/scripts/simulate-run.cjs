/**
 * 实时轨迹验证：模拟手机沿路线移动，观察跑步页轨迹是否实时延伸。
 * 输出 docs/screenshots/live-1/2/3.png
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

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    protocolTimeout: 180000,
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  })

  const page = await browser.newPage()

  const ctx = browser.defaultBrowserContext()
  await ctx.overridePermissions(BASE, ['geolocation'])

  // 起点：上海人民广场附近
  let lat = 31.230416
  let lng = 121.473701
  await page.setGeolocation({ latitude: lat, longitude: lng, accuracy: 5 })

  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 40000 })
  await page.evaluate((t) => localStorage.setItem('token', t), token)

  await page.goto(BASE + '/workout/run?sport=' + encodeURIComponent('跑步'), {
    waitUntil: 'domcontentloaded',
    timeout: 40000,
  })
  await sleep(3000)

  const st = await page.evaluate(() => document.body.innerText.slice(0, 80))
  console.log('页面状态:', st.replace(/\n/g, ' '))

  // 3.5 m/s（12.6km/h，配速约 4'45"），每步 1 秒
  const STEP_M = 3.5
  const D_LAT = STEP_M / 111320
  const D_LNG = STEP_M / (111320 * Math.cos((lat * Math.PI) / 180)) * 0.45

  async function shot(n) {
    await page.screenshot({ path: path.join(OUT, `live-${n}.png`) })
    const info = await page.evaluate(() => document.body.innerText.replace(/\n/g, ' ').slice(0, 90))
    console.log(`  live-${n}.png | ${info}`)
  }

  for (let i = 1; i <= 60; i++) {
    lat += D_LAT
    lng += D_LNG
    await page.setGeolocation({ latitude: lat, longitude: lng, accuracy: 5 })
    await sleep(1000)
    if (i === 15) await shot(1)
    if (i === 35) await shot(2)
    if (i === 60) await shot(3)
  }

  await browser.close()
  console.log('done')
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
