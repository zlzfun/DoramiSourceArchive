import { useState } from 'react';
import { Image } from '@tarojs/components';
import { API_BASE_URL } from '../config';

/**
 * 图床代理图(源头像/推文图):先走 /api/media/proxy(Image 组件带不了鉴权头,
 * 对读者门控端点会 401 → onError),失败回退原链直连;再失败留占位底色。
 * 正文内图片不走这里——render 端点已把它们改写成签名公开链。
 */
export default function ProxyImage({ src, className, mode = 'aspectFill', onClick, style }) {
  const [stage, setStage] = useState(0); // 0=proxy 1=direct 2=failed
  if (!src || stage === 2) return <Image className={className} style={style} src="" mode={mode} onClick={onClick} />;
  const url = stage === 0 ? `${API_BASE_URL}/media/proxy?url=${encodeURIComponent(src)}` : src;
  return (
    <Image
      className={className}
      style={style}
      src={url}
      mode={mode}
      lazyLoad
      onClick={onClick}
      onError={() => setStage((s) => s + 1)}
    />
  );
}
