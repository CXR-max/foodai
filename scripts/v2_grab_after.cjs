const puppeteer = require('puppeteer-core')
const path = require('path')
const BASE = 'http://127.0.0.1:5173'
const TOKEN = process.argv[2]
const OUT = path.resolve(__dirname, '../.tmp_video/v2shots')
;(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: 'new', args: ['--no-sandbox', '--font-render-hinting=none'] })
  const p = await b.newPage()
  await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
  await p.evaluateOnNewDocument((t) => localStorage.setItem('token', t), TOKEN)
  await p.goto(BASE + '/territory', { waitUntil: 'networkidle2', timeout: 90000 })
  await new Promise((r) => setTimeout(r, 5200))
  await p.screenshot({ path: path.join(OUT, 'map_after.png') })
  await b.close()
  console.log('map_after ok')
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
