import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // 将 /api 开头的请求转发到 http://127.0.0.1:3876，去掉 /api 前缀
      '/api': {
        target: 'http://127.0.0.1:3876',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      }
    }
  }
})
