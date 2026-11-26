/* 只抓「跑步中 → 闭环 → 长按结束 → 结算」这一段 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const OUT = path.resolve(__dirname, '../.tmp_video/v2shots')
fs.mkdirSync(OUT, { recursive: true })
const BASE = 'http://127.0.0.1:5173'
const TOKEN = process.argv[2]
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--font-render-hinting=none'] })

  async function open(url, wait) {
    const page = await browser.newPage()
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
    await page.evaluateOnNewDocument((t) => {
      localStorage.setItem('token', t)
      localStorage.removeItem('foodai.run.draft.v1')
    }, TOKEN)
    page.on('pageerror', (e) => console.log('  ERR', e.message))
    await page.goto(BASE + url, { waitUntil: 'networkidle2', timeout: 90000 })
    await sleep(wait)
    return page
  }
  const shot = async (page, name, full = false) => {
    await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: full })
    console.log(' ', name)
  }

  const c = await open('/workout/run?demo=1&demo_speed=0.01', 1100)
  await shot(c, 'count')
  await c.close()

  for (const [seek, name] of [[78, 'run_q1'], [157, 'run_half'], [240, 'run_q3'], [320, 'run_closed']]) {
    const p = await open(`/workout/run?demo=1&demo_seek=${seek}&demo_speed=0.01`, 3600)
    await shot(p, name)
    await p.close()
  }

  // 真实走完：闭环 → 长按 → 保存 → 结算
  const p = await open('/workout/run?demo=1&demo_seek=320&demo_speed=0.01', 3800)
  const box = await p.evaluate(() => {
    const r = document.querySelector('.act-stop').getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  })
  await p.mouse.move(box.x, box.y)
  await p.mouse.down()
  await sleep(430)
  await shot(p, 'run_press')
  await sleep(800)
  await p.mouse.up()
  try {
    await p.waitForFunction(() => !location.pathname.startsWith('/workout/run'), { timeout: 20000 })
  } catch { console.log('  !! 未跳转', p.url()) }
  await sleep(3200)
  console.log('  url', p.url())
  await shot(p, 'settle', true)
  await p.close()

  await browser.close()
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
