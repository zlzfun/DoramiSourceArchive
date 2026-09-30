<script setup lang="ts">
import { withBase } from 'vitepress'
import CropShot from './CropShot.vue'

defineProps<{
  title: string
  link: string
  linkText?: string
  reverse?: boolean
  // 传入时从整张截图里裁一块显示，格式同 CropShot
  crop?: string
  ratio?: number
  // 手机截图等竖图：限制显示宽度
  narrow?: boolean
}>()
</script>

<template>
  <section class="fr" :class="{ 'fr-reverse': reverse }">
    <div class="fr-text">
      <h2 class="fr-title">{{ title }}</h2>
      <div class="fr-body"><slot /></div>
      <a class="fr-link" :href="withBase(link)">{{ linkText ?? '了解更多' }} →</a>
    </div>
    <div class="fr-media" :class="{ 'fr-narrow': narrow }">
      <CropShot v-if="crop" class="fr-frame" :crop="crop" :ratio="ratio"><slot name="media" /></CropShot>
      <div v-else class="fr-frame"><slot name="media" /></div>
    </div>
  </section>
</template>
