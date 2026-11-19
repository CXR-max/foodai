const puppeteer = require('puppeteer-core')
const BASE = process.env.BASE || 'https://bolt-raised-encoding-graphics.trycloudflare.com'

async function main() {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
    defaultViewport: { width: 390, height: 844, isMobile: true, hasTouch: true },
  })
  const page = await browser.newPage()
  const logs = []
  page.on('console', (m) => logs.push(`console.${m.type()} ${m.text().slice(0, 200)}`))
  page.on('pageerror', (e) => logs.push('pageerror ' + String(e).slice(0, 300)))
  page.on('requestfailed', (r) => logs.push('FAIL ' + r.url().slice(0, 160) + ' ' + (r.failure()?.errorText || '')))
  page.on('response', (r) => {
    const u = r.url()
    if (u.includes('/assets/') || u.includes('/api/')) {
      logs.push(`RESP ${r.status()} ${r.headers()['content-type'] || ''} ${u.slice(-90)}`)
    }
  })

  await page.goto(BASE + '/login', { waitUntil: 'networkidle2', timeout: 90000 })
  await new Promise((r) => setTimeout(r, 2000))
  await page.evaluate(() => localStorage.clear())
  await page.reload({ waitUntil: 'networkidle2', timeout: 90000 })
  await new Promise((r) => setTimeout(r, 2000))

  const state0 = await page.evaluate(() => {
    const app = document.querySelector('#app')?.__vue_app__
    const router = app?.config?.globalProperties?.$router
    const pinia = app?.config?.globalProperties?.$pinia
    return {
      url: location.pathname,
      app: !!app,
      router: !!router,
      route: router?.currentRoute?.value?.path,
      routes: router?.getRoutes?.().map((r) => r.path).slice(0, 8),
      token: pinia?.state?.value?.auth?.token,
      htmlLen: document.querySelector('#app')?.innerHTML.length,
    }
  })
  console.log('state0', state0)

  await page.type('input[placeholder="用户名"]', 'demo_xiaoming')
  await page.type('input[placeholder="密码"]', 'demo123456')
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('登 录'))
    if (b) b.click()
  })
  await new Promise((r) => setTimeout(r, 6000))
  const state1 = await page.evaluate(() => {
    const app = document.querySelector('#app')?.__vue_app__
    const router = app?.config?.globalProperties?.$router
    const pinia = app?.config?.globalProperties?.$pinia
    return {
      url: location.pathname,
      route: router?.currentRoute?.value?.path,
      token: pinia?.state?.value?.auth?.token,
      htmlLen: document.querySelector('#app')?.innerHTML.length,
    }
  })
  console.log('state1', state1)

  const push = await page.evaluate(async () => {
    const app = document.querySelector('#app')?.__vue_app__
    const router = app?.config?.globalProperties?.$router
    try {
      const r = await router.push('/dashboard')
      return { ok: true, r: String(r), url: location.pathname, route: router.currentRoute.value.path }
    } catch (e) {
      return { ok: false, err: String(e), url: location.pathname, route: router.currentRoute.value.path }
    }
  })
  console.log('manual push', push)
  await new Promise((r) => setTimeout(r, 3000))
  console.log('after push', await page.evaluate(() => ({ url: location.pathname, text: document.body.innerText.replace(/\s+/g, ' ').slice(0, 80) })))
  console.log('logs:\n' + logs.slice(-40).join('\n'))
  await browser.close()
}
main().catch((e) => { console.error('FAILED', e); process.exit(1) })
