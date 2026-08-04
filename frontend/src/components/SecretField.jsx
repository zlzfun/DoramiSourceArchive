/**
 * 统一凭据输入件——「只写不回显」契约的表单半边(对应后端 services/credentials)。
 *
 * 后端 GET 永不回明文,只给 `{field}_set` 布尔与尾四位掩码 `{field}_preview`;
 * 本组件把这两个信号翻译成占位文案(已设置 → 「••••9876 · 留空不改」,未设置 →
 * 调用方给的示例 hint),输入框始终以空值起步——有输入=覆盖,留空=保留既有机密。
 * 外层 label 与布局留在调用方,保持各面板既有语境(model-field / sett-field)。
 */
export default function SecretField({
  value,
  onChange,
  isSet = false,
  preview = '',
  hint = '',
  ...rest
}) {
  // 掩码预览本身即「已有凭据」的信号,占位不再赘述「留空不改」;语义细节挂 title。
  const placeholder = isSet ? (preview || '已设置') : hint;
  return (
    <input
      type="password"
      autoComplete="new-password"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      title={isSet ? '已设置,输入新值将覆盖' : undefined}
      {...rest}
    />
  );
}
