// 关于(弹窗波):产品与账户信息,sett-kv 键值对。版本来自 /api/runtime(源:src/version.py)。
// 构建行(tag 即发布波):utils/buildLabel 按 runtime.build 判发布版/非发布版。
import { buildLabel } from '../../utils/buildLabel';

export default function AboutSection({ accountRoleLabel, isAdmin, version, build }) {
  return (
    <dl className="sett-kv">
      <dt>产品</dt><dd>{isAdmin ? '哆啦美·归档中枢' : '哆啦美'}</dd>
      <dt>版本</dt><dd>{version || '—'}</dd>
      <dt>构建</dt><dd>{buildLabel(version, build)}</dd>
      <dt>账户角色</dt><dd>{accountRoleLabel}</dd>
    </dl>
  );
}
