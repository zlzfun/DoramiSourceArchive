const terms = (value) => value.split(/[,，]/).map((term) => term.trim()).filter(Boolean);

export function customInterestForm(item = null) {
  const intent = item?.condition?.semantic_intent;
  return {
    mode: item ? (item.condition.match_mode === 'topic' ? 'topic' : 'rules') : 'topic',
    name: item?.name || '',
    description: item?.description || '',
    groups: item ? item.condition.all_of.map((group) => group.any_of.join('，')) : [''],
    exclude: (item?.condition?.exclude || []).join('，'),
    requirement: intent?.requirement || '',
    negativeExamples: [...(intent?.negative_examples || [])],
    status: item?.status || 'active',
  };
}

export function customInterestBody(form, editing = null) {
  const requirement = form.requirement.trim();
  const topic = form.mode === 'topic';
  if (topic && !requirement) throw new Error('填写你感兴趣的话题范围');
  const negativeExamples = form.negativeExamples.map((example) => example.trim());
  if (negativeExamples.length && !requirement) throw new Error('填写匹配说明，或移除排除示例');
  if (negativeExamples.some((example) => !example)) throw new Error('填写排除示例，或移除空白项');
  return {
    name: form.name.trim(),
    description: form.description,
    condition: {
      ...(topic ? { match_mode: 'topic' } : editing?.condition?.match_mode === 'topic' ? { match_mode: null } : {}),
      all_of: topic ? [] : form.groups.map((group) => ({ any_of: terms(group) })),
      exclude: terms(form.exclude),
      semantic_intent: requirement ? { version: 1, requirement, negative_examples: negativeExamples } : null,
    },
    status: form.status,
    ...(editing ? { expected_revision: editing.revision } : {}),
  };
}
