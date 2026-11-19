// 小米手环 Web Bluetooth 直连 + 环境自检
//
// 先说结论（避免用户白点）：
// - 手机 Chrome / 电脑 Chrome·Edge 才可能弹出蓝牙选择器；
//   微信内置浏览器、QQ 内置浏览器用的是 WebView，没有 Web Bluetooth 接口。
// - 必须是 https 或 localhost（安全上下文），http://192.168.x.x 会被浏览器直接禁用。
// - 小米手环 4 以后（含手环 10）步数通道加密，没有 auth key 读不到步数，
//   只能读到电量 / 固件这类免认证信息 —— 页面会如实说明并引导走「导入数据包」。
//
// 所以本模块的职责是：能连就尽量连，连不上就把「为什么」原样摆给用户看，
// 而不是让按钮点了没反应。
import { ref } from 'vue'

/** Mi 常用服务/特征 */
const UUID = {
  miService: 0xfee0,
  miSteps: '00000007-0000-3512-2118-0009af100700',
  miBatteryChar: '00000006-0000-3512-2118-0009af100700',
  miAuth: '00000009-0000-3512-2118-0009af100700',
  miInfo: '00000004-0000-3512-2118-0009af100700',
  batteryService: 0x180f,
  batteryLevel: 0x2a19,
  hrService: 0x180d,
  hrMeasure: 0x2a37,
  deviceInfo: 0x180a,
  firmware: 0x2a26,
} as const

export interface BandSnapshot {
  ble_name: string
  device_id: string        // 浏览器内唯一 id（同一次配对保持稳定）
  steps: number | null
  battery: number | null
  heart_rate: number | null
  firmware: string | null
  needs_auth: boolean
  unsupported: string[]    // 本次没读到的原因列表
}

export type DiagState = 'ok' | 'warn' | 'bad'

export interface DiagItem {
  label: string
  value: string
  state: DiagState
  hint?: string
}

export interface Diagnosis {
  canTry: boolean
  reason: '' | 'INSECURE' | 'WECHAT' | 'IOS' | 'NO_BLUETOOTH'
  title: string
  items: DiagItem[]
}

const supportsBluetooth = () =>
  typeof navigator !== 'undefined' && !!(navigator as any).bluetooth

function uaText() {
  return typeof navigator === 'undefined' ? '' : navigator.userAgent
}

