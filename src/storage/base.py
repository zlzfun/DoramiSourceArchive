"""
存储层基类 (src/storage/base.py)
"""
import abc
import logging
from typing import Optional, Any, Dict
from models.content import BaseContent

class BaseStorage(abc.ABC):
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @abc.abstractmethod
    async def save(self, item: BaseContent) -> bool:
        """
        保存单条数据的接口 (Create)
        :param item: 标准化的内容对象
        :return: bool 是否保存成功 (或是否为新增数据)
        """
        pass

    @abc.abstractmethod
    async def get(self, item_id: str) -> Optional[Any]:
        """
        查询单条数据的接口 (Read)
        :param item_id: 数据的唯一ID
        :return: 查询到的数据对象，不存在则返回 None
        """
        pass

    @abc.abstractmethod
    async def update(self, item_id: str, updates: Dict[str, Any]) -> bool:
        """
        更新单条数据的接口 (Update)
        :param item_id: 数据的唯一ID
        :param updates: 包含更新字段的字典
        :return: bool 是否更新成功
        """
        pass

    @abc.abstractmethod
    async def delete(self, item_id: str) -> bool:
        """
        删除单条数据的接口 (Delete)
        :param item_id: 数据的唯一ID
        :return: bool 是否删除成功
        """
        pass