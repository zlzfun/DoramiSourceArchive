import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import {
  podcastReaderBasisText,
  scoreDimensions,
  scoreDisclaimer,
  scoreTierClass,
} from '../utils/analysis';
import { splitSentences } from '../utils/readerText';
import { motionReduced } from '../motion';
import { pointerMotion, springTo } from '../utils/readerMotion';

/**
 * 哆啦美速读卡(issue #13 五轮;issue #184 改细线卡):外框色底 + 最弱细线,卡头渐变 ✦ 与分数是卡的身份。
 *
 *   ┌───────────────────────────────────────────────┐
 *   │ ✦ AI 速读                       新闻价值  8.2 │
 *   │ 一段客观摘要……                                 │
 *   └───────────────────────────────────────────────┘
 *
 * 卡头右侧一个宋体渐变数字(主分),入场以「里程表」方式滚到分值:每一位数字住在
 * 固定宽度的格子里,格子内 0–9 竖条用 transform 滑到目标——无逐帧重渲染、无布局变化。
 * 渐变落在每个数字/小数点元素自身:挂在外层时 background-clip:text 画不进
 * overflow:hidden 格子里的子元素,实测数字整个消失只剩小数点。
 *
 * 双评分(issue #178 第二波):数字左侧小字标出当前维度(新闻价值 / 内容价值)。有两个分时点数字
 * 在两个维度间切换,数字沿里程表滑到另一个值,AI 速读不变;换篇回到主分。悬停或键盘聚焦数字浮出
 * 该维度的评分依据(一句理由 + 免责小字),鼠标可以移进浮层停留;Escape 收起且不移动焦点,等悬停与焦点
 * 都离开、或有新的悬停 / 聚焦进来时复位(codex R1-6)。触屏没有悬停,依据改为卡内常显的一行小字。此前「点数字
 * 翻面看评分依据」的交互随之退役。正文区承接速读、骨架与生成入口。桌面与移动壳共用。
 *
 * 文章听导读与 AI 速读同卡呈现,音轨仍使用全局播放条(issue #238)。
 *
 * 桌面阅读窗(issue #279 第三批)传 page:速读一句一段;卡头并排写两个维度的分,点一下展开两句依据(不再靠悬停);
 * 标签住卡脚。阅读窗够宽时分数、依据、标签改排在正文右边的页边(ScoreMargin),卡里只剩速读——两种摆法由
 * 容器宽度在 CSS 里切换,两份标记都在。手机壳不传 page,仍是上面那张卡。
 *
 * 桌面的分数(issue #301 第二批):页边主分与卡头第一个维度用 ScoreRoll——换篇时从上一篇的分值逐位滚到这一篇(约 580ms),
 * 不再每次从 0 起滚 2.2 秒;手机壳的 ClassicCard 仍是原来的里程表(Odometer)。
 */
export default function AiReadingCard(props) {
  return props.page ? <PageCard {...props} /> : <ClassicCard {...props} />;
}

const WHY_KEY = 'dorami-reader-score-why';
const readWhy = () => { try { return window.localStorage.getItem(WHY_KEY) === '1'; } catch { return false; } };
const writeWhy = (open) => { try { window.localStorage.setItem(WHY_KEY, open ? '1' : ''); } catch { /* 隐私模式:本次会话内有效 */ } };

/** 两个维度各一句依据;免责句是悬停提示(原先在分数的悬停浮层里),播客另写一行评分基于什么。 */
function ScoreReasons({ dims, basis, id, className }) {
  return (
    <div className={className} id={id}>
      {dims.map((dim) => (
        <div key={dim.key} className="reader-ai-why-item">
          <div className="reader-ai-why-k" title={scoreDisclaimer(dim.key)}>{dim.label}</div>
          {dim.reason && <p>{dim.reason}</p>}
        </div>
      ))}
      {basis && <p className="reader-ai-why-basis">{basis}</p>}
    </div>
  );
}

