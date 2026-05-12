import os
import sys
import json
import asyncio
import random
import html
import time
import logging
import requests
import hashlib
from urllib.parse import urlparse, parse_qs, urlunparse
from typing import AsyncGenerator, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from fetchers.base import BaseFetcher
from models.content import BaseContent, WechatArticleContent

# ==========================================
# 统一存储配置：收纳凭证与临时文件，保持项目目录整洁
# ==========================================
AUTH_BASE_DIR = os.path.join(os.getcwd(), ".wechat_auth")
os.makedirs(AUTH_BASE_DIR, exist_ok=True)

# ====== 新增：全局异步锁，防止并发触发多个登录会话 ======
_WECHAT_AUTH_LOCK = asyncio.Lock()


class XiaolubanNotifier:
    """
    小鲁班机器人通知工具类 (解耦通信逻辑)
    依赖系统环境变量进行配置，杜绝硬编码敏感信息。
    """

    @staticmethod
    def send_msg(content: str):
        # 从环境变量中安全获取敏感配置
        auth = os.getenv("XIAOLUBAN_AUTH")
        receiver = os.getenv("XIAOLUBAN_RECEIVER")

        logger = logging.getLogger("XiaolubanNotifier")

        if not auth or not receiver:
            logger.warning("未配置 XIAOLUBAN_AUTH 或 XIAOLUBAN_RECEIVER 环境变量，跳过机器人消息发送。")
            return

        url = 'http://xiaoluban.rnd.huawei.com:80/'
        data = {'content': content, 'receiver': receiver, 'auth': auth}

        try:
            # 显式禁用代理，防止内网请求被劫持
            res = requests.post(
                url=url,
                json=data,
                timeout=10,
                proxies={"http": None, "https": None}
            )
            if not res.ok:
                logger.error(f"消息发送失败，接口返回: {res.text}")
            else:
                logger.info("✅ 扫码提醒已成功通过小鲁班发送。")
        except Exception as e:
            logger.error(f"消息发送过程发生异常: {e}")


class ImageHostUploader:
    """
    图床上传工具类
    """

    @staticmethod
    def upload(image_path: str) -> Optional[str]:
        logger = logging.getLogger("ImageHostUploader")
        try:
            timestamp = int(time.time())
            secret_key = 'bIbT0orLEO5FDGyciQKL0ounccep04qk'
            code = hashlib.md5(f'{secret_key}{timestamp}'.encode('utf-8')).hexdigest()

            url = f"http://3ms.huawei.com/hi/restnew/editor/attach/upload?app_id=67&public_key=10067&current_timestamp={timestamp}&verify_code={code}"

            with open(image_path, 'rb') as image_file:
                files = {
                    'action': (None, 'upload_image'),
                    'attach_binary': (image_path, image_file, 'multipart/form-data')
                }
                response = requests.post(url, files=files, timeout=15, proxies={"http": None, "https": None})

            if response.status_code != 200:
                raise Exception(f"HTTP Status {response.status_code} - {response.text}")

            response_data = response.json()
            if response_data.get('imgUrl') is None:
                raise Exception(response_data.get('message', 'Unknown error'))

            # 返回裁剪后的干净 URL
            return response_data.get('imgUrl').split('@900-0-90-f')[0]

        except Exception as e:
            logger.error(f"图床上传失败: {e}")
            return None


