<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vitepress'
import briefShot from '../../../docs/assets/readme/01-brief.png'
import readerShot from '../../../docs/assets/readme/02-reader.png'
import askShot from '../../../docs/assets/readme/04-ask.png'

// 首页中段的滑动叙事：一块固定的舞台，随滚动依次交代三个关键时刻。
// 没有 JS 或系统开启「减少动态效果」时，三步按顺序正常排布，全部可读。
const steps = [
  {
    kicker: '早上第一件事',
    title: '先看编排好的早报',
    text: '打开就是今天这一版：按板块排好，重要的占大版面，每张卡片写明它为什么入选。',
    shot: briefShot,
    alt: '我的早报：按板块排版的卡片，每张卡片右上角是新闻价值分',
  },
  {
    kicker: '读到感兴趣的一条',
    title: '一个阅读器读完所有来源',
    text: '订阅都在左栏，正文上方是先给结论的 AI 速读。未读、收藏、搜索、分享都在手边。',
    shot: readerShot,
    alt: '阅读器正文：标题下方是 AI 速读卡片和新闻价值分',
  },
  {
    kicker: '还想再问一句',
    title: '就地问哆啦美',
    text: '就当前这篇文章或你的全部订阅提问，回答里的编号点一下就打开原文；外文一键译成中文。',
    shot: askShot,
    alt: '问答面板：回答带编号引用，下方列出引用的文章',
  },
]

const active = ref(0)
const route = useRoute()
let dispose: (() => void) | null = null
let seq = 0

async function setup() {
  const token = ++seq
  dispose?.()
  dispose = null
  active.value = 0
  if (route.path !== '/') return

  const root = document.querySelector('.walk') as HTMLElement | null
  if (!root) return
  const shots = Array.from(root.querySelectorAll('.walk-shot')) as HTMLElement[]
  if (shots.length < 2) return

  // 先按「能不能动」决定版式，再异步取动效库；避免取到库之后再跳一次版式
  const canMove = window.matchMedia('(prefers-reduced-motion: no-preference)').matches
  if (!canMove) return
  root.classList.add('walk-live')

  let gsap: typeof import('gsap').gsap
  let ScrollTrigger: typeof import('gsap/ScrollTrigger').default
  try {
    const mods = await Promise.all([import('gsap'), import('gsap/ScrollTrigger')])
    gsap = mods[0].gsap
    ScrollTrigger = mods[1].default
  } catch {
    // 动效库没取到就退回静态版式，内容照常可读
    root.classList.remove('walk-live')
    return
  }
  if (token !== seq) {
    root.classList.remove('walk-live')
    return
  }
  gsap.registerPlugin(ScrollTrigger)

  const mm = gsap.matchMedia()
  mm.add('(prefers-reduced-motion: no-preference)', () => {
    const tl = gsap.timeline({
      scrollTrigger: {
        trigger: root,
        start: 'top top',
        end: 'bottom bottom',
        scrub: true,
        onUpdate: (self) => {
          const n = steps.length
          active.value = Math.min(n - 1, Math.max(0, Math.round(self.progress * (n - 1))))
        },
      },
    })

    // 后一张在前一张之上快速淡入，把上一张盖住；不做两张图叠在一起的交叉淡化。
    // 起点与文案切换点（每段的 50%）对齐，右侧画面和左侧高亮同时换。
    shots.forEach((shot, index) => {
      if (index === 0) return
      tl.fromTo(
        shot,
        { opacity: 0, scale: 1.02 },
        { opacity: 1, scale: 1, duration: 0.3, ease: 'power1.inOut' },
        index - 1 + 0.5,
      )
    })
  })

  dispose = () => {
    mm.revert()
    root.classList.remove('walk-live')
  }
}

onMounted(setup)
onUnmounted(() => {
  seq++
  dispose?.()
  dispose = null
})
</script>

