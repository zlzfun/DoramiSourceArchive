import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import Layout from './Layout.vue'
import FeatureRow from './FeatureRow.vue'
import CropShot from './CropShot.vue'
import InterestDemo from './InterestDemo.vue'
import ReadingDemo from './ReadingDemo.vue'
import HomeWalkthrough from './HomeWalkthrough.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ app }) {
    app.component('FeatureRow', FeatureRow)
    app.component('CropShot', CropShot)
    app.component('InterestDemo', InterestDemo)
    app.component('ReadingDemo', ReadingDemo)
    app.component('HomeWalkthrough', HomeWalkthrough)
  },
} satisfies Theme
