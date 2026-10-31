// 只保存实际编辑的字段,换主模型不会把其它档位与预算固化成运行时覆盖。
export function llmUpdates(form, effective) {
  const result = {};
  for (const key of ['base_url', 'model', 'temperature', 'max_tokens', 'thinking_mode', 'aux_model', 'vision_model']) {
    const value = ['temperature', 'max_tokens'].includes(key) ? Number(form[key]) : String(form[key] ?? '').trim();
    if (value !== effective?.[key]) result[key] = value;
  }
  if (form.api_key?.trim()) result.api_key = form.api_key.trim();
  return result;
}

export function deepseekFlashGuidance(baseUrl, model) {
  try {
    if (new URL(baseUrl).hostname !== 'api.deepseek.com') return null;
  } catch { return null; }
  const legacy = ['deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'].includes(model);
  if (!legacy && model !== 'deepseek-flash') return null;
  return { legacy, model: 'deepseek-flash', version: 'V4.1-Flash-1017' };
}
