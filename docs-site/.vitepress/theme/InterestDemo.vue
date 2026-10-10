<script setup lang="ts">
import { computed, ref } from 'vue'

// 首页交互小演示：点一个兴趣，示例早报跟着换。
// 卡片构成和「入选理由」的四种写法与 docs-site/features/brief.md 对齐。
interface DemoCard {
  source: string
  reason: string
  title: string
  summary: string
  score: number
  tags: string[]
}

const topics: { id: string; label: string; cards: DemoCard[] }[] = [
  {
    id: 'agent',
    label: 'AI 智能体',
    cards: [
      {
        source: '量子位',
        reason: '兴趣 · AI 智能体',
        title: '多智能体协作框架开源，把规划、执行、反思拆成独立角色',
        summary: '作者把长流程任务分给不同角色的智能体，并放出可复现的评测脚本，方便直接跑起来对比。',
        score: 7.6,
        tags: ['AI 智能体', '开源动态'],
      },
      {
        source: '机器之心',
        reason: '新闻价值入选',
        title: '智能体评测基准更新，新增需要连续调用工具的任务',
        summary: '新版本补充了必须串联多个工具才能完成的任务，用来区分「会聊天」和「真能干活」。',
        score: 7.2,
        tags: ['AI 智能体', '评测'],
      },
    ],
  },
  {
    id: 'coding',
    label: 'AI 编程',
    cards: [
      {
        source: 'InfoQ',
        reason: '兴趣 · AI 编程',
        title: '代码助手开始理解整个仓库，跨文件改动不再只看当前文件',
        summary: '新版本把检索范围扩到整个代码库，改动一处时会主动检查受影响的调用点。',
        score: 7.4,
        tags: ['AI 编程', '工程实践'],
      },
      {
        source: 'Hacker News',
        reason: '最新更新',
        title: '把代码评审交给模型之后，团队留下了哪些必须人工看的检查项',
        summary: '一支团队公开了他们保留人工评审的清单：涉及权限、并发和数据的改动一律不看模型结论。',
        score: 6.9,
        tags: ['AI 编程', '观点洞察'],
      },
    ],
  },
  {
    id: 'video',
    label: 'AI 视频生成',
    cards: [
      {
        source: '机器之心',
        reason: '兴趣 · AI 视频生成',
        title: '视频生成模型支持更长片段，镜头之间的衔接更连贯',
        summary: '新版本把单段时长拉长，并改善了转场处的连贯性，同一段提示词可以生成多镜头。',
        score: 7.3,
        tags: ['AI 视频生成', '模型发布'],
      },
      {
        source: '量子位',
        reason: '重大事件',
        title: '开源视频生成工具补上局部控制，改一个动作不用重跑整段',
        summary: '项目加入手势与轨迹控制，改动局部时只重算受影响的部分，生成成本明显下降。',
        score: 7.8,
        tags: ['AI 视频生成', '开源动态'],
      },
    ],
  },
  {
    id: 'safety',
    label: 'AI 对齐与安全',
    cards: [
      {
        source: 'AI 安全观察',
        reason: '兴趣 · AI 对齐与安全',
        title: '对齐研究给出新的评测方法，专门测模型在压力下的取舍',
        summary: '方法把「听话」和「安全」拆成两组指标，用来观察模型在冲突指令下会优先哪一边。',
        score: 7.1,
        tags: ['AI 对齐与安全', '学术论文'],
      },
      {
        source: '官方博客',
        reason: '重大事件 · 官方一手',
        title: '模型卡新增安全评估章节，公开红队测试的范围和局限',
        summary: '新章节写明了测试覆盖的场景、没覆盖的场景，以及已知的失效方式。',
        score: 7.5,
        tags: ['AI 对齐与安全', '模型发布'],
      },
    ],
  },
]

const picked = ref(topics[0].id)
const active = computed(() => topics.find(topic => topic.id === picked.value) ?? topics[0])
</script>

<template>
  <div class="demo">
    <div class="demo-head">
      <p class="demo-kicker">试一试</p>
      <p class="demo-title">点一个兴趣，看早报怎么变</p>
      <p class="demo-note">
        早报会优先挑命中兴趣的内容，哪怕它来自你没订阅的来源。下面是为了演示编的示例，不是真实资讯。
      </p>
    </div>

    <div class="demo-topics" role="group" aria-label="选择兴趣">
      <button
        v-for="topic in topics"
        :key="topic.id"
        type="button"
        class="demo-topic"
        :class="{ 'is-on': topic.id === picked }"
        :aria-pressed="topic.id === picked"
        @click="picked = topic.id"
      >
        {{ topic.label }}
      </button>
    </div>

    <TransitionGroup tag="ul" name="demo-card" class="demo-cards">
      <li v-for="card in active.cards" :key="card.title" class="demo-card">
        <div class="demo-card-top">
          <span class="demo-card-source">{{ card.source }}</span>
          <span class="demo-card-reason">{{ card.reason }}</span>
          <span class="demo-card-score">{{ card.score.toFixed(1) }}</span>
        </div>
        <p class="demo-card-title">{{ card.title }}</p>
        <p class="demo-card-summary">{{ card.summary }}</p>
        <div class="demo-card-tags">
          <span v-for="tag in card.tags" :key="tag">{{ tag }}</span>
        </div>
      </li>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.demo {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 22px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 16px;
  background-color: var(--vp-c-bg-soft);
}
.demo-kicker {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--vp-c-brand-1);
}
.demo-title {
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--vp-c-text-1);
}
.demo-note {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--vp-c-text-2);
}

.demo-topics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.demo-topic {
  padding: 7px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 999px;
  background-color: var(--vp-c-bg);
  color: var(--vp-c-text-2);
  font-size: 13px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: border-color 0.18s, color 0.18s, background-color 0.18s;
}
.demo-topic:hover {
  border-color: var(--vp-c-brand-2);
  color: var(--vp-c-text-1);
}
.demo-topic.is-on {
  border-color: var(--vp-c-brand-1);
  background-color: var(--vp-c-brand-soft);
  color: var(--vp-c-brand-1);
}

.demo-cards {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.demo-card {
  padding: 14px 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background-color: var(--vp-c-bg);
}
.demo-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
}
.demo-card-source {
  font-weight: 600;
  color: var(--vp-c-text-1);
}
.demo-card-reason {
  color: var(--vp-c-text-3);
}
.demo-card-score {
  margin-left: auto;
  font-size: 15px;
  font-weight: 700;
  color: var(--vp-c-brand-1);
}
.demo-card-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--vp-c-text-1);
}
.demo-card-summary {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--vp-c-text-2);
}
.demo-card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.demo-card-tags span {
  padding: 2px 8px;
  border-radius: 999px;
  background-color: var(--vp-c-default-soft);
  color: var(--vp-c-text-2);
  font-size: 11px;
  line-height: 1.6;
}

.demo-card-move {
  transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}
.demo-card-enter-active,
.demo-card-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}
.demo-card-enter-from {
  opacity: 0;
  transform: translateY(10px);
}
.demo-card-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
.demo-card-leave-active {
  position: absolute;
  right: 0;
  left: 0;
}
</style>