<template>
  <section class="walk" aria-label="哆啦美的三个使用时刻">
    <div class="walk-stage">
      <div class="walk-copy">
        <p class="walk-kicker">{{ steps[active].kicker }}</p>
        <ol class="walk-list">
          <li
            v-for="(step, index) in steps"
            :key="step.title"
            class="walk-item"
            :class="{ 'is-on': index === active }"
          >
            <h3 class="walk-title">{{ step.title }}</h3>
            <p class="walk-text">{{ step.text }}</p>
          </li>
        </ol>
        <div class="walk-dots" aria-hidden="true">
          <span
            v-for="(step, index) in steps"
            :key="step.title"
            class="walk-dot"
            :class="{ 'is-on': index === active }"
          />
        </div>
      </div>

      <div class="walk-shots">
        <div v-for="step in steps" :key="step.title" class="walk-shot">
          <img :src="step.shot" :alt="step.alt">
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 静态兜底：三步依次排开，图片各自成段 */
.walk {
  margin: 8px 0 40px;
}
.walk-stage {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.walk-copy {
  max-width: 520px;
}
.walk-kicker {
  margin: 0 0 10px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--vp-c-brand-1);
}
.walk-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.walk-item + .walk-item {
  margin-top: 20px;
}
.walk-title {
  margin: 0 0 6px;
  font-size: 22px;
  line-height: 1.35;
  letter-spacing: -0.01em;
}
.walk-text {
  margin: 0;
  font-size: 15px;
  line-height: 1.75;
  color: var(--vp-c-text-2);
}
.walk-dots {
  display: none;
}
.walk-shots {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.walk-shot img {
  display: block;
  width: 100%;
  margin: 0;
  border-radius: 14px;
  background: var(--vp-c-bg);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 12px 32px rgba(15, 23, 42, 0.1);
}
.dark .walk-shot img {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06), 0 12px 32px rgba(0, 0, 0, 0.45);
}

/* 动效可用时：舞台吸顶，图片叠在一起由滚动换页 */
.walk-live {
  height: 240vh;
}
.walk-live .walk-stage {
  position: sticky;
  top: calc(var(--vp-nav-height) + 24px);
  display: grid;
  grid-template-columns: minmax(0, 4fr) minmax(0, 7fr);
  gap: 56px;
  align-items: center;
  min-height: calc(100vh - var(--vp-nav-height) - 48px);
}
.walk-live .walk-copy {
  max-width: none;
}
.walk-live .walk-item {
  transition: opacity 0.4s ease;
}
.walk-live .walk-item + .walk-item {
  margin-top: 26px;
}
.walk-live .walk-item:not(.is-on) {
  opacity: 0.34;
}
.walk-live .walk-title {
  font-size: 20px;
}
.walk-live .walk-dots {
  display: flex;
  gap: 6px;
  margin-top: 22px;
}
.walk-dot {
  width: 22px;
  height: 3px;
  border-radius: 999px;
  background: var(--vp-c-divider);
  transition: background-color 0.3s ease, width 0.3s ease;
}
.walk-dot.is-on {
  width: 34px;
  background: var(--vp-c-brand-1);
}
.walk-live .walk-shots {
  position: relative;
  aspect-ratio: 1.6;
  border-radius: 14px;
  background: var(--vp-c-bg);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 18px 44px rgba(15, 23, 42, 0.12);
}
.dark .walk-live .walk-shots {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06), 0 18px 44px rgba(0, 0, 0, 0.45);
}
.walk-live .walk-shot {
  position: absolute;
  inset: 0;
  overflow: hidden;
  border-radius: 14px;
}
.walk-live .walk-shot:not(:first-child) {
  opacity: 0;
}
.walk-live .walk-shot img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top left;
  border-radius: 0;
  box-shadow: none;
}

@media (max-width: 768px) {
  .walk-live {
    height: auto;
  }
  .walk-live .walk-stage {
    position: static;
    display: flex;
    flex-direction: column;
    gap: 24px;
    min-height: 0;
  }
  .walk-live .walk-item:not(.is-on) {
    opacity: 1;
  }
  .walk-live .walk-shots {
    position: static;
    aspect-ratio: auto;
  }
  .walk-live .walk-shot {
    position: static;
  }
  .walk-live .walk-shot:not(:first-child) {
    opacity: 1;
  }
  .walk-live .walk-dots {
    display: none;
  }
}
</style>
