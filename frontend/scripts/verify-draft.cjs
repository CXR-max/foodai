/**
 * 验证草稿恢复：演示模式跑几秒 → 刷新页面 → 弹“发现未完成的运动” → 恢复
 */
const puppeteer = require('puppeteer-core')
const path = require('path')
const fs = require('fs')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:5173'
const OUT = path.resolve(__dirname, '../../docs/screenshots')
const TOK = fs.readFileSync(path.resolve(__dirname, '../../backend/_tok.txt'), 'utf-8').trim()
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
  })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
  await page.evaluate((t) => {
    localStorage.setItem('token', t)
    localStorage.removeItem('foodai.run.draft.v1')
  }, TOK)

  await page.goto(BASE + '/workout/run?demo=1&sport=' + encodeURIComponent('跑步'), {
    waitUntil: 'domcontentloaded', timeout: 30000,
  })
  await sleep(7000)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await sleep(2000)
  const dialog = await page.evaluate(() => document.body.innerText.includes('发现未完成的运动'))
  await page.screenshot({ path: path.join(OUT, 'w-07-draft.png') })
  console.log('draft dialog:', dialog)

  if (dialog) {
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('恢复'))
      if (b) b.click()
    })
    await sleep(1500)
    await page.screenshot({ path: path.join(OUT, 'w-08-restored.png') })
    console.log('restored:', await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').slice(0, 120)))
    await page.evaluate(() => localStorage.removeItem('foodai.run.draft.v1'))
  }
  await browser.close()
  console.log('errors:', errors.length ? errors : 'none')
}

main().catch((e) => { console.error('FAILED', e); process.exit(1) })
