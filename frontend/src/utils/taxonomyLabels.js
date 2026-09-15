// 标签治理的枚举 → 中文 label + tone 单点(issue #76,归并稿 P2 #12):
// 表格章、列头筛选档、抽屉详情共用;未知值不裸出英文,退化为「未知状态(raw)」。

export const TAG_KIND_LABELS = Object.freeze({ topic: '主题', industry: '行业', entity: '实体' });

export const TAG_KIND_FILTERS = Object.freeze([
  ['', '分面'], ['topic', '主题'], ['industry', '行业'], ['entity', '实体'],
]);

export const TAG_STATUS_META = Object.freeze({
  active: { label: '启用', tone: 'ok' },
  draft: { label: '草稿', tone: 'idle' },
  deprecated: { label: '已废弃', tone: 'idle' },
  merged: { label: '已合并', tone: 'idle' },
});

export const CANDIDATE_STATUS_META = Object.freeze({
  candidate: { label: '候选', tone: 'idle' },
  reviewing: { label: '审核中', tone: 'warn' },
  rejected: { label: '已拒绝', tone: 'bad' },
  merged: { label: '已归并', tone: 'idle' },
  activated: { label: '已激活', tone: 'ok' },
});

// 统一表的「状态」列头轮换档:候选四态在前、标签两态在后(merged 两边同词,归并到一档)。
export const LEDGER_STATUS_FILTERS = Object.freeze([
  ['', '状态'],
  ['candidate', '候选'],
  ['reviewing', '审核中'],
  ['rejected', '已拒绝'],
  ['active', '启用'],
  ['deprecated', '已废弃'],
  ['merged', '已归并'],
]);

export const LEDGER_TYPE_FILTERS = Object.freeze([
  ['', '类型'], ['candidate', '候选'], ['tag', '规范标签'],
]);

export const ALIAS_TYPE_LABELS = Object.freeze({
  synonym: '同义词',
  abbreviation: '缩写',
  translation: '翻译',
  former_name: '旧称',
  misspelling: '常见误写',
});

export const ENTITY_TYPE_LABELS = Object.freeze({
  organization: '组织 / 公司 / 实验室',
  product: '产品 / 服务',
  model: '模型 / 模型家族',
  protocol: '协议 / 标准',
  project: '开源项目 / 框架',
});

export function tagKindLabel(kind) {
  return TAG_KIND_LABELS[kind] || `未知分面(${kind || '—'})`;
}

export function tagStatusMeta(status) {
  return TAG_STATUS_META[status] || { label: `未知状态(${status || '—'})`, tone: 'idle' };
}

export function candidateStatusMeta(status) {
  return CANDIDATE_STATUS_META[status] || { label: `未知状态(${status || '—'})`, tone: 'idle' };
}

export function aliasTypeLabel(type) {
  return ALIAS_TYPE_LABELS[type] || `未知类型(${type || '—'})`;
}

export function entityTypeLabel(type) {
  return ENTITY_TYPE_LABELS[type] || (type ? `未知类型(${type})` : '');
}

export function tagDisplayName(tag) {
  return tag?.name_zh || tag?.name_en || tag?.label || tag?.code || '—';
}
