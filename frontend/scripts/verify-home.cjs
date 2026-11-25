const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:5173'
const OUT = path.resolve(__dirname, '../../docs/screenshots')
const TOK = fs.readFileSync(path.resolve(__dirname, '../../backend/_tok.txt'), 'utf-8').trim()

async function main() {
  const browser = await puppeteer.launch({
    executablePath: EDGE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  })
  const page = await browser.newPage()
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
  await page.evaluate((t) => localStorage.setItem('token', t), TOK)
  await page.goto(BASE + '/workout', { waitUntil: 'domcontentloaded' })
  await new Promise((r) => setTimeout(r, 3500))
  await page.screenshot({ path: path.join(OUT, 'w-01-home.png') })
  console.log(await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').slice(0, 180)))
  await browser.close()
}
main().catch((e) => { console.error(e); process.exit(1) })
