<script setup lang="ts">
import { computed } from 'vue'

// 从整张截图里裁出一块显示，不另存裁剪后的图片
const props = defineProps<{
  // "x,y,宽,高"，均为占整图的比例
  crop: string
  // 整图宽高比
  ratio?: number
  // 最大显示宽度（px）
  width?: number
}>()

const style = computed(() => {
  const [x, y, w, h] = props.crop.split(',').map(Number)
  const ratio = props.ratio ?? 1.6
  return {
    frame: {
      aspectRatio: `${(w * ratio) / h}`,
      maxWidth: props.width ? `${props.width}px` : undefined,
    },
    img: {
      width: `${100 / w}%`,
      left: `${(-x / w) * 100}%`,
      top: `${(-y / h) * 100}%`,
    },
  }
})
</script>

<template>
  <div class="crop-shot" :style="style.frame">
    <div class="crop-shot-img" :style="style.img"><slot /></div>
  </div>
</template>

<style scoped>
.crop-shot {
  position: relative;
  overflow: hidden;
  margin: 16px auto;
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 8px 24px rgba(15, 23, 42, 0.08);
}
.crop-shot-img {
  position: absolute;
}
.crop-shot-img :deep(img) {
  display: block;
  width: 100%;
  max-width: none;
  margin: 0;
  box-shadow: none;
  border-radius: 0;
}
</style>