function browserName(ua: string) {
  if (/MicroMessenger/i.test(ua)) return '微信内置浏览器'
  if (/\bQQ\//.test(ua)) return 'QQ 内置浏览器'
  if (/EdgA?\//i.test(ua)) return 'Edge'
  if (/Chrome\//i.test(ua)) return 'Chrome'
  if (/SamsungBrowser/i.test(ua)) return '三星浏览器'
  if (/Firefox\//i.test(ua)) return 'Firefox'
  if (/Safari\//i.test(ua)) return 'Safari'
  return '未知浏览器'
}

function platformName(ua: string) {
  if (/Android/i.test(ua)) return '安卓'
  if (/iPad|iPhone|iPod/i.test(ua)) return 'iOS'
  if (/Windows/i.test(ua)) return 'Windows'
  if (/Macintosh|Mac OS X/i.test(ua)) return 'macOS'
  return '未知系统'
}

export function useMiBand() {
  const connecting = ref(false)
  const readingHr = ref(false)
  const lastError = ref('')
  const server = ref<any>(null)
  const device = ref<any>(null)

  /** 把「为什么用不了蓝牙」拆成一条条可读的事实，直接展示给用户 */
  function diagnose(): Diagnosis {
    const ua = uaText()
    const browser = browserName(ua)
    const platform = platformName(ua)
    const isWeChat = browser === '微信内置浏览器' || browser === 'QQ 内置浏览器'
    const isIOS = platform === 'iOS'
    const hasBT = supportsBluetooth()
    const secure = typeof window !== 'undefined' && !!window.isSecureContext
    const origin = typeof location !== 'undefined' ? `${location.protocol}//${location.host}` : ''

    const items: DiagItem[] = [
      {
        label: '浏览器 / 系统',
        value: `${browser} · ${platform}`,
        state: isWeChat || isIOS ? 'bad' : 'ok',
        hint: isWeChat ? '内置浏览器没有蓝牙接口，请点右上角「…」→ 在浏览器打开'
          : isIOS ? 'iOS 所有浏览器都不支持 Web Bluetooth' : undefined,
      },
      {
        label: '安全上下文',
        value: secure ? `是（${origin}）` : `否（${origin || '未知地址'}）`,
        state: secure ? 'ok' : 'bad',
        hint: secure ? undefined : '浏览器只允许 https 或 localhost 调用蓝牙；用「启动手机访问.bat」拿 https 地址',
      },
      {
        label: 'Web Bluetooth 接口',
        value: hasBT ? '可用' : '不可用',
        state: hasBT ? 'ok' : 'bad',
        hint: hasBT ? undefined : '换 Chrome / Edge 打开；微信里打开一定没有',
      },
      {
        label: '蓝牙开关 / 定位权限',
        value: '需要手机蓝牙开启、浏览器有「附近设备」或定位权限',
        state: 'warn',
        hint: '安卓上关闭定位权限时，扫描结果会是空的（能看到选择框但没有设备）',
      },
    ]

    let reason: Diagnosis['reason'] = ''
    let title = '环境正常，点按钮会弹出系统蓝牙选择器'
    if (!hasBT && !secure && !isWeChat && !isIOS) {
      reason = 'INSECURE'
      title = '页面不是安全上下文，浏览器把蓝牙接口关掉了'
    } else if (isWeChat) {
      reason = 'WECHAT'
      title = '微信内置浏览器不支持蓝牙，需要在系统浏览器里打开'
    } else if (isIOS) {
      reason = 'IOS'
      title = 'iOS 上的浏览器都不支持 Web Bluetooth'
    } else if (!hasBT) {
      reason = 'NO_BLUETOOTH'
      title = '当前浏览器没有 Web Bluetooth 接口'
    }
    if (!hasBT && !reason) {
      reason = 'NO_BLUETOOTH'
      title = '当前浏览器没有 Web Bluetooth 接口'
    }

    return { canTry: hasBT && secure && !isWeChat && !isIOS, reason, title, items }
  }

  /** 兼容旧调用：返回 undefined 表示可用，字符串是原因代码 */
  function checkSupport(): string | undefined {
    const d = diagnose()
    if (d.canTry) return undefined
    return d.reason === 'INSECURE' ? 'INSECURE' : d.title
  }

  /** 弹出系统蓝牙选择器 */
  async function requestDevice(): Promise<{ name: string; id: string } | null> {
    const d = diagnose()
    if (!d.canTry) throw new Error(d.reason === 'INSECURE' ? 'INSECURE' : d.title)

    const bt = (navigator as any).bluetooth
    try {
      // 只按服务过滤时，很多手环（包括手环 10）广播包里不带 0xfeE0，
      // 选择器里会一个设备都不显示 —— 看起来就像「点了没反应」。
      // 所以列出附近全部 BLE 设备，再按名字自己认。
      device.value = await bt.requestDevice({
        acceptAllDevices: true,
        optionalServices: [
          UUID.miService, UUID.batteryService, UUID.hrService, UUID.deviceInfo,
          UUID.miBatteryChar, UUID.miSteps, UUID.miAuth, UUID.miInfo,
        ],
      })
    } catch (e: any) {
      if (e?.name === 'NotFoundError')
        return null                       // 用户取消选择，不算错误
      if (e?.name === 'SecurityError')
        throw new Error('浏览器拦下了这次蓝牙请求（需要由「点击」直接触发），请再点一次按钮')
      if (e?.name === 'NotSupportedError')
        throw new Error('这台设备/浏览器不支持 Web Bluetooth')
      throw new Error(`蓝牙请求失败：${e?.message || e}`)
    }
    return { name: device.value?.name || '未命名设备', id: device.value?.id || '' }
  }

  async function connect(): Promise<any> {
    if (!device.value)
      throw new Error('请先选择设备')
    server.value = await device.value.gatt.connect()
    return server.value
  }

  async function readBattery(): Promise<number | null> {
    try {
      // 优先标准电池服务
      const svc = await server.value.getPrimaryService(UUID.batteryService)
      const ch = await svc.getCharacteristic(UUID.batteryLevel)
      const v = await ch.readValue()
      return v.getUint8(0)
    } catch {
      try {
        // 小米 fee0 下的私有电池特征（老款）
        const svc = await server.value.getPrimaryService(UUID.miService)
        const ch = await svc.getCharacteristic(UUID.miBatteryChar)
        const v = await ch.readValue()
        return v.getUint8(0)
      } catch {
        return null
      }
    }
  }

  async function readFirmware(): Promise<string | null> {
    try {
      const svc = await server.value.getPrimaryService(UUID.deviceInfo)
      const ch = await svc.getCharacteristic(UUID.firmware)
      const v = await ch.readValue()
      return new TextDecoder().decode(v.buffer).replace(/\0.*$/, '').trim() || null
    } catch {
      return null
    }
  }

  /** 当日步数：fee0/0x0007，先订阅通知（新款只推通知），再尝试直接读 */
  async function readSteps(unsupported: string[]): Promise<number | null> {
    const parse = (v: DataView): number => {
      // 老款 2 字节；新款 4 字节小端
      if (v.byteLength >= 4) {
        const v4 = v.getUint32(0, true)
        if (v4 > 0 && v4 < 300000) return v4
      }
      return v.getUint16(0, true)
    }
    try {
      const svc = await server.value.getPrimaryService(UUID.miService)
      const ch = await svc.getCharacteristic(UUID.miSteps)
      // 先试 notify（多数固件走通知）
      const byNotify = await new Promise<number | null>((resolve) => {
        const timer = setTimeout(() => resolve(null), 4000)
        ch.addEventListener('characteristicvaluechanged', (ev: any) => {
          clearTimeout(timer)
          try { resolve(parse(ev.target.value)) } catch { resolve(null) }
        })
        ch.startNotifications().catch(() => { clearTimeout(timer); resolve(null) })
      })
      if (byNotify != null) {
        try { await ch.stopNotifications() } catch { /* 忽略 */ }
        return byNotify
      }
      const v = await ch.readValue()
      return parse(v)
    } catch (e: any) {
      if (/auth/i.test(e?.message || '') || e?.name === 'SecurityError')
        unsupported.push('步数需要手环认证（小米手环 4 以后加密），请改用「导入手环数据」')
      else
        unsupported.push('该手环未开放步数特征')
      return null
    }
  }

  /** 实时心率：订阅标准 HR 通知，取 8 秒内最新读数 */
  async function readHeartRate(seconds = 8): Promise<number | null> {
    try {
      readingHr.value = true
      const svc = await server.value.getPrimaryService(UUID.hrService)
      const ch = await svc.getCharacteristic(UUID.hrMeasure)
      const value = await new Promise<number | null>((resolve) => {
        let latest: number | null = null
        const timer = setTimeout(() => resolve(latest), seconds * 1000)
        ch.addEventListener('characteristicvaluechanged', (ev: any) => {
          const v: DataView = ev.target.value
          const flags = v.getUint8(0)
          latest = flags & 0x01 ? v.getUint16(1, true) : v.getUint8(1)
        })
        ch.startNotifications().catch(() => { clearTimeout(timer); resolve(null) })
      })
      try { await ch.stopNotifications() } catch { /* 忽略 */ }
      return value
    } catch {
      return null
    } finally {
      readingHr.value = false
    }
  }

  /** 一把梭：选设备 → 连接 → 尽量多读 */
  async function capture(): Promise<BandSnapshot> {
    const d = diagnose()
    if (!d.canTry) throw new Error(d.reason === 'INSECURE' ? 'INSECURE' : d.title)

    connecting.value = true
    lastError.value = ''
    const unsupported: string[] = []
    try {
      const picked = await requestDevice()
      if (!picked)
        throw new Error('CANCELLED')
      await connect()

      const [battery, firmware] = await Promise.all([readBattery(), readFirmware()])
      const steps = await readSteps(unsupported)
      const heart_rate = await readHeartRate()

      await disconnect()
      return {
        ble_name: picked.name,
        device_id: picked.id,
        steps, battery, heart_rate, firmware, unsupported,
        needs_auth: unsupported.some((s) => s.includes('认证')),
      }
    } catch (e: any) {
      lastError.value = e?.message || String(e)
      await disconnect()
      throw e
    } finally {
      connecting.value = false
    }
  }

  async function disconnect() {
    try {
      if (device.value?.gatt?.connected)
        device.value.gatt.disconnect()
    } catch { /* 忽略 */ }
    server.value = null
  }

  return {
    connecting, readingHr, lastError,
    supportsBluetooth, checkSupport, diagnose,
    capture, disconnect,
  }
}
