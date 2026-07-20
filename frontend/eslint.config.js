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

// ── Legacy bridge 类护栏（重构阶段4 · F8 步骤 B 已完成） ──
// index.css 曾用后代选择器把这些 utility 类改写成令牌语义（`.surface-card .bg-white` ≠ 白）。
// F8-B 已把全部存量（54 处 / 17 文件）令牌化并删除了那段桥接，故这条规则现挂 'error'，
// 纯粹把关「增量」：新代码直接用 --dorami-* 令牌类，不得再写这些依赖已删桥接的裸工具类。
// 说明：bg-white 仅匹配「裸」形态；`bg-white/NN`（带透明度的白玻璃，用于固定紫/深色 Hero、
// 深色代码面、Toast 等主题恒定表面）从不被桥接改写，是正当写法，故不在此拦截。
const FORBIDDEN_BRIDGE = [
  { re: /\bbg-white\b(?!\/)/, msg: 'legacy bridge：请用 bg-[var(--dorami-surface)] 等令牌类替代 bg-white（桥接已删除，直接引用令牌）' },
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

// 静默仪器防回潮(B 残债清尾):工作区 900 字重与仪式性入场编排已整体退场
// (conventions §3/§7),此处拦增量。popover/modal 开合的 animate-in fade-in 属
// feedback 白名单,不禁;禁的是字重回潮与列表/卡片入场位移编排。
const FORBIDDEN_CEREMONY = [
  { re: /\bfont-black\b/, msg: '工作区 900/font-black 已退场(conventions §3):页面级标题 font-extrabold、指标数字 font-bold' },
  { re: /\b(?:row-stagger|entrance-stagger)\b/, msg: '仪式性入场编排已拆除(conventions §7 feedback-only),不要再给列表/卡片加入场' },
  { re: /\bslide-in-from-(?:bottom|top|left|right)(?:-\d+)?\b/, msg: '入场位移动效属仪式性编排(conventions §7 feedback-only);浮层开合复用既有 popover/modal 范式' },
]

function checkCeremony(context, raw, node) {
  if (typeof raw !== 'string') return
  for (const { re, msg } of FORBIDDEN_CEREMONY) {
    if (re.test(raw)) context.report({ node, message: msg })
  }
}

const doramiPlugin = {
  rules: {
    'no-ceremonial-entrance': {
      meta: {
        type: 'suggestion',
        docs: { description: '静默仪器防回潮:禁 font-black 与仪式性入场编排类(row-stagger/slide-in-from-*)' },
        schema: [],
      },
      create(context) {
        return {
          Literal(node) {
            if (typeof node.value === 'string') checkCeremony(context, node.value, node)
          },
          TemplateElement(node) {
            checkCeremony(context, node.value.raw, node)
          },
        }
      },
    },
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
      // Legacy bridge 护栏：F8-B 已令牌化全部存量并删除 index.css 桥接段，规则以 'error' 把关增量。
      // 见 docs/archive/frontend-refactor-{plan,progress}.md。
      'dorami/no-legacy-bridge-class': 'error',
      // 静默仪器防回潮:字重/入场编排增量拦截(B 残债清尾)
      'dorami/no-ceremonial-entrance': 'error',
    },
  },
])
