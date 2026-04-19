import asyncio
import os
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional, TypedDict

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

WIKI_BASE_URL = "https://limbuscompany.huijiwiki.com"
DEFAULT_SEARCH_LIMIT = 10
DEFAULT_SCREENSHOT_TIMEOUT = 60000
DEFAULT_PAGE_WAIT = 3000
SESSION_TIMEOUT_SECONDS = 30


class WikiSearchResult(TypedDict):
    title: str
    url: str
    index: int


@dataclass
class SessionEntry:
    results: List[WikiSearchResult]
    last_access: float


class LimbusWikiSearchPlugin(Star):
    """
    边狱公司中文维基搜索插件
    
    配置选项：
    - wiki_base_url: 维基基础URL，默认: https://limbuscompany.huijiwiki.com
    - search_limit: 搜索结果数量限制，默认: 10
    - screenshot_timeout: 截图超时时间(毫秒)，默认: 60000
    - page_wait_ms: 页面加载等待时间(毫秒)，默认: 3000
    - session_timeout: 会话超时时间(秒)，默认: 30
    """
    
    # 配置选项
    config = {
        "wiki_base_url": {
            "type": "string",
            "label": "维基基础URL",
            "default": WIKI_BASE_URL,
            "desc": "维基百科的基础URL地址"
        },
        "search_limit": {
            "type": "integer",
            "label": "搜索结果数量",
            "default": DEFAULT_SEARCH_LIMIT,
            "min": 1,
            "max": 50,
            "desc": "每次搜索返回的结果数量"
        },
        "screenshot_timeout": {
            "type": "integer",
            "label": "截图超时时间",
            "default": DEFAULT_SCREENSHOT_TIMEOUT,
            "min": 1000,
            "max": 300000,
            "desc": "页面截图的超时时间(毫秒)"
        },
        "page_wait_ms": {
            "type": "integer",
            "label": "页面加载等待时间",
            "default": DEFAULT_PAGE_WAIT,
            "min": 500,
            "max": 10000,
            "desc": "页面加载后等待的时间(毫秒)"
        },
        "session_timeout": {
            "type": "integer",
            "label": "会话超时时间",
            "default": SESSION_TIMEOUT_SECONDS,
            "min": 5,
            "max": 300,
            "desc": "搜索会话的超时时间(秒)"
        }
    }
    
    def __init__(self, context: Context):
        super().__init__(context)
        self._session_data: Dict[str, SessionEntry] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._playwright = None
        self._browser = None
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # 尝试从配置中加载参数，如果不存在则使用默认值
        try:
            if hasattr(context, '_config') and context._config:
                self._wiki_base_url = context._config.get('wiki_base_url', WIKI_BASE_URL)
                self._search_limit = context._config.get('search_limit', DEFAULT_SEARCH_LIMIT)
                self._screenshot_timeout = context._config.get('screenshot_timeout', DEFAULT_SCREENSHOT_TIMEOUT)
                self._page_wait_ms = context._config.get('page_wait_ms', DEFAULT_PAGE_WAIT)
                self._session_timeout = context._config.get('session_timeout', SESSION_TIMEOUT_SECONDS)
                logger.info(f"插件配置加载完成: wiki_base_url={self._wiki_base_url}, search_limit={self._search_limit}")
            else:
                # 配置系统不可用，使用默认值
                self._wiki_base_url = WIKI_BASE_URL
                self._search_limit = DEFAULT_SEARCH_LIMIT
                self._screenshot_timeout = DEFAULT_SCREENSHOT_TIMEOUT
                self._page_wait_ms = DEFAULT_PAGE_WAIT
                self._session_timeout = SESSION_TIMEOUT_SECONDS
                logger.info("配置系统不可用，使用默认配置")
        except Exception as e:
            # 任何配置错误都回退到默认值
            self._wiki_base_url = WIKI_BASE_URL
            self._search_limit = DEFAULT_SEARCH_LIMIT
            self._screenshot_timeout = DEFAULT_SCREENSHOT_TIMEOUT
            self._page_wait_ms = DEFAULT_PAGE_WAIT
            self._session_timeout = SESSION_TIMEOUT_SECONDS
            logger.warning(f"配置加载失败: {e}，使用默认配置")

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _cleanup_expired_sessions(self) -> None:
        current_time = time.time()
        timeout = self._session_timeout
        expired_sessions = [
            sid for sid, entry in self._session_data.items()
            if current_time - entry.last_access > timeout
        ]
        for sid in expired_sessions:
            async with self._get_session_lock(sid):
                if sid in self._session_data:
                    del self._session_data[sid]
                if sid in self._session_locks:
                    del self._session_locks[sid]
            logger.debug(f"已清理过期会话: {sid}")
        if expired_sessions:
            logger.info(f"已清理 {len(expired_sessions)} 个过期会话")

    async def _start_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await self._cleanup_expired_sessions()
            except Exception as e:
                logger.warning(f"清理过期会话时出错: {e}")

    def _update_session_access(self, session_id: str) -> None:
        if session_id in self._session_data:
            self._session_data[session_id].last_access = time.time()

    async def _init_browser(self) -> bool:
        if self._browser is not None:
            return True

        try:
            from playwright.async_api import async_playwright
            from playwright.async_api import Error as PlaywrightError
        except ImportError:
            logger.error("Playwright 未安装，请运行以下命令安装:")
            logger.error("  pip install playwright")
            logger.error("  playwright install chromium")
            return False

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
            logger.info("Playwright 浏览器已初始化")
            return True
        except PlaywrightError as e:
            logger.error(f"Playwright 启动失败: {e}")
            logger.error("请确保已运行: playwright install chromium")
            logger.error("如已安装，请检查系统依赖是否完整（如 libgtk3）")
            return False
        except Exception as e:
            logger.error(f"初始化 Playwright 浏览器时发生未知错误: {e}")
            return False

    async def _search_wiki(self, keyword: str, limit: Optional[int] = None) -> List[WikiSearchResult]:
        results: List[WikiSearchResult] = []
        if limit is None:
            limit = self._search_limit

        api_url = f"{self._wiki_base_url}/api.php"
        params = {
            "action": "opensearch",
            "search": keyword,
            "limit": limit,
            "format": "json",
            "formatversion": "2",
        }

        data = await self._fetch_with_curl_cffi(api_url, params)
        if data is None:
            data = await self._fetch_with_httpx(api_url, params)

        if data and len(data) >= 4:
            titles = data[1] if len(data) > 1 else []
            urls = data[3] if len(data) > 3 else []

            for i, title in enumerate(titles):
                safe_url = urls[i] if i < len(urls) else (
                    f"{self._wiki_base_url}/wiki/{urllib.parse.quote(title)}"
                )
                results.append({
                    "title": title,
                    "url": safe_url,
                    "index": i + 1
                })

        if not results:
            direct_url = f"{self._wiki_base_url}/wiki/{urllib.parse.quote(keyword)}"
            results.append({
                "title": keyword,
                "url": direct_url,
                "index": 1
            })

        return results

    async def _fetch_with_curl_cffi(self, api_url: str, params: Dict[str, object]) -> Optional[list]:
        try:
            import curl_cffi.requests
            from curl_cffi.requests import RequestError as CurlCffiError
        except ImportError:
            return None

        try:
            response = curl_cffi.requests.get(
                api_url,
                params=params,
                impersonate="chrome120",
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except CurlCffiError as e:
            logger.warning(f"curl_cffi 请求失败: {e}")
            return None
        except ValueError as e:
            logger.warning(f"curl_cffi 响应 JSON 解析失败: {e}")
            return None

    async def _fetch_with_httpx(self, api_url: str, params: Dict[str, object]) -> Optional[list]:
        try:
            import httpx
            from httpx import HTTPError, TimeoutException
        except ImportError:
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                response = await client.get(api_url, params=params)
                response.raise_for_status()
                return response.json()
        except TimeoutException as e:
            logger.warning(f"httpx 请求超时: {e}")
            return None
        except HTTPError as e:
            logger.warning(f"httpx HTTP 错误: {e}")
            return None
        except ValueError as e:
            logger.warning(f"httpx 响应 JSON 解析失败: {e}")
            return None

    async def _capture_page(self, url: str) -> Optional[str]:
        browser_ok = await self._init_browser()
        if not browser_ok:
            return None

        page = None
        try:
            page = await self._browser.new_page()
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            await page.set_viewport_size({"width": 1280, "height": 2000})
            logger.info(f"访问页面: {url}")

            timeout_ms = self._screenshot_timeout
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            await page.wait_for_timeout(self._page_wait_ms)

            temp_dir = tempfile.gettempdir()
            url_hash = abs(hash(url))
            screenshot_path = os.path.join(temp_dir, f"wiki_screenshot_{url_hash}.png")
            await page.screenshot(path=screenshot_path, full_page=True)
            logger.info(f"截图已保存: {screenshot_path}")
            return screenshot_path

        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None
        finally:
            if page:
                await page.close()

    @filter.command("wiki")
    async def wiki_search(self, event: AstrMessageEvent):
        await self._start_cleanup_task()

        keyword = event.message_str.strip()
        keyword = keyword.replace("wiki", "", 1).strip()
        if not keyword:
            yield event.plain_result("请输入搜索关键词，例如: wiki 良秀")
            return

        logger.info(f"搜索关键词: {keyword}")

        try:
            results = await self._search_wiki(keyword)
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            yield event.plain_result(f"搜索失败，请稍后重试。错误信息: {e}")
            return

        session_id = event.session_id
        lock = await self._get_session_lock(session_id)

        async with lock:
            self._session_data[session_id] = SessionEntry(
                results=results,
                last_access=time.time()
            )

        msg_lines = [f"找到 {len(results)} 个与 '{keyword}' 相关的结果:"]
        for r in results:
            msg_lines.append(f"{r['index']}. {r['title']}")
        msg_lines.append(f"\n请回复数字选择要查看的页面（{self._session_timeout}秒内有效）")

        yield event.plain_result("\n".join(msg_lines))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_selection(self, event: AstrMessageEvent):
        session_id = event.session_id
        lock = await self._get_session_lock(session_id)

        async with lock:
            if session_id not in self._session_data:
                return

            self._update_session_access(session_id)

            message = event.message_str.strip()

            try:
                selection = int(message)
            except ValueError:
                return

            entry = self._session_data[session_id]
            results = entry.results

            if selection < 1 or selection > len(results):
                yield event.plain_result(f"无效选择，请输入 1-{len(results)} 之间的数字")
                return

            selected = results[selection - 1]
            url = selected["url"]
            title = selected["title"]

        yield event.plain_result(f"正在获取 '{title}' 的页面，请稍候...")

        try:
            screenshot_path = await self._capture_page(url)
            if screenshot_path and os.path.exists(screenshot_path):
                yield event.image_result(screenshot_path)
                try:
                    os.remove(screenshot_path)
                except OSError:
                    logger.warning(f"无法删除临时截图文件: {screenshot_path}")
            else:
                error_text = f"页面: {title}\n链接: {url}"
                try:
                    yield event.image_result(await event.text_to_image(error_text))
                except Exception as e:
                    logger.error(f"生成错误图片失败: {e}")
                    yield event.plain_result(error_text)
        except Exception as e:
            logger.error(f"截图过程出错: {e}")
            error_text = f"页面: {title}\n链接: {url}"
            try:
                yield event.image_result(await event.text_to_image(error_text))
            except Exception:
                yield event.plain_result(error_text)

    async def terminate(self):
        logger.info("正在关闭 LimbusWikiSearchPlugin 浏览器实例...")

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self._browser:
            try:
                await self._browser.close()
                logger.info("浏览器实例已关闭")
            except Exception as e:
                logger.warning(f"关闭浏览器实例时出错: {e}")
            finally:
                self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
                logger.info("Playwright 已停止")
            except Exception as e:
                logger.warning(f"停止 Playwright 时出错: {e}")
            finally:
                self._playwright = None

        self._session_data.clear()
        self._session_locks.clear()
        logger.info("LimbusWikiSearchPlugin 已卸载")