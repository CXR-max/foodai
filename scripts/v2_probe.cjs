const puppeteer = require('puppeteer-core')
const BASE = 'http://127.0.0.1:5173'
const TOKEN = process.argv[2]
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'

;(async () => {
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox'] })
  const go = async (url, wait = 3000) => {
    const page = await browser.newPage()
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true })
    await page.evaluateOnNewDocument((t) => localStorage.setItem('token', t), TOKEN)
    const errs = []
    page.on('pageerror', (e) => errs.push(e.message))
    await page.goto(BASE + url, { waitUntil: 'networkidle2', timeout: 60000 })
    await new Promise((r) => setTimeout(r, wait))
    return { page, errs }
  }

  // 领地地图
  let { page, errs } = await go('/territory', 4500)
  console.log('== /territory errs', errs.join('|'))
  console.log(await page.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n').slice(0, 1200)))
  console.log('~', await page.evaluate(() => {
    const out = []
    document.querySelectorAll('button,a,[class*=btn],[class*=act]').forEach((el) => {
      const r = el.getBoundingClientRect()
      if (r.width > 20 && r.height > 12) out.push(`${el.className || el.tagName}|${(el.innerText || '').trim().slice(0, 18)}|${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)}x${Math.round(r.height)}`)
    })
    return out.slice(0, 40).join('\n')
  }))
  await page.close()

  // 详情页
  ;({ page, errs } = await go('/territory/owner/2', 3500))
  console.log('\n== /territory/owner/2 errs', errs.join('|'))
  console.log(await page.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n').slice(0, 1500)))
  console.log('scrollHeight', await page.evaluate(() => document.documentElement.scrollHeight))
  console.log('~', await page.evaluate(() => {
    const out = []
    document.querySelectorAll('button').forEach((el) => {
      const r = el.getBoundingClientRect()
      out.push(`${el.className}|${(el.innerText || '').trim().slice(0, 18)}|${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.width)}x${Math.round(r.height)}`)
    })
    return out.join('\n')
  }))
  await page.close()

  await browser.close()
})().catch((e) => { console.error('FAIL', e.message); process.exit(1) })
