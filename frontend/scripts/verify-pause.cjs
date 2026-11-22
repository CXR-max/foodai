/**
 * 验证暂停/恢复：暂停期间轨迹不应累计，恢复后不应出现跨暂停段的直线
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
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
  await page.evaluate((t) => {
    localStorage.setItem('token', t)
    localStorage.removeItem('foodai.run.draft.v1')
  }, TOK)
  await page.goto(BASE + '/workout/run?demo=1&sport=' + encodeURIComponent('跑步'), {
    waitUntil: 'domcontentloaded', timeout: 30000,
  })

  const dist = () => page.evaluate(() => {
    const b = document.querySelector('.metric-main b')?.textContent || '0'
    const u = document.querySelector('.metric-main span')?.textContent || 'm'
    return Number(b) * (u === 'km' ? 1000 : 1)
  })

  await sleep(7000)
  const before = await dist()
  await page.click('.act-pause')
  await sleep(8000)
  const paused = await dist()
  await page.screenshot({ path: path.join(OUT, 'w-09-paused.png') })
  await page.click('.act-pause')
  await sleep(4000)
  const after = await dist()
  await page.screenshot({ path: path.join(OUT, 'w-10-resumed.png') })
  console.log({ before, paused, after })
  console.log('pause 期间未累计:', paused === before)
  console.log('恢复后未跨段:', after - before < 60)
  await page.evaluate(() => localStorage.removeItem('foodai.run.draft.v1'))
  await browser.close()
}

main().catch((e) => { console.error('FAILED', e); process.exit(1) })
