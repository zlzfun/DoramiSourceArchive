import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import process from 'node:process'
import { pwaBuild } from './pwa/build.mjs'

const appConfig = JSON.parse(readFileSync(new URL('./app.config.json', import.meta.url), 'utf-8'))
const allowedHosts = (process.env.DORAMI_VITE_ALLOWED_HOSTS || '')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), pwaBuild()],
  build: {
    rolldownOptions: {
      output: {
        // vendor 分包(issue #230 审计 M-14 重排):框架 / 图表 / Markdown 三块大依赖各自成 chunk,业务代码改动不废大依赖的缓存。
        // 必须用 codeSplitting.groups 显式排优先级:rolldown 的分组默认把被捕获模块的依赖递归并进同一组
        // (includeDependenciesRecursively),原先的函数式 manualChunks 各组同级、按发现顺序抢——react 被 recharts
        // 一并拖进了 vendor-charts,入口为了 react 只好静态依赖 vendor-charts,登录页就 modulepreload 了整包图表库。
        // 框架优先级最高(谁都要),图表、Markdown 只有懒加载的管理面 / 正文渲染器(ReaderMarkdownView)才引用,
        // 入口不得静态依赖它们(test/chunkSplit.test.mjs 对构建产物守这一条)。
        codeSplitting: {
          groups: [
            { name: 'vendor-react', test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/, priority: 30 },
            { name: 'vendor-charts', test: /[\\/]node_modules[\\/](recharts|d3-[^\\/]+|victory-vendor)[\\/]/, priority: 20 },
            {
              name: 'vendor-markdown',
              test: /[\\/]node_modules[\\/](react-markdown|remark-[^\\/]+|rehype-[^\\/]+|katex|micromark[^\\/]*|mdast-[^\\/]+|unist-[^\\/]+|unified|hast-[^\\/]+|vfile[^\\/]*)[\\/]/,
              priority: 10,
            },
          ],
        },
      },
    },
  },
  server: {
    port: appConfig.devServer?.port || 5173,
    // Keep preview hosts deployment-configured instead of committing a
    // machine- or environment-specific hostname.
    allowedHosts,
    proxy: {
      '/api': {
        // 隔离栈验收时用环境变量把代理指向临时后端(VITE_PROXY_TARGET=http://127.0.0.1:8099)
        target: process.env.VITE_PROXY_TARGET || appConfig.devServer?.proxyTarget || 'http://127.0.0.1:8088',
        changeOrigin: true,
      }
    }
  }
})
