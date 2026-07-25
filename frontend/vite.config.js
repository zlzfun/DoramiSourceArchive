import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

const appConfig = JSON.parse(readFileSync(new URL('./app.config.json', import.meta.url), 'utf-8'))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // vendor 分包:框架/图表/Markdown 数学渲染三块大依赖各自成 chunk——
        // 内容 hash 随业务代码变动而失效的只有业务 chunk,大依赖长缓存;
        // recharts/katex 本已被 lazy Tab 隔离,这里进一步与业务代码解耦。
        // (vite 8 的 rolldown 仅接受函数式 manualChunks,对象式会构建报错。)
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react';
          if (/[\\/]node_modules[\\/](recharts|d3-[^\\/]+|victory-vendor)[\\/]/.test(id)) return 'vendor-charts';
          if (/[\\/]node_modules[\\/](react-markdown|remark-[^\\/]+|rehype-[^\\/]+|katex|micromark[^\\/]*|mdast-[^\\/]+|unist-[^\\/]+|unified|hast-[^\\/]+|vfile[^\\/]*)[\\/]/.test(id)) return 'vendor-markdown';
          return undefined;
        },
      },
    },
  },
  server: {
    port: appConfig.devServer?.port || 5173,
    proxy: {
      '/api': {
        target: appConfig.devServer?.proxyTarget || 'http://127.0.0.1:8088',
        changeOrigin: true,
      }
    }
  }
})
