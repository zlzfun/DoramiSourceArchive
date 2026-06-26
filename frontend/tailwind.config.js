/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    // 暗色变体不在此声明：Tailwind v4 不读本文件的 darkMode 键，dark: 的接线在
    // src/index.css 里用 `@custom-variant dark (&:where([data-theme="dark"], ...))` 完成。
    // 绝大多数翻转靠 index.css 的令牌重定义 + Tailwind --color-* 重映射，仅双角色翻车点用 dark: 补丁。
    theme: {
        extend: {},
    },
    plugins: [],
}