class BaseWechatGzhFetcher(BaseFetcher):
    """
    微信公众号抓取器通用基类（不直接挂载到前端）
    封装了 Playwright 收割、微信后台接口请求与清洗等所有底层逻辑。
    """
    source_id = "unknown"
    content_type = "wechat_article"
    target_account = ""
    CREDENTIALS_FILE = os.path.join(AUTH_BASE_DIR, "wechat_config.json")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 强制突破 FastAPI/Uvicorn 的日志屏蔽，确保 INFO 级别的进度日志能够打印到终端
        self.logger.setLevel(logging.INFO)
        if not any(isinstance(h, logging.StreamHandler) for h in self.logger.handlers):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(logging.Formatter('[%(name)s] %(levelname)s: %(message)s'))
            self.logger.addHandler(console_handler)

    @classmethod
    def get_parameter_schema(cls) -> list[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限 (篇)", "type": "number", "default": 5},
            {"field": "days_back", "label": "时间回溯天数 (建议2天以规避微信延迟)", "type": "number", "default": 2},
            {"field": "headless", "label": "无头模式 (建议服务器开启)", "type": "boolean", "default": True}
        ]

    def _clean_url(self, raw_url: str) -> str:
        if not raw_url:
            return ""

        raw_url = html.unescape(raw_url)
        parsed = urlparse(raw_url)
        qs = parse_qs(parsed.query)
        core_keys = ['__biz', 'mid', 'idx', 'sn']

        clean_qs_parts = []
        for k in core_keys:
            if k in qs:
                clean_qs_parts.append(f"{k}={qs[k][0]}")
        new_query = "&".join(clean_qs_parts)

        scheme = "https" if parsed.scheme == "http" else parsed.scheme
        return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    def _sync_harvest_credentials(self, headless: bool) -> Optional[Dict[str, str]]:
        from playwright.sync_api import sync_playwright

        self.logger.info("🚀 启动 Playwright 自动化凭证收割 (等待弹出全屏二维码)...")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,
                    args=[
                        '--headless=new',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-blink-features=AutomationControlled'
                    ]
                )

                # 核心防白图策略：强制给无头浏览器设置一个正常的屏幕尺寸，防止它默认缩成一团导致截图空白
                context = browser.new_context(viewport={'width': 1280, 'height': 800})
                default_ua = context.pages[0].evaluate(
                    "navigator.userAgent") if context.pages else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                clean_ua = default_ua.replace("HeadlessChrome", "Chrome")

                # 重新设置干净的 UA
                context = browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    user_agent=clean_ua
                )

                # 去除 webdriver 指纹
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                page = context.new_page()

                # 等待 DOM 加载
                page.goto("https://mp.weixin.qq.com/", wait_until="networkidle")

                # 强制等待 8 秒
                self.logger.info("⏳ 页面加载完毕，强行等待 8 秒确保二维码完全渲染...")
                page.wait_for_timeout(8000)

                # page info
                self.logger.info(f"当前页面标题: {page.title()}")
                self.logger.info(f"页面前500字符: {page.content()[:500]}")

                # 直接全屏截图
                qr_code_path = os.path.join(AUTH_BASE_DIR, "wechat_login_qr.png")
                abs_qr_path = os.path.abspath(qr_code_path)

                page.screenshot(path=abs_qr_path)
                self.logger.info(f"📸 全屏防白截图已保存至: {abs_qr_path}")

                self.logger.info("☁️ 正在将截图上传至图床...")
                img_url = ImageHostUploader.upload(abs_qr_path)

                if img_url:
                    msg = f"⚠️ [哆啦美中枢] 微信抓取凭证已过期/丢失。\n请在 5 分钟内点击下方链接扫码确认登录：\n{img_url}"
                    self.logger.info(f"发送图床直达链接: {img_url}")
                else:
                    local_uri = abs_qr_path.replace('\\', '/')
                    msg = f"⚠️ [哆啦美中枢] 图床上传失败，退回本地模式。\n请在 5 分钟内扫码确认登录：\n本地直达链接: file:///{local_uri}"
                    self.logger.info(f"图床失败，发送本地兜底链接: file:///{local_uri}")

                XiaolubanNotifier.send_msg(msg)

                # 等待扫码后的 URL 跳转特征
                page.wait_for_url("**/cgi-bin/home?**token=**", timeout=300000)
                self.logger.info("✅ 扫码成功！正在提取底层凭证...")

                current_url = page.url
                token = parse_qs(urlparse(current_url).query).get('token', [''])[0]

                cookies = context.cookies()
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

                config_data = {
                    "token": token,
                    "cookie": cookie_str,
                    "update_time": datetime.now().isoformat()
                }

                with open(self.CREDENTIALS_FILE, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=4)

                self.logger.info("🎉 凭证已提取并缓存到本地，下次自动跳过扫码。")
                return config_data

        except Exception as e:
            self.logger.error(f"❌ 自动化凭证收割发生异常: {e}")
            return None

    async def _auto_harvest_credentials(self, headless: bool = True) -> Optional[Dict[str, str]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_harvest_credentials, headless)

    async def _get_or_refresh_credentials(self, headless: bool, force_refresh: bool = False) -> Optional[
        Dict[str, str]]:
        global _WECHAT_AUTH_LOCK

        # 如果不是强制刷新，先快速检查是否有本地文件
        if not force_refresh and os.path.exists(self.CREDENTIALS_FILE):
            with open(self.CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

        self.logger.info("⏳ 准备获取/刷新凭证，正在排队等待全局锁 (防止并发冲突)...")
        async with _WECHAT_AUTH_LOCK:
            # 进入锁后，再次检查：是否已经被刚才释放锁的其他并发任务给刷新过了？
            if os.path.exists(self.CREDENTIALS_FILE):
                try:
                    with open(self.CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                        config = json.load(f)
                        update_time_str = config.get("update_time")
                        if update_time_str:
                            update_time = datetime.fromisoformat(update_time_str)
                            # 如果凭证是在最近 2 分钟内更新的，说明肯定是其他并发任务刚刷新的，直接复用
                            if (datetime.now() - update_time).total_seconds() < 120:
                                self.logger.info("✅ 发现凭证已被其他并发任务刷新，直接复用，跳过扫码。")
                                return config
                except Exception as e:
                    self.logger.warning(f"读取凭证文件异常: {e}")

            # 如果确实没有可用凭证，才真正拉起浏览器
            self.logger.info("🔄 开始执行微信扫码登录流程...")
            return await self._auto_harvest_credentials(headless)

    async def _get_article_content_no_cookie(self, url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x6309080f) XWEB/9129",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://mp.weixin.qq.com/"
        }

        async with httpx.AsyncClient(verify=False, timeout=15.0, follow_redirects=True) as clean_client:
            try:
                response = await clean_client.get(url, headers=headers)
                response.raise_for_status()
                html_text = response.text

                soup = BeautifulSoup(html_text, 'html.parser')
                content_div = soup.find('div', class_='rich_media_content') or soup.find('div', id='js_content')

                if content_div:
                    return content_div.get_text(separator='\n', strip=True)
                else:
                    title_tag = soup.find('title')
                    page_title = title_tag.get_text(strip=True) if title_tag else "无标题"
                    self.logger.warning(f"⚠️ 未能定位到正文容器！页面标题: 【{page_title}】")
                    return ""
            except Exception as e:
                self.logger.warning(f"文章正文解析引发异常: {e}")
                return ""

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        account_name = self.target_account
        if not account_name:
            self.logger.error(f"❌ 抓取器 {self.name} 未配置 target_account 属性！")
            return

        limit = int(kwargs.get("limit", 10))
        days_back = int(kwargs.get("days_back", 2))

        headless_param = kwargs.get("headless", True)
        headless = headless_param if isinstance(headless_param, bool) else str(headless_param).lower() == 'true'

        tz_bj = timezone(timedelta(hours=8))
        now_bj = datetime.now(tz_bj)
        cutoff_dt = now_bj.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back - 1)
        cutoff_ts = cutoff_dt.timestamp()

        self.logger.info(
            f"⚙️ [{self.name}] 策略: 上限 {limit} 篇 | 回溯线: 北京时间 {cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        # 第一处：正常获取凭证（走锁排队）
        config = await self._get_or_refresh_credentials(headless, force_refresh=False)
        if not config:
            return

        token = config.get("token")
        cookie = config.get("cookie")

        req_headers = {
            **self.default_headers,
            "Cookie": cookie,
            "Referer": "https://mp.weixin.qq.com/"
        }

        self.logger.info(f"🔍 正在检索目标公众号: [{account_name}] ...")
        fakeid = None
        search_url = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
        for _ in range(2):
            params = {
                "action": "search_biz", "begin": "0", "count": "5",
                "query": account_name, "token": token, "lang": "zh_CN", "f": "json", "ajax": "1"
            }
            res = await self._safe_get(client, search_url, params=params, headers=req_headers)
            if not res: return

            data = res.json()
            ret_code = data.get('base_resp', {}).get('ret')

            if ret_code == 200003:
                self.logger.warning("♻️ 接口提示凭证已过期 (200003)，触发自愈重新收割...")
                # 第二处：遇到过期强制刷新凭证（走锁排队）
                config = await self._get_or_refresh_credentials(headless, force_refresh=True)
                if not config: return
                token, req_headers["Cookie"] = config["token"], config["cookie"]
                continue

            biz_list = data.get('list', [])
            if biz_list:
                for biz in biz_list:
                    if biz.get('nickname', '').strip() == account_name.strip():
                        fakeid = biz.get('fakeid')
                        break
                if not fakeid:
                    fakeid = biz_list[0].get('fakeid')
                    self.logger.warning(f"⚠️ 未找到完全同名的公众号，兜底使用首位结果: [{biz_list[0].get('nickname')}]")

                self.logger.info(f"✅ 成功命中公众号，提取 fakeid: {fakeid}")
            break

        if not fakeid:
            self.logger.error(f"❌ 无法获取公众号 {account_name} 的 fakeid，可能是名字不完全匹配或账号被封。")
            return

        appmsg_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        total_fetched = 0
        page = 0
        is_finished = False
        stale_count = 0

        while not is_finished:
            self.logger.info(f"📦 正在向微信后台请求第 {page + 1} 批次列表数据...")
            begin = page * 15

            for _ in range(2):
                params = {
                    "action": "list_ex", "begin": str(begin), "count": "15", "fakeid": fakeid,
                    "type": "9", "query": "", "token": token, "lang": "zh_CN", "f": "json", "ajax": "1"
                }

                res = await self._safe_get(client, appmsg_url, params=params, headers=req_headers)
                if not res: break

                data = res.json()
                ret_code = data.get('base_resp', {}).get('ret')

                if ret_code == 200003:
                    self.logger.warning("♻️ 列表接口提示凭证失效 (200003)，触发自愈...")
                    # 第三处：遇到过期强制刷新凭证（走锁排队）
                    config = await self._get_or_refresh_credentials(headless, force_refresh=True)
                    if not config: return
                    token, req_headers["Cookie"] = config["token"], config["cookie"]
                    continue
                elif ret_code == 200013:
                    self.logger.error("🚫 触发微信后台请求频率限制 (200013)，请暂停抓取数小时。")
                    return

                msg_list = data.get('app_msg_list', [])
                if not msg_list:
                    self.logger.info("ℹ️ 列表返回空，已触底历史底线。")
                    is_finished = True
                    break

                for msg in msg_list:
                    raw_time = msg.get('update_time')
                    time_val = int(raw_time) if raw_time else 0
                    pub_time_bj = datetime.fromtimestamp(time_val, tz_bj).strftime('%Y-%m-%d %H:%M:%S')

                    if time_val < cutoff_ts:
                        stale_count += 1
                        self.logger.info(f"⏭️ 忽略老文章 ({stale_count}/5): 《{msg.get('title')}》发布于 {pub_time_bj}")
                        if stale_count >= 5:
                            self.logger.info("🛑 连续 5 篇过期，触发安全时间熔断。")
                            is_finished = True
                            break
                        continue
                    else:
                        stale_count = 0

                    if total_fetched >= limit:
                        self.logger.info(f"🛑 触发数量熔断: 已达到单次抓取上限 ({limit}篇)。")
                        is_finished = True
                        break

                    clean_link = self._clean_url(msg.get('link', ''))

                    self.logger.info(f"⏳ 准备抓取正文 (休眠防风控): 《{msg.get('title')}》")
                    await asyncio.sleep(random.uniform(1.5, 3.5))

                    article_content = await self._get_article_content_no_cookie(clean_link)

                    sn_list = parse_qs(urlparse(clean_link).query).get('sn', [])
                    sn = sn_list[0] if sn_list else None
                    aid = sn or msg.get('aid') or msg.get('appmsgid') or str(random.randint(10000, 99999))

                    pub_time_iso = datetime.fromtimestamp(time_val, tz_bj).isoformat()

                    total_fetched += 1
                    self.logger.info(f"✅ 成功产出 [{total_fetched}/{limit}]: 《{msg.get('title')}》")

                    yield WechatArticleContent(
                        id=f"{self.source_id}_{aid}",
                        title=msg.get('title'),
                        source_url=clean_link,
                        publish_date=pub_time_iso,
                        content=article_content,
                        has_content=bool(article_content),
                        account_name=account_name,
                        digest=msg.get('digest', ''),
                        cover_url=msg.get('cover', ''),
                        original_url=msg.get('link', '')
                    )
                break

            page += 1
            if not is_finished:
                await asyncio.sleep(random.uniform(3, 5))

            if page > 50:
                self.logger.warning("⚠️ 达到最大批次限制(50批)，强制终止以防止无限循环。")
                break


# ==========================================
# 下方为具体向前端暴露的各个公众号独立抓取节点
# ==========================================

class JiQiZhiXinWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_jiqizhixin"
    category = "wechat"
    name = "机器之心"
    icon = "🤖"
    description = "抓取「机器之心」微信公众号的最新 AI 产业、研究与前沿报道。"
    target_account = "机器之心"


class QbitAIWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_qbitai"
    category = "wechat"
    name = "量子位"
    icon = "⚛️"
    description = "抓取「量子位」微信公众号的 AI 科技新闻、大模型进展追踪。"
    target_account = "量子位"


class XinzhiyuanWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_xinzhiyuan"
    category = "wechat"
    name = "新智元"
    icon = "🧠"
    description = "抓取「新智元」微信公众号的 AI 深度解析、人物专访与行业洞察。"
    target_account = "新智元"


class AiTechReviewWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_ai_tech_review"
    category = "wechat"
    name = "AI科技评论"
    icon = "📰"
    description = "抓取「AI科技评论」微信公众号的 AI 研究、产业与技术评论。"
    target_account = "AI科技评论"


class InfoQAiWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_infoq_ai"
    category = "wechat"
    name = "AI前线"
    icon = "💻"
    description = "抓取「AI前线」微信公众号的 AI 工程、架构与开发者生态内容。"
    target_account = "AI前线"


class ZhidxWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_zhidx"
    category = "wechat"
    name = "智东西"
    icon = "🔬"
    description = "抓取「智东西」微信公众号的 AI 硬件、产业与科技公司动态。"
    target_account = "智东西"


class FounderParkWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_founder_park"
    category = "wechat"
    name = "Founder Park"
    icon = "🏗️"
    description = "抓取「Founder Park」微信公众号的 AI 创业、产品与投资生态内容。"
    target_account = "Founder Park"


class SiliconStarWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_silicon_star"
    category = "wechat"
    name = "硅星人"
    icon = "🌉"
    description = "抓取「硅星人」微信公众号的硅谷、AI 产品与全球科技生态内容。"
    target_account = "硅星人"


class XixiaoyaoWechatFetcher(BaseWechatGzhFetcher):
    source_id = "wechat_xixiaoyao"
    category = "wechat"
    name = "夕小瑶科技说"
    icon = "🧪"
    description = "抓取「夕小瑶科技说」微信公众号的 AI 技术解读、论文与应用趋势。"
    target_account = "夕小瑶科技说"
