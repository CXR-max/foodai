/* 逐帧渲染（加速版）：CDP 直连截屏 + 多页并行，绕开 page.screenshot 的等待逻辑 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const FPS = 25
const WORKERS = 3
const DIR = path.resolve(__dirname, '../.tmp_video')
const FRAMES = path.join(DIR, 'frames')
const HTML = 'file:///' + path.join(DIR, 'video.html').replace(/\\/g, '/')

;(async () => {
  fs.rmSync(FRAMES, { recursive: true, force: true })
  fs.mkdirSync(FRAMES, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: 'new',
    args: ['--no-sandbox', '--allow-file-access-from-files', '--font-render-hinting=none', '--hide-scrollbars'],
    protocolTimeout: 300000,
    protocolTimeout: 300000,
  })

  const pages = []
  for (let i = 0; i < WORKERS; i++) {
    const p = await browser.newPage()
    await p.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 })
    p.on('pageerror', (e) => console.log('PAGEERROR', e.message))
    await p.goto(HTML, { waitUntil: 'networkidle0', timeout: 60000 })
    await p.evaluate(() => window.ready)
    const client = await p.createCDPSession()
    pages.push({ page: p, client })
  }

  const total = await pages[0].page.evaluate(() => window.TOTAL)
  const n = Math.round(total * FPS)
  console.log('duration', total, 'sec, frames', n, 'workers', WORKERS)

  const t0 = Date.now()
  let done = 0

  async function run(w) {
    const { page, client } = pages[w]
    for (let i = w; i < n; i += WORKERS) {
      await page.evaluate((t) => window.seek(t), i / FPS)
      const { data } = await client.send('Page.captureScreenshot', {
        format: 'jpeg',
        quality: 90,
        optimizeForSpeed: true,
        captureBeyondViewport: false,
      })
      fs.writeFileSync(path.join(FRAMES, String(i).padStart(5, '0') + '.jpg'), Buffer.from(data, 'base64'))
      done++
      if (done % 100 === 0) {
        const el = (Date.now() - t0) / 1000
        console.log(`frame ${done}/${n}  ${el.toFixed(1)}s  ${(done / el).toFixed(1)} fps`)
      }
    }
  }

  await Promise.all(Array.from({ length: WORKERS }, (_, i) => run(i)))
  console.log('done in', ((Date.now() - t0) / 1000).toFixed(1), 's')
  await browser.close()
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
