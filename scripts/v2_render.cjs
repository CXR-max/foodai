/* 渲染竖屏 1080x1920 逐帧 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const FPS = 25
const WORKERS = 3
const DIR = path.resolve(__dirname, '../.tmp_video')
const FRAMES = path.join(DIR, 'v3frames')
const HTML = 'file:///' + path.join(DIR, 'v2.html').replace(/\\/g, '/')
const PREVIEW = process.argv.includes('--preview')
const PREVIEW_T = [1.0, 4.0, 7.2, 9.2, 11.6, 15.0, 20.6, 23.2, 26.0, 30.4]

;(async () => {
  fs.mkdirSync(FRAMES, { recursive: true })

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: 'new',
    args: ['--no-sandbox', '--allow-file-access-from-files', '--font-render-hinting=none', '--hide-scrollbars'],
    protocolTimeout: 300000,
  })

  if (PREVIEW) {
    const p = await browser.newPage()
    await p.setViewport({ width: 1080, height: 1920, deviceScaleFactor: 1 })
    await p.goto(HTML, { waitUntil: 'networkidle0', timeout: 60000 })
    await p.evaluate(() => window.ready)
    for (const t of PREVIEW_T) {
      await p.evaluate((tt) => window.seek(tt), t)
      await new Promise((r) => setTimeout(r, 260))
      await p.screenshot({ path: path.join(DIR, `preview_${t.toFixed(1)}.png`) })
      console.log('preview', t)
    }
    await browser.close()
    return
  }

  const pages = []
  for (let i = 0; i < WORKERS; i++) {
    const p = await browser.newPage()
    await p.setViewport({ width: 1080, height: 1920, deviceScaleFactor: 1 })
    p.on('pageerror', (e) => console.log('PAGEERROR', e.message))
    await p.goto(HTML, { waitUntil: 'networkidle0', timeout: 60000 })
    await p.evaluate(() => window.ready)
    const client = await p.createCDPSession()
    pages.push({ page: p, client })
  }

  const total = await pages[0].page.evaluate(() => window.TOTAL)
  const n = Math.round(total * FPS)
  console.log('duration', total, 'frames', n)

  const t0 = Date.now()
  let done = 0
  async function run(w) {
    let { page, client } = pages[w]
    const newer = async () => {
      await page.close().catch(() => {})
      page = await browser.newPage()
      await page.setViewport({ width: 1080, height: 1920, deviceScaleFactor: 1 })
      await page.goto(HTML, { waitUntil: 'networkidle0', timeout: 60000 })
      await page.evaluate(() => window.ready)
      client = await page.createCDPSession()
      pages[w] = { page, client }
    }
    for (let i = w; i < n; i += WORKERS) {
      await page.evaluate((t) => window.seek(t), i / FPS)
      let buf = null
      for (let k = 0; k < 4 && !buf; k++) {
        try {
          const { data } = await client.send('Page.captureScreenshot', {
            format: 'jpeg', quality: 92, optimizeForSpeed: true, captureBeyondViewport: false,
          })
          buf = Buffer.from(data, 'base64')
        } catch (e) {
          console.log('  retry frame', i, k, e.message.slice(0, 40))
          await newer()
          await page.evaluate((t) => window.seek(t), i / FPS)
        }
      }
      if (!buf) throw new Error('frame ' + i + ' failed')
      fs.writeFileSync(path.join(FRAMES, String(i).padStart(5, '0') + '.jpg'), buf)
      done++
    }
  }
  await Promise.all(Array.from({ length: WORKERS }, (_, i) => run(i)))
  console.log('frames done in', ((Date.now() - t0) / 1000).toFixed(1), 's')
  await browser.close()
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