function PageCard({
  article,
  summary,
  summarizing,
  canGenerate,
  onGenerate,
  podcast = false,
  listen = null,
  tags = null,
}) {
  const dims = scoreDimensions(article);
  const reason = (article?.score_reason || '').trim();
  const basis = podcast ? podcastReaderBasisText(article) : '';
  // 同步来的权威初评理论上包含摘要,但即使旧 / 异常数据只有分数理由,也不能把理由藏掉(同 ClassicCard)。
  const primaryText = summary || (podcast ? reason : '');
  const primaryTitle = !summary && podcast && reason ? '评分参考' : 'AI 速读';
  // 播客无摘要时理由已作正文,依据里不再重复
  const shownDims = podcast && !summary ? dims.map((dim) => ({ ...dim, reason: '' })) : dims;
  const hasWhy = shownDims.some((dim) => dim.reason) || Boolean(basis);
  const [why, setWhy] = useState(readWhy);
  const whyId = useId();
  const paragraphs = primaryText ? splitSentences(primaryText) : [];
  const bare = !primaryText && !summarizing && !canGenerate && !listen;
  return (
    <div className={`reader-ai-summary is-page ${bare ? 'is-bare' : ''}`}>
      <div className="reader-ai-card-head">
        <span className="reader-ai-layer-title">{primaryTitle}</span>
        <span className="reader-ai-grow" />
        {dims.length > 0 && (
          <button
            type="button"
            className="reader-ai-dims"
            aria-expanded={hasWhy ? why : undefined}
            aria-controls={hasWhy && why ? whyId : undefined}
            disabled={!hasWhy}
            onClick={() => { const next = !why; setWhy(next); writeWhy(next); }}
          >
            {dims.map((dim, index) => (
              <span key={dim.key} className="reader-ai-dim">
                <span className="reader-ai-dim-k">{dim.label}</span>
                {index === 0 ? (
                  <span className="reader-ai-dim-v">
                    <span className="sr-only">{dim.text}</span>
                    <ScoreRoll slot="head" inline score={dim.text} tierClass={scoreTierClass(dim.value)} />
                  </span>
                ) : (
                  <span className={`reader-ai-dim-v ai-grad-text ${scoreTierClass(dim.value)}`}>{dim.text}</span>
                )}
              </span>
            ))}
            {hasWhy && <ChevronDown aria-hidden="true" />}
          </button>
        )}
        {listen}
      </div>
      {why && hasWhy && <ScoreReasons dims={shownDims} basis={basis} id={whyId} className="reader-ai-why" />}
      <div className="reader-ai-summary-main">
        {paragraphs.length > 0 ? (
          <div className="reader-ai-layer reader-ai-layer-summary">
            {paragraphs.map((text, index) => <p key={index} className="reader-ai-layer-text">{text}</p>)}
          </div>
        ) : summarizing ? (
          <div className="reader-ai-summary-skel" role="status" aria-label="正在生成速读">
            <span className="skeleton" /><span className="skeleton" /><span className="skeleton" />
          </div>
        ) : canGenerate ? (
          <button type="button" onClick={onGenerate} className="reader-ai-summary-generate">
            {podcast ? '生成本期要点速读' : '生成本文要点速读'}
          </button>
        ) : null}
      </div>
      {tags && <div className="reader-ai-foot">{tags}</div>}
    </div>
  );
}

/**
 * 页边(issue #279 第三批):关于这篇的话——主分(44px,换篇时逐位滚动)与依据、另一个维度、标签。
 * 只在阅读窗够宽时出现(容器查询,见 index.css「阅读窗页边」),窄时这些内容在速读卡的卡头与卡脚。
 */
export function ScoreMargin({ article, podcast = false, tags = null }) {
  const dims = scoreDimensions(article);
  const [first, second] = dims;
  const basis = podcast ? podcastReaderBasisText(article) : '';
  if (!first && !tags) return null;
  return (
    <aside className="reader-pane-side" aria-label="评分与标签">
      {first && (
        <div className="reader-side-blk">
          <div className="reader-side-k" title={scoreDisclaimer(first.key)}>{first.label}</div>
          <div className="reader-side-score" role="img" aria-label={`${first.label} ${first.text}`}>
            <ScoreRoll slot="side" score={first.text} tierClass={scoreTierClass(first.value)} />
          </div>
          {first.reason && <p>{first.reason}</p>}
          {basis && <p className="reader-ai-why-basis">{basis}</p>}
        </div>
      )}
      {second && (
        <div className="reader-side-blk is-minor">
          <div className="reader-side-k" title={scoreDisclaimer(second.key)}>
            {second.label}
            <span className={`reader-side-minor ai-grad-text ${scoreTierClass(second.value)}`}>{second.text}</span>
          </div>
          {second.reason && <p>{second.reason}</p>}
        </div>
      )}
      {tags && <div className="reader-side-blk">{tags}</div>}
    </aside>
  );
}

