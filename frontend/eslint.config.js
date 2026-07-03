import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

// ── 设计令牌护栏（见 docs/frontend/conventions.md） ──
// 禁止在样式字符串里手写偏离设计系统的值。只匹配「无歧义的 class-token 形态」，
// 因此可安全扫描所有字符串/模板字面量（含 INPUT_CLS 这类常量）而不会误伤 JS 数据里的裸 hex。
// index.css 自身是令牌的事实来源，不在 JS lint 范围内；登录页 .auth-* 同理天然豁免。
const FORBIDDEN = [
  { re: /text-\[\d+px\]/, msg: '禁止手写像素字号 text-[Npx]：请用 Tailwind 字号梯度或角色类（.card-title/.body-text/.micro-label 等）' },
  { re: /rounded-\[\d+px\]/, msg: '禁止手写像素圆角 rounded-[Npx]：请用圆角令牌 rounded-[var(--r-sm|control|card|overlay|pill)]' },
  { re: /-\[#[0-9a-fA-F]{3,8}\]/, msg: '禁止在 class 中手写十六进制色 -[#hex]：请用 --dorami-* 颜色令牌（如 bg-[var(--dorami-blue)]）' },
  { re: /\btext-slate-400\b/, msg: '禁止用 text-slate-400 作文本（对比度仅 ~2.7:1，不达 WCAG AA）：请用 text-slate-500 或 --dorami-faint' },
  { re: /\btext-slate-600\b/, msg: '灰字只用 3 档（强 800 / 正文 700 / 次要 500）：text-slate-600 是已废的中间档，偏正文用 text-slate-700、偏次要用 text-slate-500（见 docs/frontend/conventions.md §2 灰阶深浅基准）' },
]

function checkRaw(context, raw, node) {
  if (typeof raw !== 'string') return
  for (const { re, msg } of FORBIDDEN) {
    if (re.test(raw)) context.report({ node, message: msg })
  }
}

// ── Legacy bridge 类冻结（重构阶段4 · F8 步骤 A） ──
// index.css:5262+ 用后代选择器把这些 utility 类改写成令牌语义（`.surface-card .bg-white`
// ≠ 白）。目标是逐步令牌化后删掉桥接。这条规则冻结「增量」：新代码不得再写这些类。
// 存量（重构时 45 处 / 16 文件，见 frontend-refactor-progress.md）在步骤 B 迁移期逐批替换。
// 现挂 'off'——避免在刚清零的 lint 里灌入 45 条存量告警；开始步骤 B 时把它翻成 'warn'，
// 存量清零后翻 'error' 并删除 index.css 的桥接段。
const FORBIDDEN_BRIDGE = [
  { re: /\bbg-white\b/, msg: 'legacy bridge：请用 bg-[var(--dorami-surface)] 等令牌类替代 bg-white（见 index.css 桥接段）' },
  { re: /\bbg-slate-50(\/\d{1,3})?\b/, msg: 'legacy bridge：请用 bg-[var(--dorami-soft)] 令牌类替代 bg-slate-50' },
  { re: /\bbg-indigo-50\b/, msg: 'legacy bridge：请用 bg-[var(--dorami-wash)] 令牌类替代 bg-indigo-50' },
  { re: /\bbg-blue-50(\/\d{1,3})?\b/, msg: 'legacy bridge：请用 bg-[var(--dorami-wash)] 令牌类替代 bg-blue-50' },
  { re: /\bborder-slate-(100|200)\b/, msg: 'legacy bridge：请用 border-[var(--dorami-border)] 令牌类替代 border-slate-100/200' },
  { re: /\bborder-indigo-200\b/, msg: 'legacy bridge：请用 border-[var(--dorami-border-strong)] 令牌类替代 border-indigo-200' },
]

function checkBridge(context, raw, node) {
  if (typeof raw !== 'string') return
  for (const { re, msg } of FORBIDDEN_BRIDGE) {
    if (re.test(raw)) context.report({ node, message: msg })
  }
}

const doramiPlugin = {
  rules: {
    'no-hardcoded-style': {
      meta: {
        type: 'problem',
        docs: { description: '前端纪律：样式必须引用设计令牌/角色类，禁止手写 px 字号/圆角、方括号 hex 色、不达 AA 的灰阶' },
        schema: [],
      },
      create(context) {
        return {
          Literal(node) {
            if (typeof node.value === 'string') checkRaw(context, node.value, node)
          },
          TemplateElement(node) {
            checkRaw(context, node.value.raw, node)
          },
        }
      },
    },
    'no-legacy-bridge-class': {
      meta: {
        type: 'suggestion',
        docs: { description: '冻结 index.css 桥接段依赖的 utility 类（bg-white/bg-slate-50/bg-indigo-50/border-slate-100|200/border-indigo-200）；令牌化迁移期用' },
        schema: [],
      },
      create(context) {
        return {
          Literal(node) {
            if (typeof node.value === 'string') checkBridge(context, node.value, node)
          },
          TemplateElement(node) {
            checkBridge(context, node.value.raw, node)
          },
        }
      },
    },
  },
}

export default defineConfig([
  // 护栏规则自身的提示文案里含示例 class 字面量，排除配置文件本体以免自扫误报
  globalIgnores(['dist', 'eslint.config.js']),
  {
    files: ['**/*.{js,jsx}'],
    plugins: { dorami: doramiPlugin },
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      'react-hooks/set-state-in-effect': 'off',
      // 允许 `const { node, ...props }` 这种丢弃 react-markdown node 的惯用写法
      'no-unused-vars': ['error', { ignoreRestSiblings: true }],
      // 设计令牌护栏：收敛已完成，直接以 error 把关
      'dorami/no-hardcoded-style': 'error',
      // Legacy bridge 冻结：现 off（不灌存量告警）；开始 index.css 桥接段令牌化迁移（步骤 B）时翻 'warn'，
      // 存量清零后翻 'error' 并删除桥接段。见 docs/analysis/frontend-refactor-{plan,progress}.md。
      'dorami/no-legacy-bridge-class': 'off',
    },
  },
])
