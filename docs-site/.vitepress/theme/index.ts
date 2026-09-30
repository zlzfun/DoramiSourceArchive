import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import Layout from './Layout.vue'
import FeatureRow from './FeatureRow.vue'
import CropShot from './CropShot.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app }) {
    app.component('FeatureRow', FeatureRow)
    app.component('CropShot', CropShot)
  },
} satisfies Theme
