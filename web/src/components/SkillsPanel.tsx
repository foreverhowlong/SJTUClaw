import type { SkillSummary, SkillUsage } from "../types";

interface Props {
  skills: SkillSummary[];
  usages: SkillUsage[];
  selectedSkillName: string | null;
  onSelect: (name: string) => void;
}

export function SkillsPanel({ skills, usages, selectedSkillName, onSelect }: Props) {
  return (
    <div className="skills-panel">
      <header className="skills-intro">
        <div className="skills-section-heading">
          <span className="micro-label">AVAILABLE SKILLS</span>
          <span>{skills.length} READY</span>
        </div>
        <p>选择后，下一条消息会显式使用该 Skill；普通聊天仍可自动选择。</p>
      </header>

      <div className="skill-list">
        {skills.map((skill, index) => {
          const selected = selectedSkillName === skill.name;
          return (
            <article
              className={`skill-card${selected ? " is-selected" : ""}`}
              key={skill.name}
            >
              <header className="skill-card-header">
                <div className="skill-card-title">
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{skill.name}</strong>
                </div>
                <span className="skill-origin">{skill.origin.toUpperCase()}</span>
              </header>
              <p>{skill.description}</p>
              <button
                type="button"
                aria-pressed={selected}
                onClick={() => onSelect(skill.name)}
              >
                <span>{selected ? "SELECTED FOR NEXT" : "USE NEXT"}</span>
                <span aria-hidden="true">{selected ? "✓" : "↗"}</span>
              </button>
            </article>
          );
        })}
      </div>

      <section className="skill-usage-section">
        <div className="skills-section-heading">
          <span className="micro-label">SESSION USAGE</span>
          <span>{usages.length} RECORDED</span>
        </div>
        {usages.length === 0 && <p className="muted-copy">尚未使用 Skill。</p>}
        <div className="skill-usage-list">
          {usages.map((usage) => (
            <article className="skill-usage-card" key={usage.usageId}>
              <div className="skill-usage-topline">
                <strong>{usage.skillName}</strong>
                <span className={`skill-outcome skill-outcome-${usage.outcome}`}>
                  {usage.outcome.toUpperCase()}
                </span>
              </div>
              <div className="skill-usage-meta">
                <span>{usage.source.toUpperCase()}</span>
                <span>{formatUsageTime(usage.usedAt)}</span>
              </div>
              <p>{usage.reason}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function formatUsageTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "TIME UNKNOWN";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
