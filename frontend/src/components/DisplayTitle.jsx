import { Fragment } from 'react';
import { latinLang, punctParts } from '../utils/readerText';
import { needsGap, titleChunks } from '../utils/wordBreak';

/**
 * 标题里的文字(issue #279 第五批,桌面)。两件事,都只包标记、不改字——复制出来的文本、读屏读到的与原文一样:
 *   · 按词断行:标题切成不该从中间折开的块(utils/wordBreak),块之间放 <wbr>,外层 .ts-kw 让行只在块之间折;
 *     汉字贴着字母数字的块边界补一小截(.ts-as)——<wbr> 会让浏览器的中西文自动间隙在那里失效。
 *   · 标点挤压(squeeze,只给 30px 以上的标题):全角标点收掉空的那半个字宽(.ts-pz / .ts-pzh)。
 * 西文标题、分不出块的标题原样输出。样式见 index.css「排印」一段。
 */
function squeezed(text, keepTail, keyBase) {
  const parts = punctParts(text, { keepTail });
  if (parts.length <= 1 && !parts[0]?.kind) return text;
  // key 用下标:片段由同一段文本顺序切出,没有重排
  return parts.map((part, index) => (
    part.kind
      ? <span key={`${keyBase}-${index}`} className={part.kind === 'close' ? 'ts-pz' : 'ts-pzh'}>{part.text}</span>
      : part.text
  ));
}

export default function DisplayTitle({ text, squeeze = true }) {
  if (!text) return null;
  // 西文标题原样输出:它走西文字体,弯引号本来就窄,挤压反而会压到前后的字(样页的 T() 也是先放过西文)
  if (latinLang(text)) return text;
  const chunks = titleChunks(text);
  if (!chunks) return squeeze ? squeezed(text, true, 's') : text;
  return (
    <span className="ts-kw">
      {chunks.map((chunk, index) => (
        // key 用下标:块由同一段文本顺序切出,没有重排
        <Fragment key={index}>
          {index > 0 && needsGap(chunks[index - 1], chunk) && <span className="ts-as" aria-hidden="true" />}
          {index > 0 && <wbr />}
          {squeeze ? squeezed(chunk, index === chunks.length - 1, index) : chunk}
        </Fragment>
      ))}
    </span>
  );
}
