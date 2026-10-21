import { useState } from 'react';
import { podcastBodyKey, podcastOf } from '../utils/podcast';
import PodcastTextPanel from './PodcastTextPanel';
import { Spark } from './player/PlayerGlyphs';

// 播客正文(issue #199 拍板 1:正文与音轨解耦)。正在播哪条音轨与这里无关——
// 有导读稿时正文区自带「节目简介 | ✦ 导读稿」切换,默认导读稿,本次会话记住读者的选择;
// 没有导读稿时就是节目简介。AI 速读卡由阅读窗常驻在上方,逐字稿等其它区块照旧在下方。
const VIEW_KEY = 'dorami-podcast-body-view';

function readView() {
  try {
    return window.sessionStorage.getItem(VIEW_KEY) === 'notes' ? 'notes' : 'guide';
  } catch {
    return 'guide';
  }
}

function writeView(view) {
  try {
    window.sessionStorage.setItem(VIEW_KEY, view);
  } catch {
    /* 存储不可用:本页内仍按读者的选择显示 */
  }
}

/**
 * children = 节目简介正文(阅读窗的正文容器,带翻译作用域);导读稿与逐字稿同一次请求取回。
 */
export default function PodcastExperiencePanel({ article, showTranslation = false, showNotesHead = true, children }) {
  const [view, setView] = useState(readView);
  const podcast = podcastOf(article);
  if (!podcast) return null;
  const hasGuide = Boolean(podcast.premium_guide?.blog_ready);
  const guideOn = hasGuide && view === 'guide';
  const pick = (next) => {
    setView(next);
    writeView(next);
  };

  return (
    <>
      {hasGuide ? (
        <div className="podcast-view-head">
          <div className="reader-tr-seg podcast-view-seg" role="group" aria-label="正文">
            <button
              type="button"
              aria-pressed={!guideOn}
              className={`reader-tr-seg-btn ${guideOn ? '' : 'is-on'}`}
              onClick={() => pick('notes')}
            >
              节目简介
            </button>
            <button
              type="button"
              aria-pressed={guideOn}
              className={`reader-tr-seg-btn ${guideOn ? 'is-on is-ai' : ''}`}
              onClick={() => pick('guide')}
            >
              <Spark />
              <span>导读稿</span>
            </button>
          </div>
          <span className="podcast-view-note">{guideOn ? '中文 · AI 整理' : '来源方提供'}</span>
        </div>
      ) : showNotesHead && (
        <div className="podcast-show-notes-head">
          <h2 className="section-title">节目简介</h2>
          <span>来源方提供</span>
        </div>
      )}
      {!guideOn && children}
      <div data-ai-translation-excluded="true">
        {/* 键只在导读稿就绪那一次翻转:停留本页时导读稿生成完成,面板重挂载取回新文字(否则正文区是空的) */}
        <PodcastTextPanel
          key={podcastBodyKey(article)}
          episodeId={article.id}
          showDigest={guideOn}
          digestMode={podcast.premium_guide?.mode}
          preferredTranscriptKind="publisher_transcript"
          showTranslation={showTranslation}
        />
      </div>
    </>
  );
}
