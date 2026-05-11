import httpx
import datetime
from typing import AsyncGenerator

# 引入基础的抓取器父类和内容模型
from fetchers.base import BaseFetcher
from models.content import BaseContent


class DifyWebhookTrigger(BaseFetcher):
    """
    通用的大模型工作流触发器 (Webhook Trigger)
    这是一个特殊的“反向节点”，它不负责向内抓取数据入库，
    而是作为数据管道的后续编排节点，主动向外部系统发送执行指令。
    """

    # ==========================================
    # 1. 架构解耦标识 (必须声明为类属性，供注册中心扫描)
    # ==========================================
    source_id = "webhook_dify_workflow"
    content_type = "webhook_trigger"  # 作为一个虚拟动作标识
    category = "workflow"

    # ==========================================
    # 2. 前端 UI 渲染元数据 (声明为类属性)
    # ==========================================
    name = "Dify 自动化日报编排"
    description = "后置驱动节点：通过 API 主动触发外部 Dify 工作流，执行数据归纳与生成任务。"
    icon = "🤖"

    def __init__(self):
        # 由于 Dify 阻塞模式生成文章耗时较长，我们在这里将基类的 timeout 直接拉长到 120 秒
        super().__init__(timeout=120)

    @classmethod
    def get_parameter_schema(cls) -> list:
        """
        向前端暴露出可供动态修改的参数列表
        """
        return [
            {
                "field": "webhook_url",
                "label": "Webhook API 地址 (必填)",
                "type": "url",
                "default": "http://www.starling.huawei.com/v1/workflows/run"
            },
            {
                "field": "auth_token",
                "label": "Bearer 认证秘钥 (必填)",
                "type": "text",
                "default": "app-VyVDSYKrP2Ie5t3kPWsYzpEE"
            },
            {
                "field": "target_date",
                "label": "目标切片日期 (留空默认今天)",
                "type": "text",
                "default": ""
            },
            {
                "field": "target_count",
                "label": "期望处理数量 (Count)",
                "type": "number",
                "default": 50
            }
        ]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        """
        核心执行逻辑：发起向外的指令
        """
        webhook_url = kwargs.get("webhook_url", "").strip()
        auth_token = kwargs.get("auth_token", "").strip()
        target_date = kwargs.get("target_date", "").strip()
        target_count = kwargs.get("target_count", 50)

        if not webhook_url:
            self.logger.error("❌ Webhook URL 不能为空，放弃触发。")
            return

        # 如果没有传入特定日期，默认智能提取服务器当天的 YYYY-MM-DD
        if not target_date:
            target_date = datetime.datetime.now().strftime("%Y-%m-%d")

        # 组装 Dify 标准的鉴权头部
        headers = {
            "Authorization": f"Bearer {auth_token}" if auth_token else "",
            "Content-Type": "application/json"
        }

        # 组装您期望的 Payload
        payload = {
            "inputs": {
                "target_date": target_date,
                "target_count": str(target_count)
            },
            "response_mode": "blocking",  # 阻塞等待 Dify 执行完毕返回完整结果
            "user": "auto-bot"
        }

        self.logger.info(f"🚀 正在准备触发外部工作流: {webhook_url}")
        self.logger.info(f"📦 传递核心参数: target_date={target_date}, target_count={target_count}")

        try:
            # ✨ 核心修复：绕过系统代理
            # 放弃使用父类传进来的带有代理设定的 client
            # 显式实例化一个全新的 AsyncClient，并设置 trust_env=False 来强制忽略所有的 HTTP_PROXY 环境变量
            async with httpx.AsyncClient(trust_env=False, timeout=120.0) as direct_client:
                response = await direct_client.post(webhook_url, headers=headers, json=payload)

                if response.status_code == 200:
                    # 避免日志过长，只打印前 200 个字符
                    resp_preview = response.text[:200] + ("..." if len(response.text) > 200 else "")
                    self.logger.info(f"✅ Dify 工作流触发成功! 响应速览: {resp_preview}")
                else:
                    self.logger.error(f"⚠️ Dify 工作流触发异常! 状态码: {response.status_code}, 详情: {response.text}")

        except httpx.TimeoutException:
            self.logger.warning(
                "⏱️ Dify 工作流触发成功，但等待返回超时。(系统已将内网请求发出，由于 response_mode=blocking，Dify 仍在后台执行)")
        except Exception as e:
            self.logger.error(f"❌ 触发网络请求彻底失败 (请确认内网是否可达): {e}")

        # 满足 AsyncGenerator 的类型契约，但不实际入库任何数据
        if False:
            yield None
