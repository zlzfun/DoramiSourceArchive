<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vitepress'

// 首页滚动动效：首屏景深 + 区块按内容出场。
// 只在客户端挂载后执行；无 JS、动效库加载失败或系统开启「减少动态效果」时，
// 页面照常按静态内容可读可点。
const route = useRoute()
let dispose: (() => void) | null = null
let seq = 0

// 八个功能行轮转四种出场方式，避免整页都是同一种淡入上移
const ROW_KINDS = ['paper', 'reveal', 'drop', 'expand']

async function setup() {
  const token = ++seq
  dispose?.()
  dispose = null
  if (route.path !== '/') return

  await nextTick()
  if (token !== seq) return

  let gsap: typeof import('gsap').gsap
  let ScrollTrigger: typeof import('gsap/ScrollTrigger').default
  try {
    const mods = await Promise.all([import('gsap'), import('gsap/ScrollTrigger')])
    gsap = mods[0].gsap
    ScrollTrigger = mods[1].default
  } catch {
    // 动效库没取到就保持静态，内容照常可读
    return
  }
  if (token !== seq) return
  gsap.registerPlugin(ScrollTrigger)

  const mm = gsap.matchMedia()
  mm.add('(prefers-reduced-motion: no-preference)', () => {
    const hero = document.querySelector('.VPHero')
    const heading = hero?.querySelector('.heading')
    const shot = hero?.querySelector('.hero-shot')
    const glow = document.querySelector('.hero-glow')

    // 首屏景深：滚动直接驱动，产品画面缩进去、大标题反向放大后退，往回滚倒放
    if (hero && heading && shot) {
      const tl = gsap.timeline({
        scrollTrigger: { trigger: hero, start: 'top top', end: '+=85%', scrub: 0.5 },
      })
      tl.fromTo(shot, { scale: 1, y: 0 }, { scale: 0.83, y: -34, ease: 'none' }, 0)
      tl.fromTo(heading, { scale: 1, y: 0, opacity: 1 }, { scale: 1.09, y: 28, opacity: 0.32, ease: 'none' }, 0)
      if (glow) tl.fromTo(glow, { y: 0 }, { y: -110, ease: 'none' }, 0)
    }

    // 首屏以下的每个功能行，滚到位置出场一次，往回滚不重播
    const rows = gsap.utils.toArray('.home-rows .fr') as HTMLElement[]
    rows.forEach((row, index) => {
      const text = row.querySelector('.fr-text')
      const media = row.querySelector('.fr-media')
      if (!text || !media) return
      const reverse = row.classList.contains('fr-reverse')
      const kind = ROW_KINDS[index % ROW_KINDS.length]

      const tl = gsap.timeline({
        scrollTrigger: { trigger: row, start: 'top 82%', once: true },
      })
      // 文字先到，画面随后被带出，间隔 60ms；整段 540ms
      tl.from(text, { y: 24, opacity: 0, duration: 0.36, ease: 'power2.out' }, 0)

      const from: Record<string, unknown> = { opacity: 0, duration: 0.48, ease: 'power2.out' }
      if (kind === 'paper') {
        // 纸、标签：先快后慢，带一点旋转后摆正
        Object.assign(from, { y: 30, scale: 1.04, rotate: reverse ? 0.7 : -0.7 })
      } else if (kind === 'reveal') {
        // 图片：遮罩从画面主体的方向揭开
        Object.assign(from, { clipPath: reverse ? 'inset(0 0 0 100%)' : 'inset(0 100% 0 0)' })
      } else if (kind === 'drop') {
        // 卡片：带重量落下，过冲后稳住
        Object.assign(from, { y: -32, ease: 'back.out(1.3)' })
      } else {
        // 线条、流程：沿阅读方向展开
        Object.assign(from, {
          scaleX: 0.9,
          transformOrigin: reverse ? 'right center' : 'left center',
        })
      }
      tl.from(media, from, 0.06)
    })

    // 页尾入口：一次淡入上移
    const start = document.querySelector('.home-start')
    if (start) {
      gsap.from(start, {
        y: 28,
        opacity: 0,
        duration: 0.5,
        ease: 'power2.out',
        scrollTrigger: { trigger: start, start: 'top 88%', once: true },
      })
    }
  })

  dispose = () => mm.revert()
}

onMounted(setup)
watch(() => route.path, setup)
onUnmounted(() => {
  seq++
  dispose?.()
  dispose = null
})
</script>

<template>
  <span class="home-motion" hidden />
</template>
