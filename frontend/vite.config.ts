import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Vite 配置：@ 别名指向 src；开发时把 /api 代理到后端 8000 端口（免跨域）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 0.0.0.0 = 监听所有 IPv4 网卡：电脑用 127.0.0.1/localhost 访问，
    // 手机连同一 Wi-Fi 用局域网 IP（如 http://192.168.4.121:5173）访问
    host: '0.0.0.0',
    // 允许通过内网穿透的公网域名访问（vite 默认会拦截陌生 Host 头）
    allowedHosts: true,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/uploads': { target: 'http://localhost:8000', changeOrigin: true }, // 识别的图片
    },
  },
})
