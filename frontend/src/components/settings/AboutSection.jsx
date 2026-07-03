import { SectionHeading, FieldRow } from './SectionPrimitives';

// 关于：产品名与账户角色。
export default function AboutSection({ accountRoleLabel, isAdmin }) {
  return (
    <div>
      <SectionHeading title="关于" />
      <div className="surface-card rounded-[var(--r-card)] px-4">
        <FieldRow label="产品">{isAdmin ? '哆啦美·归档中枢' : '哆啦美'}</FieldRow>
        <FieldRow label="账户角色">{accountRoleLabel}</FieldRow>
      </div>
    </div>
  );
}
