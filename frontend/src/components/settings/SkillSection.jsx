import { useState } from 'react';
import { Check, Copy, Download } from 'lucide-react';
import { copyText } from '../../utils/clipboard';
import { runAction } from '../../utils/runAction';

// SKILL.md 已是跨家开放标准(Agent Skills):Claude Code/Codex/OpenCode/Cursor/Gemini CLI 等均支持。
// 各家的 skills 目录不同,这里只列查证过的一处;其余以各工具文档为准。
const SKILL_DIRS = [['Claude Code', '~/.claude/skills/']];

/**
 * Agent 技能包（设置柜·接入集成组）：模板化日报技能的下载,和解压到哪儿。
 * 前身是接入集成页签的技能包卡（并入设置波）。
 * issue #291 起不写安装说明:一枚下载钮 + 一行「解压到 <目录>」(原先是一段介绍 + 三步文字,「下载技能包」在一屏里出现两次)。
 */
export default function SkillSection({ showToast }) {
  const [copiedKey, setCopiedKey] = useState('');

  const handleDownloadSkill = () => {
    const a = document.createElement('a');
    a.href = '/api/skill/daily-brief';
    a.download = 'dorami-daily-brief.zip';
    a.click();
  };

  const copy = (text, key) => runAction(() => copyText(text), {
    showToast,
    success: '已复制 skills 目录',
    error: '复制失败，请手动选择文本复制',
    onSuccess: () => { setCopiedKey(key); setTimeout(() => setCopiedKey(''), 1600); },
  });

  return (
    <div>
      <div className="sett-row">
        <span className="sett-id"><span className="sett-lbl">每日 AI 资讯技能</span></span>
        <span className="sett-ctl">
          <button type="button" onClick={handleDownloadSkill} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
            <Download className="w-3.5 h-3.5" />
            下载技能包
          </button>
        </span>
      </div>
      {SKILL_DIRS.map(([tool, dir]) => (
        <div key={tool} className="endpoint mt-3">
          <span className="endpoint-method">解压到</span>
          <span className="endpoint-url" title={dir}>{dir}</span>
          <span className="target-chip">{tool}</span>
          <button type="button" className="copybtn" title="复制目录" onClick={() => copy(dir, tool)}>
            {copiedKey === tool ? <Check /> : <Copy />}
          </button>
        </div>
      ))}
    </div>
  );
}
