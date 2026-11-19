import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import basicSsl from '@vitejs/plugin-basic-ssl'
import { defineConfig } from 'vite'

// 融合：手机访问时把 VITE_HTTPS=1，启用自签名证书的 https。
// Web Bluetooth（浏览器蓝牙连手环）要求安全上下文，http 局域网地址调不了蓝牙。
// 不设这个变量时保持主分支默认的 http 行为，桌面开发不受影响。
const useHttps = process.env.VITE_HTTPS === '1'

// 后端地址：默认 8000；本机 8000 被旧进程占用时，可用 VITE_API_TARGET 指到 8001
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000'

// Vite 配置：@ 别名指向 src；开发/预览都把 /api 代理到后端（免跨域）
export default defineConfig({
  plugins: [vue(), ...(useHttps ? [basicSsl()] : [])],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 0.0.0.0 = 监听所有 IPv4 网卡：电脑用 127.0.0.1/localhost 访问，
    // 手机连同一 Wi-Fi 用局域网 IP（如 http://192.168.4.121:5173）访问
    host: process.env.VITE_HOST || '0.0.0.0',
    // 允许通过内网穿透的公网域名访问（vite 默认会拦截陌生 Host 头）
    allowedHosts: true,
    // 融合：VITE_HTTPS=1 时加载 basicSsl，手机端 https://<局域网IP>:5173 可用
    ...(useHttps ? { https: {} } : {}),
    // 编辑器原子写入会在 src 下产生 .xxx.tmpdir/ 临时目录，watch 它会在 Windows 上 EBUSY 崩溃
    watch: { ignored: ['**/.*.tmpdir/**', '**/*.tmp'] },
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/uploads': { target: API_TARGET, changeOrigin: true }, // 识别的图片
    },
  },
  preview: {
    port: 4173,
    host: '0.0.0.0',
    allowedHosts: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/uploads': { target: API_TARGET, changeOrigin: true },
    },
  },
})
