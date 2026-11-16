import { Fragment, useMemo, useRef } from 'react';
import { useTitleFit } from '../hooks/useTitleFit';
import { latinLang, punctParts } from '../utils/readerText';
import { titleUnits } from '../utils/titleFit';
import { needsGap, titleChunks } from '../utils/wordBreak';

/**
 * 标题里的文字(issue #279 第五批,桌面)。三件事,都只包标记、不改字——复制出来的文本、读屏读到的与原文一样:
 *   · 按词断行:标题切成不该从中间折开的块(utils/wordBreak),块之间放 <wbr>,外层 .ts-kw 让行只在块之间(与块里的空格处)折;
 *     每块再按空格切成片,一片一个 .ts-w(量宽度的单位)。
 *     汉字贴着字母数字的块边界补一小截(.ts-as)——<wbr> 会让浏览器的中西文自动间隙在那里失效。
 *   · 挑折行点:折在哪几块之间不交给浏览器的均衡折行(它只看行宽、不认停顿,会在逗号后面甩下一个词)——
 *     量好每块宽度后自己挑(hooks/useTitleFit + utils/titleFit):优先折在逗号、冒号这类停顿处,不把一两个词甩在行尾行首。
 *     挑好之后折点是 .ts-nl(伪元素里的换行,不进文本),整段不再自动折行(.is-fit),<wbr> 也不放了(它在 nowrap 里照样可折);
 *     挑不了就还是 <wbr>,由浏览器折。
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

function FittedTitle({ chunks, squeeze }) {
  const ref = useRef(null);
  const units = useMemo(() => titleUnits(chunks), [chunks]);
  const heads = useTitleFit(ref, units);
  return (
    <span ref={ref} className={heads ? 'ts-kw is-fit' : 'ts-kw'}>
      {units.map((unit, index) => {
        // 片尾的空格放在 .ts-w 外面:量宽度、行尾都不算它
        const body = unit.text.trimEnd();
        return (
          // key 用下标:片由同一段文本顺序切出,没有重排
          <Fragment key={index}>
            {index > 0 && needsGap(units[index - 1].text, unit.text) && <span className="ts-as" aria-hidden="true" />}
            {index > 0 && heads?.includes(index) && <span className="ts-nl" aria-hidden="true" />}
            {index > 0 && !heads && !unit.glued && <wbr />}
            <span className="ts-w">{squeeze ? squeezed(body, index === units.length - 1, index) : body}</span>
            {unit.text.slice(body.length)}
          </Fragment>
        );
      })}
    </span>
  );
}

export default function DisplayTitle({ text, squeeze = true }) {
  if (!text) return null;
  // 西文标题原样输出:它走西文字体,弯引号本来就窄,挤压反而会压到前后的字(样页的 T() 也是先放过西文)
  if (latinLang(text)) return text;
  const chunks = titleChunks(text);
  if (!chunks) return squeeze ? squeezed(text, true, 's') : text;
  return <FittedTitle chunks={chunks} squeeze={squeeze} />;
}
