// 关于(弹窗波):产品与账户信息,sett-kv 键值对。
export default function AboutSection({ accountRoleLabel, isAdmin }) {
  return (
    <dl className="sett-kv">
      <dt>产品</dt><dd>{isAdmin ? '哆啦美·归档中枢' : '哆啦美'}</dd>
      <dt>账户角色</dt><dd>{accountRoleLabel}</dd>
    </dl>
  );
}
