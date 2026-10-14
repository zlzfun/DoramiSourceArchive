<script setup lang="ts">
import { ref } from 'vue'

// 首页交互小演示：在「原文」和「AI 速读」之间切换，看同一篇文章的两种读法。
// 卡片形态与 docs-site/features/ask-and-translate.md、features/reader.md 对齐。
const mode = ref<'digest' | 'original'>('digest')

const digest = {
  score: 7.2,
  points: [
    '研究团队发布新的智能体框架，在八项基准里的两项拿到第一。',
    '模型在禁用联网的条件下，用 100GB 气象数据训练出 1 分钟级的全球天气预报模型。',
    '隔离靶场里，56 名参与者用 769 条任务记录测了漏洞发现与修复流程。',
  ],
  reason: '新闻价值入选',
}

const original = {
  source: 'TechWire',
  title: 'New agent framework tops two benchmarks, forecasts global weather in one minute',
  body: [
    'The team released an open-weight model and reported first-place results on two of eight benchmarks, with second place on two more.',
    'Trained on 100 GB of weather data without web access, the model produces a one-minute global forecast; the same pipeline builds a runnable MiniOS from a natural-language request in about twenty minutes.',
    'In an isolated range, 56 participants filed 769 task records covering vulnerability discovery and repair.',
  ],
}
</script>

<template>
  <div class="demo-read">
    <div class="demo-read-head">
      <div class="demo-seg" role="group" aria-label="选择读法">
        <span class="demo-seg-thumb" :class="{ 'is-right': mode === 'original' }" aria-hidden="true" />
        <button
          type="button"
          class="demo-seg-btn"
          :class="{ 'is-on': mode === 'digest' }"
          :aria-pressed="mode === 'digest'"
          @click="mode = 'digest'"
        >
          AI 速读
        </button>
        <button
          type="button"
          class="demo-seg-btn"
          :class="{ 'is-on': mode === 'original' }"
          :aria-pressed="mode === 'original'"
          @click="mode = 'original'"
        >
          原文
        </button>
      </div>
      <p class="demo-read-hint">同一篇文章，两种读法</p>
    </div>

    <div class="demo-read-body">
      <Transition name="demo-fade" mode="out-in">
        <div v-if="mode === 'digest'" key="digest" class="demo-digest">
          <div class="demo-digest-score">
            <span class="demo-digest-num">{{ digest.score.toFixed(1) }}</span>
            <span class="demo-digest-label">新闻价值</span>
            <span class="demo-digest-reason">{{ digest.reason }}</span>
          </div>
          <div class="demo-digest-points">
            <p class="demo-digest-tag">AI 速读</p>
            <ul>
              <li v-for="point in digest.points" :key="point">{{ point }}</li>
            </ul>
          </div>
        </div>

        <div v-else key="original" class="demo-original">
          <p class="demo-original-source">{{ original.source }}</p>
          <p class="demo-original-title">{{ original.title }}</p>
          <p v-for="para in original.body" :key="para" class="demo-original-para">{{ para }}</p>
        </div>
      </Transition>
    </div>

    <p class="demo-read-foot">示例文章，内容为演示而编。</p>
  </div>
</template>

<style scoped>
.demo-read {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 22px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 16px;
  background-color: var(--vp-c-bg-soft);
}
.demo-read-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

/* 选中指示用一块会移动的底板，而不是旧的熄灭、新的点亮 */
.demo-seg {
  position: relative;
  display: inline-flex;
  padding: 3px;
  border-radius: 999px;
  background-color: var(--vp-c-default-soft);
}
.demo-seg-thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 3px;
  width: calc(50% - 3px);
  border-radius: 999px;
  background-color: var(--vp-c-bg);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
  transition: transform 0.26s cubic-bezier(0.4, 0, 0.2, 1);
}
.demo-seg-thumb.is-right {
  transform: translateX(100%);
}
.demo-seg-btn {
  position: relative;
  z-index: 1;
  min-width: 84px;
  padding: 6px 14px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--vp-c-text-2);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
  cursor: pointer;
  transition: color 0.2s;
}
.demo-seg-btn.is-on {
  color: var(--vp-c-text-1);
}
.demo-read-hint {
  margin: 0;
  font-size: 13px;
  color: var(--vp-c-text-3);
}

.demo-read-body {
  min-height: 190px;
}
.demo-digest {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 18px;
  padding: 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background-color: var(--vp-c-bg);
}
.demo-digest-score {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}
.demo-digest-num {
  font-size: 30px;
  font-weight: 800;
  line-height: 1.1;
  color: var(--vp-c-brand-1);
}
.demo-digest-label {
  font-size: 12px;
  color: var(--vp-c-text-3);
}
.demo-digest-reason {
  margin-top: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background-color: var(--vp-c-default-soft);
  color: var(--vp-c-text-2);
  font-size: 11px;
  line-height: 1.6;
}
.demo-digest-points :deep(ul),
.demo-digest-points ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.demo-digest-points li {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--vp-c-text-2);
}
.demo-digest-tag {
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--vp-c-brand-1);
}

.demo-original {
  padding: 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background-color: var(--vp-c-bg);
}
.demo-original-source {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--vp-c-text-3);
}
.demo-original-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.45;
  color: var(--vp-c-text-1);
}
.demo-original-para {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--vp-c-text-2);
}
.demo-original-para:last-child {
  margin-bottom: 0;
}

.demo-read-foot {
  margin: 0;
  font-size: 12px;
  color: var(--vp-c-text-3);
}

.demo-fade-enter-active,
.demo-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.demo-fade-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.demo-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 640px) {
  .demo-digest {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
}
</style>
