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
    },
  },
])
