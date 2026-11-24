"""
动态抓取器注册中心 (src/fetchers/registry.py)

利用 Python 反射与包扫描机制，实现抓取器的零配置热插拔。
只要在 impl 目录下继承了 BaseFetcher 并填写了 source_id，即会自动在此注册。

预设源的身份(名称 / 图标 / 简介 / 形态 / 准入 / 策展维度)不写在类上,写在策展源目录文件
src/sources/catalog/*.toml 里,类定义时回投到类属性(issue #262 R2,见 fetchers.base.apply_catalog_identity)。
目录有结构错误、或者 impl 里的预设类在目录里没有条目,discover() 直接抛 CatalogError——
这类错误不走下面「单个模块加载失败只告警」的隔离。
"""

import importlib
import pkgutil
import inspect
from typing import Type, Dict, List, Any, Optional

from fetchers import catalog_files
from fetchers.base import BaseFetcher


ADMISSION_STATUSES = ("admitted", "incubating")


def admission_status_of(value: Any) -> str:
    """规范化准入状态;未知值按正式处理(不让拼错的值把节点挤出每日采集之外再无人察觉)。"""
    status = str(value or "").strip().lower()
    return status if status in ADMISSION_STATUSES else "admitted"


def focused_curation_for(source_id: str, *, is_template: bool = False) -> Dict[str, str | bool]:
    """注册元数据里的两个历史字段 ``default_visible`` / ``curation_reason``。

    「是不是进过审阅的策展源」现在只有一个答案:在不在策展源目录(``src/sources/catalog``)里。
    以前这里另有一份手写白名单 ``ESSENTIAL_FETCHER_IDS``,内容恰好就是全部预设源,加源要改两处;
    下线源的墓碑名单 ``DECOMMISSIONED_FETCHER_IDS`` 也曾在本模块,现在在目录的 ``system.toml``,
    经 ``services.source_catalog.tombstone_ids`` 读。
    这两个字段没有运行时消费方,留着只为注册元数据的输出不变;R3 抓取器去身份时随元数据一起瘦身。
    """
    if source_id in catalog_files.catalog():
        return {
            "default_visible": True,
            "curation_reason": "推荐审查源：已按 v1.0 维度完成书面化标注，进入从 0 做加法的当前批次。",
        }
    if is_template:
        return {
            "default_visible": False,
            "curation_reason": "通用扩展能力，不属于默认 AI 资讯源。",
        }
    return {
        "default_visible": False,
        "curation_reason": "与当前 AI 资讯/应用/模型/Agent 聚焦目标弱相关，默认隐藏。",
    }


class FetcherRegistry:
    def __init__(self):
        # 内部维护的映射表： { "source_id": FetcherClass }
        self._fetchers: Dict[str, Type[BaseFetcher]] = {}

    def register(self, fetcher_class: Type[BaseFetcher]):
        """手动注册抓取器类"""
        if not issubclass(fetcher_class, BaseFetcher) or fetcher_class is BaseFetcher:
            return

        source_id = getattr(fetcher_class, 'source_id', 'unknown')
        if source_id == "unknown_source" or source_id == "unknown":
            return  # 跳过没有正确定义源ID的中间抽象类

        self._fetchers[source_id] = fetcher_class

    def discover(self, package_name: str = "fetchers.impl"):
        """
        动态扫描并注册指定包下的所有抓取器。
        默认扫描 src/fetchers/impl 文件夹。
        """
        # 策展源目录先加载(在异常隔离之外):目录坏了就别扫了,不能只剩几个模板还照常启动。
        catalog_files.catalog()
        try:
            # 导入包本身
            package = importlib.import_module(package_name)

            # 遍历包目录下的所有模块 (.py 文件)
            for _, module_name, _ in pkgutil.iter_modules(package.__path__):
                full_module_name = f"{package_name}.{module_name}"
                try:
                    module = importlib.import_module(full_module_name)

                    # 提取模块中定义的所有类
                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        # 判断是否为 BaseFetcher 的子类（且排除自身）
                        if issubclass(obj, BaseFetcher) and obj is not BaseFetcher:
                            self.register(obj)
                except catalog_files.CatalogError:
                    # 预设类缺目录条目 / 目录结构错误:不是「某个模块坏了」,是身份来源坏了,必须起不来。
                    raise
                except Exception as module_err:
                    # ✨ 修复：隔离单个模块的加载错误，防止一颗老鼠屎坏了一锅粥
                    print(f"⚠️ 无法加载抓取器模块 [{full_module_name}]: {module_err}")

            print(f"🔌 抓取器注册中心就绪，成功挂载 {len(self._fetchers)} 个数据源节点。")

        except ImportError as e:
            print(f"⚠️ 无法扫描抓取器包 {package_name}: {e}")

    def get_class(self, source_id: str) -> Optional[Type[BaseFetcher]]:
        """根据 ID 获取抓取器类"""
        return self._fetchers.get(source_id)

    def get_all_metadata(self) -> List[Dict[str, Any]]:
        """
        生成给前端的注册表大纲，前端据此渲染出所有的面板和表单。
        """
        metadata_list = []
        for source_id, cls in self._fetchers.items():
            is_template = bool(getattr(cls, "is_template", False))
            curation = focused_curation_for(source_id, is_template=is_template)
            metadata_list.append({
                "id": source_id,
                "name": cls.name,
                "icon": cls.icon,
                "desc": cls.description,
                "admission_status": admission_status_of(getattr(cls, "admission_status", "")),
                "content_type": cls.content_type,
                "shape": getattr(cls, "content_shape", "article"),
                "default_kind": getattr(cls, "default_kind", "") or "",
                "platform": getattr(cls, "platform", ""),
                "active": True,
                "is_template": is_template,
                "parameters": cls.get_parameter_schema(),
                "source_owner": getattr(cls, "source_owner", ""),
                "source_brand": getattr(cls, "source_brand", ""),
                "source_scope": getattr(cls, "source_scope", ""),
                "source_channel": getattr(cls, "source_channel", ""),
                "source_url": getattr(cls, "source_url", ""),
                "base_url": getattr(cls, "source_url", "") or getattr(cls, "listing_url", "") or getattr(cls, "feed_url", ""),
                "provenance_tier": getattr(cls, "provenance_tier", ""),
                "signal_strength": getattr(cls, "signal_strength", ""),
                "noise_risk": getattr(cls, "noise_risk", ""),
                "fetch_reliability": getattr(cls, "fetch_reliability", ""),
                **curation,
            })
        return metadata_list


# ==========================================
# 单例实例化与自启动
# ==========================================
fetcher_registry = FetcherRegistry()

# 当此文件被 app.py 导入时，立刻执行一次全目录扫描发现
fetcher_registry.discover()
