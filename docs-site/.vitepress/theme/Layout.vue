<script setup lang="ts">
import DefaultTheme from 'vitepress/theme'
import CropShot from './CropShot.vue'
import HomeMotion from './HomeMotion.vue'
import briefShot from '../../../docs/assets/readme/01-brief.png'
</script>

<template>
  <DefaultTheme.Layout>
    <template #home-hero-image>
      <div class="hero-stage">
        <span class="hero-glow" aria-hidden="true" />
        <CropShot class="hero-shot" crop="0.259,0.023,0.664,0.54">
          <img :src="briefShot" alt="我的早报：今天的日期、编排说明，下面是按板块排好的文章卡片和新闻价值分">
        </CropShot>
      </div>
    </template>
    <template #layout-bottom>
      <HomeMotion />
    </template>
  </DefaultTheme.Layout>
</template>
