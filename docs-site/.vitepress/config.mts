import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitepress'

export default defineConfig({
  lang: 'zh-CN',
  title: '哆啦美',
  description: '哆啦美使用手册：每天一份按你的订阅和兴趣编排的 AI 资讯早报',
  cleanUrls: true,
  themeConfig: {
    nav: [
      { text: '快速开始', link: '/guide/quick-start' },
      { text: '使用指南', link: '/features/brief' },
    ],
    sidebar: [
      { text: '开始', items: [{ text: '快速开始', link: '/guide/quick-start' }] },
      { text: '使用指南', items: [{ text: '读每天的早报', link: '/features/brief' }] },
    ],
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
    // 截图直接引用仓库的 docs/assets/readme/，站点目录不另存一份
    server: { fs: { allow: [fileURLToPath(new URL('../..', import.meta.url))] } },
  },
})
