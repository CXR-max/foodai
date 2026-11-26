/* 抓竖屏演示视频素材：手机视口 390x844 @3x，顺序真实操作一遍「跑一次闭环」 */
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
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--font-render-hinting=none'],
  })

  async function open(url, wait = 3000, opts = {}) {
    const page = await browser.newPage()
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
    await page.evaluateOnNewDocument((t) => localStorage.setItem('token', t), TOKEN)
    const errs = []
    page.on('pageerror', (e) => errs.push(e.message))
    await page.goto(BASE + url, { waitUntil: 'networkidle2', timeout: 90000 })
    await sleep(wait)
    if (errs.length) console.log('  ERR', url, errs.join('|'))
    return page
  }
  const shot = async (page, name, full = false) => {
    const f = path.join(OUT, name + '.png')
    await page.screenshot({ path: f, fullPage: full })
    const d = await page.evaluate(() => ({ w: innerWidth, h: innerHeight, sh: document.documentElement.scrollHeight }))
    console.log(' ', name, JSON.stringify(d))
  }

  // 1 运动页（领地地图在首屏）
  let p = await open('/m/sport', 3200)
  await shot(p, 'sport')
  await p.close()

  // 2 领地地图
  p = await open('/territory', 4500)
  await shot(p, 'map')
  await p.close()

  // 3 领地详情（好友 小明）
  p = await open('/territory/owner/1', 3200)
  await shot(p, 'detail')
  // 4 下战书
  const clicked = await p.evaluate(() => {
    const b = [...document.querySelectorAll('.btn.danger')].find((el) => el.innerText.includes('战书'))
    if (b) { b.click(); return b.innerText.trim() }
    return null
  })
  console.log('  challenge btn:', clicked)
  await sleep(900)
  await shot(p, 'dialog')
  // 关掉弹窗（晚点再跑），保留「战书中」状态
  await p.evaluate(() => {
    const b = [...document.querySelectorAll('.el-message-box__btns button')]
      .find((el) => el.innerText.includes('晚点'))
    b?.click()
  })
  await sleep(1200)
  await shot(p, 'challenged')
  await p.close()

  // 5 跑步页：倒计时 + 闭环各阶段（demo 快进）
  const run = async (seek, name, extraWait = 2200) => {
    const page = await open(`/workout/run?demo=1&demo_seek=${seek}&demo_speed=0.01`, 2600)
    await sleep(extraWait)
    await shot(page, name)
    return page
  }
  let c = await open('/workout/run?demo=1&demo_speed=0.01', 1200)
  await shot(c, 'count')
  await c.close()
  for (const [seek, name] of [[78, 'run_q1'], [157, 'run_half'], [240, 'run_q3'], [322, 'run_closed']]) {
    const page = await run(seek, name, 1600)
    await page.close()
  }

  // 6 闭环 → 长按结束 → 保存 → 结算（真实走一遍）
  p = await open('/workout/run?demo=1&demo_seek=322&demo_speed=0.01', 2600)
  await sleep(1600)
  const box = await p.evaluate(() => {
    const r = document.querySelector('.act-stop').getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  })
  await p.mouse.move(box.x, box.y)
  await p.mouse.down()
  await sleep(420)
  await shot(p, 'run_press')
  await sleep(700)
  await p.mouse.up()
  await sleep(4500)
  console.log('  after save url:', p.url())
  await shot(p, 'settle', true)
  await p.close()

  await browser.close()
  console.log('done ->', OUT)
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
