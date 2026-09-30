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
      { text: '使用指南', link: '/features/brief', activeMatch: '/features/' },
      { text: '常见问题', link: '/help/faq' },
      { text: '设计理念', link: '/about/philosophy' },
    ],
    sidebar: [
      { text: '开始', items: [{ text: '快速开始', link: '/guide/quick-start' }] },
      {
        text: '使用指南',
        items: [
          { text: '读每天的早报', link: '/features/brief' },
          { text: '读文章与订阅内容', link: '/features/reader' },
          { text: '管理兴趣', link: '/features/interests' },
          { text: '找到更多来源', link: '/features/discover' },
          { text: '问哆啦美与翻译', link: '/features/ask-and-translate' },
          { text: '听播客', link: '/features/podcasts' },
          { text: '在手机上用', link: '/features/mobile' },
          { text: '把订阅接到别的工具', link: '/features/integrations' },
        ],
      },
      {
        text: '参考',
        items: [
          { text: '界面标记速查', link: '/reference/markers' },
          { text: '常见问题与排错', link: '/help/faq' },
        ],
      },
      { text: '了解哆啦美', items: [{ text: '设计理念', link: '/about/philosophy' }] },
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
