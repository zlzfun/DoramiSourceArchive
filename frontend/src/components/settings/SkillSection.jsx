import { Download } from 'lucide-react';

// SKILL.md 已是跨家开放标准(Agent Skills):Claude Code/Codex/OpenCode/Cursor/Gemini CLI 等均支持。
const SKILL_STEPS = [
  <>下载技能包 <code>dorami-daily-brief.zip</code></>,
  <>解压到工具的 skills 目录（如 Claude Code：<code>~/.claude/skills/</code>）</>,
  <>对 Agent 说「看看今天的 AI 日报」即可对话取用</>,
];

/**
 * Agent 技能包（设置柜·接入集成组）：模板化日报技能的下载与安装指引。
 * 前身是接入集成页签的技能包卡（并入设置波）。
 */
export default function SkillSection() {
  const handleDownloadSkill = () => {
    const a = document.createElement('a');
    a.href = '/api/skill/daily-brief';
    a.download = 'dorami-daily-brief.zip';
    a.click();
  };

  return (
    <div>
      <div className="card-head">
        <span className="sett-lbl">每日 AI 资讯技能</span>
        <span className="target-chip">SKILL.md 开放标准</span>
      </div>
      <p className="card-desc">按本站地址模板化的日报技能，装进 Claude Code、Codex、OpenCode 等支持 Agent Skills 的工具即可对话取用每日资讯。</p>
      <div className="steps">
        {SKILL_STEPS.map((step, i) => <div key={i} className="step">{step}</div>)}
      </div>
      <div className="mt-4">
        <button type="button" onClick={handleDownloadSkill} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
          <Download className="w-3.5 h-3.5" />
          下载技能包
        </button>
      </div>
    </div>
  );
}
