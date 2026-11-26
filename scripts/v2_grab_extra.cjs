/* 补两个镜头：起跑瞬间、结算页下半屏 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const BASE = 'http://127.0.0.1:5173'
const TOKEN = process.argv[2]
const OUT = path.resolve(__dirname, '../.tmp_video/v2shots')
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--font-render-hinting=none'] })
  const open = async (url, wait) => {
    const p = await browser.newPage()
    await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
    await p.evaluateOnNewDocument((t) => {
      localStorage.setItem('token', t)
      localStorage.removeItem('foodai.run.draft.v1')
    }, TOKEN)
    await p.goto(BASE + url, { waitUntil: 'networkidle2', timeout: 90000 })
    await sleep(wait)
    return p
  }

  let p = await open('/workout/run?demo=1&demo_seek=10&demo_speed=0.01', 3200)
  await p.screenshot({ path: path.join(OUT, 'run_start.png') })
  await p.close()
  console.log('run_start')

  p = await open('/territory/settle/33', 3200)
  await p.evaluate(() => {
    const s = document.querySelector('.scroll')
    if (s) s.scrollTop = 460
  })
  await sleep(700)
  await p.screenshot({ path: path.join(OUT, 'settle2.png') })
  await p.close()
  console.log('settle2')
  await browser.close()
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
