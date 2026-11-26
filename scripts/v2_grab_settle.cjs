/* 闭环态 → 派发 pointerdown 长按 → 保存 → 结算页截图 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const BASE = 'http://127.0.0.1:5173'
const TOKEN = process.argv[2]
const OUT = path.resolve(__dirname, '../.tmp_video/v2shots')
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--font-render-hinting=none'] })
  const page = await browser.newPage()
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
  await page.evaluateOnNewDocument((t) => {
    localStorage.setItem('token', t)
    localStorage.removeItem('foodai.run.draft.v1')
  }, TOKEN)
  page.on('pageerror', (e) => console.log('  ERR', e.message))
  await page.goto(BASE + '/workout/run?demo=1&demo_seek=320&demo_speed=0.01', { waitUntil: 'networkidle2', timeout: 90000 })
  await sleep(3800)

  await page.evaluate(() => {
    document.querySelector('.act-stop').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }))
  })
  await sleep(430)
  await page.screenshot({ path: path.join(OUT, 'run_press.png') })
  console.log('  run_press')
  await sleep(900)
  try {
    await page.waitForFunction(() => !location.pathname.startsWith('/workout/run'), { timeout: 25000 })
  } catch { console.log('  !! 未跳转', page.url()) }
  await sleep(3500)
  console.log('  url', page.url())
  await page.screenshot({ path: path.join(OUT, 'settle.png'), fullPage: true })
  const d = await page.evaluate(() => document.documentElement.scrollHeight)
  console.log('  settle h', d)
  await browser.close()
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