function ClassicCard({
  article,
  summary,
  summarizing,
  canGenerate,
  onGenerate,
  podcast = false,
  listen = null,
}) {
  const podcastBasis = podcast ? podcastReaderBasisText(article) : '';
  const dims = scoreDimensions(article);
  const [dimKey, setDimKey] = useState(null);   // null = 主分
  const current = dims.find((d) => d.key === dimKey) || dims[0] || null;
  const other = dims.length > 1 ? dims.find((d) => d.key !== current.key) : null;
  const score = current?.text || '';
  const scoreTier = scoreTierClass(current?.value);   // issue #54:档位挂在每位数字自身(渐变落在元素自身)
  const reason = (article?.score_reason || '').trim();
  const tipId = useId();
  const sideRef = useRef(null);
  const [tipDismissed, setTipDismissed] = useState(false);
  const tipTitle = `评分依据 · ${current?.label || ''}`;
  const tipReason = current?.reason || '';
  const tipNote = podcast ? podcastBasis : scoreDisclaimer(current?.key);

  useEffect(() => { setDimKey(null); setTipDismissed(false); }, [article?.id]);
  useEffect(() => {
    // Escape 收起评分依据:只在数字区处于悬停或焦点在其中时生效,不移动焦点。
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      const side = sideRef.current;
      if (side && (side.contains(document.activeElement) || side.matches(':hover'))) setTipDismissed(true);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // 同步来的权威初评理论上包含摘要，但即使旧/异常数据只有分数理由，也不能把理由藏掉。
  const primaryText = summary || (podcast ? reason : '');
  const primaryTitle = !summary && podcast && reason ? '评分参考' : 'AI 速读';
  // 播客无摘要时理由已作正文,不再重复成依据行。
  const inlineReason = tipReason && !(podcast && !summary) ? tipReason : '';
  return (
    <div className="reader-ai-summary">
      {/* 卡头恒在常流里(codex R1-P2-03):左「✦ 标题」,右统计位「维度 分数」——只有分数没有正文时卡也撑得住 */}
      <div className="reader-ai-card-head">
        <span className="reader-ai-layer-title">{primaryTitle}</span>
        {score && (
          <div
            ref={sideRef}
            className={`reader-ai-summary-side ${tipDismissed ? 'is-tip-dismissed' : ''}`}
            onMouseEnter={() => setTipDismissed(false)}
            onFocus={() => setTipDismissed(false)}
            onMouseLeave={(event) => {
              if (!event.currentTarget.contains(document.activeElement)) setTipDismissed(false);
            }}
            onBlur={(event) => {
              const side = event.currentTarget;
              if (!side.contains(event.relatedTarget) && !side.matches(':hover')) setTipDismissed(false);
            }}
          >
            <ScoreFigure
              key={article.id}
              score={score}
              tierClass={scoreTier}
              interactive={Boolean(other)}
              label={other ? `${current.label} ${score}，点按切换到${other.label}` : `${current.label} ${score}`}
              describedBy={tipReason || tipNote ? tipId : undefined}
              onToggle={() => { if (other) setDimKey(other.key); }}
            />
            <span className="reader-ai-score-dim" aria-hidden="true">{current.label}</span>
            {(tipReason || tipNote) && (
              <div className="reader-ai-score-tip" role="tooltip" id={tipId}>
                <span className="reader-ai-layer-title">{tipTitle}</span>
                {tipReason && <p className="reader-ai-layer-text">{tipReason}</p>}
                <span className="reader-ai-layer-note">{tipNote}</span>
              </div>
            )}
          </div>
        )}
      </div>
      {listen && <div className="reader-ai-listen">{listen}</div>}
      <div className="reader-ai-summary-main">
        {primaryText ? (
          <div className="reader-ai-layer reader-ai-layer-summary">
            <p className="reader-ai-layer-text">{primaryText}</p>
          </div>
        ) : summarizing ? (
          <div className="reader-ai-summary-skel" role="status" aria-label="正在生成速读">
            <span className="skeleton" /><span className="skeleton" /><span className="skeleton" />
          </div>
        ) : canGenerate ? (
          <button type="button" onClick={onGenerate} className="reader-ai-summary-generate">
            {podcast ? '生成本期要点速读' : '生成本文要点速读'}
          </button>
        ) : null}
        {score && (inlineReason || podcastBasis) && (
          <p className="reader-ai-reason-inline">
            <span className="reader-ai-reason-inline-label">{current.label}依据</span>
            {podcastBasis && <>{podcastBasis}{inlineReason ? '。' : ''}</>}
            {inlineReason}
          </p>
        )}
      </div>
    </div>
  );
}

const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];


/** 衬线渐变大数字:里程表式入场(每位数字在定宽格内滑到位),有两个分时点按切换维度(数字滑到另一个值)。 */
function ScoreFigure({ score, tierClass = '', interactive, label, describedBy, onToggle }) {
  return (
    <button
      type="button"
      className={`reader-ai-score-btn ${interactive ? 'is-interactive' : ''}`}
      aria-label={label}
      aria-describedby={describedBy}
      onClick={() => { if (interactive) onToggle?.(); }}
    >
      <Odometer score={score} tierClass={tierClass} />
    </button>
  );
}

// 桌面阅读窗的分数(issue #301 第二批;conventions §7 具名例外「AI 速读卡分数」的桌面写法):
// 换篇时从上一篇的分值逐位滚到这一篇——右边一位先动、往左每位晚 60ms,数字没变的那一位不动;第一次出现时从 0 起。
// 弹簧用 move 档(落定约 580ms)。键盘换篇、减少动效直落终值。结构沿用里程表的格子与竖条,过渡交给 WAAPI。
// 上一次显示的分值按位置(页边 side / 卡头 head)记在模块里:阅读窗按文章 id 重挂载,组件自己留不住。
const lastRolled = new Map();
const ROLL_STAGGER = 60;

function ScoreRoll({ score, tierClass = '', slot, inline = false }) {
  const ref = useRef(null);
  const text = String(score);
  useLayoutEffect(() => {
    const from = lastRolled.get(slot);
    lastRolled.set(slot, text);
    const root = ref.current;
    if (!root || from === text || pointerMotion() !== 'full') return;
    const old = String(from ?? '').replace(/\D/g, '').split('').reverse();
    [...root.querySelectorAll('.reader-ai-odo-strip')].reverse().forEach((strip, j) => {
      const to = Number(strip.dataset.d);
      const start = Number(old[j] || 0);
      if (start !== to) {
        springTo(strip, [{ transform: `translateY(${-start * 10}%)` }, { transform: `translateY(${-to * 10}%)` }], 'move', { delay: j * ROLL_STAGGER });
      }
    });
  }, [slot, text]);
  return (
    <span ref={ref} className={`reader-ai-odo is-roll ${inline ? 'is-inline' : ''}`} aria-hidden="true">
      {text.split('').map((ch, i) => {
        if (ch === '.') return <span key={i} className={`reader-ai-odo-dot ai-grad-text ${tierClass}`}>.</span>;
        const digit = Number(ch);
        if (!Number.isFinite(digit)) return <span key={i}>{ch}</span>;
        return (
          <span key={i} className="reader-ai-odo-cell">
            <span className="reader-ai-odo-strip" data-d={digit} style={{ transform: `translateY(${-digit * 10}%)` }}>
              {DIGITS.map((d) => <span key={d} className={`reader-ai-odo-digit ai-grad-text ${tierClass}`}>{d}</span>)}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** 里程表数字本体(不带按钮):手机壳速读卡的分数钮在用(桌面改用上面的 ScoreRoll)。 */
function Odometer({ score, tierClass = '' }) {
  const reduced = motionReduced();
  // 先以 0 挂载,下一帧再落到目标值,让 CSS transition 接管滑动;减少动画时直落。
  const [armed, setArmed] = useState(reduced);
  useEffect(() => {
    if (reduced) return undefined;
    let id = requestAnimationFrame(() => { id = requestAnimationFrame(() => setArmed(true)); });
    return () => cancelAnimationFrame(id);
  }, [reduced]);

  const chars = String(score);
  return (
      <span className="reader-ai-odo" aria-hidden="true">
        {chars.split('').map((ch, i) => {
          if (ch === '.') return <span key={i} className={`reader-ai-odo-dot ai-grad-text ${tierClass}`}>.</span>;
          const digit = Number(ch);
          if (!Number.isFinite(digit)) return <span key={i}>{ch}</span>;
          return (
            <span key={i} className="reader-ai-odo-cell">
              <span
                className="reader-ai-odo-strip"
                style={{ transform: `translateY(${-(armed ? digit : 0) * 10}%)` }}
              >
                {DIGITS.map((d) => <span key={d} className={`reader-ai-odo-digit ai-grad-text ${tierClass}`}>{d}</span>)}
              </span>
            </span>
          );
        })}
      </span>
  );
}
