import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitepress'

const repo = 'https://github.com/zlzfun/DoramiSourceArchive'

export default defineConfig({
  lang: 'zh-CN',
  title: '哆啦美',
  description: '自托管的 AI 资讯阅读器：采集、打分、个人早报与问答',
  cleanUrls: true,
  themeConfig: {
    nav: [
      { text: '快速开始', link: '/guide/quick-start' },
      { text: '设计理念', link: '/design/overview' },
    ],
    sidebar: [
      { text: '上手', items: [{ text: '快速开始', link: '/guide/quick-start' }] },
      { text: '设计理念', items: [{ text: '系统总览', link: '/design/overview' }] },
    ],
    socialLinks: [{ icon: 'github', link: repo }],
    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索', buttonAriaLabel: '搜索' },
          modal: {
            noResultsText: '没有找到结果',
            resetButtonTitle: '清除',
            backButtonTitle: '关闭',
            footer: { selectText: '打开', navigateText: '切换', closeText: '关闭' },
          },
        },
      },
    },
    outline: { level: [2, 3], label: '本页目录' },
    docFooter: { prev: '上一页', next: '下一页' },
    returnToTopLabel: '回到顶部',
    sidebarMenuLabel: '目录',
    darkModeSwitchLabel: '外观',
  },
  vite: {
    // 首页截图直接引用仓库的 docs/assets/readme/，站点目录不另存一份
    server: { fs: { allow: [fileURLToPath(new URL('../..', import.meta.url))] } },
  },
})
