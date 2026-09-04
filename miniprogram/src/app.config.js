export default defineAppConfig({
  pages: [
    'pages/feed/article/index',
    'pages/feed/podcast/index',
    'pages/feed/bulletin/index',
    'pages/feed/social/index',
    'pages/me/index',
    'pages/login/index',
    'pages/article/index',
    'pages/sources/index',
    'pages/discover/index',
  ],
  window: {
    navigationBarTitleText: '哆啦美',
    navigationBarBackgroundColor: '@navBg',
    navigationBarTextStyle: '@navText',
    backgroundColor: '@bg',
    backgroundTextStyle: '@bgText',
  },
  // 原生 tabBar 5 项(方案 §3.5 建议档):早报入口下沉到「我的」与文章页顶部,发现/源过滤为 push 页。
  // 图标待 P1 目检补 PNG(iconPath 可选,先文字);tabBar 颜色随 theme.json 翻转。
  tabBar: {
    color: '@tabColor',
    selectedColor: '@tabSelected',
    backgroundColor: '@tabBg',
    borderStyle: '@tabBorder',
    list: [
      { pagePath: 'pages/feed/article/index', text: '文章' },
      { pagePath: 'pages/feed/podcast/index', text: '播客' },
      { pagePath: 'pages/feed/bulletin/index', text: '动态' },
      { pagePath: 'pages/feed/social/index', text: '社交' },
      { pagePath: 'pages/me/index', text: '我的' },
    ],
  },
  darkmode: true,
  themeLocation: 'theme.json',
  requiredBackgroundModes: ['audio'],
  lazyCodeLoading: 'requiredComponents',
});
