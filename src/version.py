"""版本号——全项目单一事实来源。

管理方式(2026-07 确立):
- 语义化版本(SemVer):MAJOR = 产品形态级改版 / MINOR = 功能波 / PATCH = 修复;
- 改版本只改这里,并同步 pyproject.toml 的 version(项目非 editable install,
  importlib.metadata 读不到包元数据,故以本常量为准);
- `/api/runtime` 透出 version,前端「设置 → 关于」展示;
- 合入 main 的版本节点打 annotated git tag(v{__version__})。

纪元回溯:1.x = 采集/归档 CMS 原型(单管理员);2.x = 读者分发平台
(双角色/订阅/RAG/日报/运维,PM2 app 名 dorami-backend-v2 即此纪元遗痕);
3.0.0 = 静默仪器全站重构 + 实体简化/阶段3 收官(style/quiet-instrument 合入 main)。
"""

__version__ = "3.19.2"
