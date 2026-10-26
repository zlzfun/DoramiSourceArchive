import { X } from 'lucide-react';
import { READABLE_MAP } from './fixtures';
import { isActiveJob, prefixLength } from './translationSim';

function DemoSeg({ label, options, value, onChange, disabled = false }) {
  return (
    <div className="r2-demo-row">
      <span className="r2-demo-label">{label}</span>
      <div className="r2-demo-seg" role="group" aria-label={label}>
        {options.map(([optionValue, text]) => (
          <button
            key={String(optionValue)}
            type="button"
            aria-pressed={value === optionValue}
            disabled={disabled}
            onClick={() => onChange(optionValue)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

function jobStatusText(job) {
  if (!job) return '未开始';
  if (job.status === 'paused') {
    if (job.reason === 'quota') return '额度用尽，暂停';
    return job.reason === 'interrupted' ? '中断，暂停' : '已暂停（额度已恢复）';
  }
  return {
    queued: '排队中',
    running: '翻译中',
    failed: '失败',
    cancelled: '已取消',
    succeeded: job.live ? '已完成' : '已有完整译文',
  }[job.status] || job.status;
}

// 演示控制:挂在样页说明条下的非模态面板,只改本页的模拟数据,与产品界面分开(反色、独立区域)。
// 现网没有这些开关——它们模拟的是后台并发落片的先后、失败 / 额度 / 次日恢复、标题待办等外部条件;
// 「显示方式」是两种中文视图的对照(逐段占位 / 连续展示),供比较体验,不是产品设置。
// 「后台各片」一行只在这里可见:产品界面刻意不显示进度,评审靠它看清哪些片已完成、是否已显示。
export default function DemoPanel({ model: m, demo, onClose, toggleRef }) {
  const active = m.activeId ? READABLE_MAP[m.activeId] : null;
  const translatable = active?.body?.lang === 'en';
  const job = active ? m.jobs[active.id] : null;
  const activeJob = isActiveJob(job);
  const quotaPaused = job?.status === 'paused' && job.reason === 'quota';
  const shown = prefixLength(job);
  const titleState = active ? m.titleStates[active.id] : null;
  const titleTranslatable = Boolean(active?.title_zh);
  const close = () => {
    onClose();
    toggleRef.current?.focus();
  };

  return (
    <div
      id="r2-demo"
      className="r2-demo"
      role="region"
      aria-label="演示控制"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          close();
        }
      }}
    >
      <div className="r2-demo-head">
        <span className="r2-demo-heading">演示控制</span>
        <span className="r2-demo-note">只改本页模拟数据</span>
        <button type="button" className="r2-demo-close" onClick={close} aria-label="收起演示控制">
          <X aria-hidden="true" />
        </button>
      </div>

      <section className="r2-demo-sec" aria-label="正文分段翻译">
        <div className="r2-demo-title">正文分段翻译</div>
        <DemoSeg
          label="显示方式"
          value={demo.displayMode}
          onChange={demo.setDisplayMode}
          options={[['slots', '逐段占位'], ['prefix', '连续展示']]}
        />
        <DemoSeg label="落片节奏" value={demo.pacing} onChange={demo.setPacing} options={[['auto', '自动'], ['manual', '手动']]} />
        <DemoSeg
          label="完成顺序"
          value={demo.order}
          onChange={demo.setOrder}
          options={[['shuffled', '并发 3→1→2'], ['firstSlow', '首片最慢 2→3→1'], ['sequential', '顺序 1→2→3']]}
        />
        <DemoSeg label="开始前排队" value={demo.queueFirst} onChange={demo.setQueueFirst} options={[[false, '否'], [true, '是']]} />
        <div className="r2-demo-target">
          {translatable ? `当前：${m.displayFor(active).text}` : active ? '当前这篇是中文原文，无需翻译' : '先在阅读器里打开一篇外文内容'}
        </div>
        {translatable && (
          <div className="r2-demo-chunks">
            <span>后台任务：{jobStatusText(job)}</span>
            <span className="r2-demo-chunk-list">
              {active.body.segments.map((_, index) => {
                const done = Boolean(job?.done.includes(index));
                const hidden = demo.displayMode === 'prefix' && index >= shown;
                const state = !done ? '未完成' : hidden ? '已缓存，待接上' : '可显示';
                return <span key={index} className="r2-demo-chunk">第 {index + 1} 片 {state}</span>;
              })}
            </span>
          </div>
        )}
        <div className="r2-demo-actions">
          <button type="button" className="r2-demo-btn" disabled={!activeJob} onClick={() => m.actions.step(active.id)}>模拟：落下一片</button>
          <button type="button" className="r2-demo-btn" disabled={!activeJob} onClick={() => m.actions.failNext(active.id)}>模拟：下一片失败</button>
          <button type="button" className="r2-demo-btn" disabled={!activeJob} onClick={() => m.actions.pause(active.id, 'quota')}>模拟：额度用尽</button>
          <button type="button" className="r2-demo-btn" disabled={!quotaPaused} onClick={() => m.actions.restoreQuota(active.id)}>模拟：次日额度恢复</button>
          <button type="button" className="r2-demo-btn" disabled={!job} onClick={() => demo.resetJob(active.id)}>重置本篇任务</button>
        </div>
        <p className="r2-demo-tip">
          先在正文里点「译为中文」。后台按所选顺序并发落片。逐段占位：每片先显示按原文结构估算的骨架，哪片完成就原地换成中文；
          连续展示：只接上从第 1 片起连续完成的部分，后片先完成只缓存。两种方式共用同一个任务，切换不重置进度。
          额度用尽时产品不给立即继续，「次日额度恢复」才会出现「继续翻译」。
        </p>
      </section>

      <section className="r2-demo-sec" aria-label="标题译名">
        <div className="r2-demo-title">标题译名</div>
        <DemoSeg
          label="本篇译名"
          value={titleState}
          disabled={!titleTranslatable}
          onChange={(state) => demo.setTitleState(active.id, state)}
          options={[['ready', '已就绪'], ['pending', '未就绪']]}
        />
        <p className="r2-demo-tip">
          {titleTranslatable
            ? '切换即模拟下一次获取列表 / 详情的结果；页面不为标题单独轮询。'
            : '当前这篇的标题是中文或版本号，无需翻译。'}
          读者开关在「设置 → 阅读 → 默认显示中文标题」。
        </p>
      </section>

      <section className="r2-demo-sec" aria-label="页面">
        <div className="r2-demo-title">页面</div>
        {!m.compact && (
          <DemoSeg label="版式" value={demo.phoneFrame} onChange={demo.setPhoneFrame} options={[[false, '桌面'], [true, '手机框']]} />
        )}
        <DemoSeg label="主题" value={m.theme.pref} onChange={m.theme.setPref} options={[['light', '亮'], ['dark', '暗'], ['system', '跟随系统']]} />
        <DemoSeg label="减少动效" value={demo.motion.reduced} onChange={demo.motion.setReduced} options={[[false, '关'], [true, '开']]} />
        <div className="r2-demo-actions">
          <button type="button" className="r2-demo-btn" onClick={demo.reopenPage}>模拟重新打开本页</button>
          <button type="button" className="r2-demo-btn" onClick={demo.resetDemo}>重置演示</button>
        </div>
      </section>
    </div>
  );
}
