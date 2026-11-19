/**
 * 生成手机访问二维码（局域网 IPv4 + 公网 IPv6）
 * 运行：node qr.cjs
 */
const QRCode = require('qrcode')
const path = require('path')

const OUT = path.resolve(__dirname, '../../docs/screenshots')

const targets = [
  {
    file: 'qr-lan.png',
    label: '局域网',
    url: process.env.LAN_URL || 'http://172.20.10.7:4173',
  },
  {
    file: 'qr-wan.png',
    label: '公网IPv6',
    url: process.env.WAN_URL || '',
  },
]

async function main() {
  for (const t of targets) {
    if (!t.url) {
      console.log(`skip ${t.label}（地址为空）`)
      continue
    }
    await QRCode.toFile(path.join(OUT, t.file), t.url, {
      width: 640,
      margin: 2,
      errorCorrectionLevel: 'M',
      color: { dark: '#1b2430', light: '#ffffff' },
    })
    console.log(`${t.file}  <-  ${t.url}`)
  }
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
