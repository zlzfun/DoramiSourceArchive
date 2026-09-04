import path from 'node:path';
import { defineConfig } from '@tarojs/cli';
import devConfig from './dev';
import prodConfig from './prod';

// https://taro-docs.jd.com/docs/next/config
export default defineConfig(async (merge) => {
  const baseConfig = {
    projectName: 'dorami-miniprogram',
    date: '2026-9-4',
    designWidth: 375,
    deviceRatio: { 640: 2.34 / 2, 750: 1, 375: 2, 828: 1.81 / 2 },
    sourceRoot: 'src',
    outputRoot: 'dist',
    plugins: [],
    defineConstants: {},
    copy: { patterns: [], options: {} },
    framework: 'react',
    compiler: { type: 'webpack5', prebundle: { enable: false } },
    cache: { enable: false },
    alias: {
      '@': path.resolve(__dirname, '..', 'src'),
    },
    sass: {
      // 全局注入设计令牌变量(与 src/app.scss 的 CSS 变量互补:scss 变量用于编译期计算)
      resource: [path.resolve(__dirname, '..', 'src', 'styles', 'vars.scss')],
    },
    mini: {
      postcss: {
        pxtransform: { enable: true, config: {} },
        cssModules: { enable: false },
      },
      webpackChain(chain) {
        chain.merge({ module: { rule: { mjsScript: { test: /\.mjs$/, include: [], type: 'javascript/auto' } } } });
      },
    },
    h5: {},
  };

  if (process.env.NODE_ENV === 'development') return merge({}, baseConfig, devConfig);
  return merge({}, baseConfig, prodConfig);
});
