import os
import tempfile
import urllib.parse
from typing import Dict, List, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

WIKI_BASE_URL = "https://limbuscompany.huijiwiki.com"


class LimbusWikiSearchPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._session_data: Dict[str, List[dict]] = {}
        self._playwright = None
        self._browser = None

    async def _init_browser(self):
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch()
                logger.info("Playwright 浏览器已初始化")
            except Exception as e:
                logger.warning(f"初始化 Playwright 浏览器失败: {e}")
                raise

    async def _search_wiki(self, keyword: str, limit: int = 10) -> List[dict]:
        results = []
        
        try:
            api_url = f"{WIKI_BASE_URL}/api.php"
            params = {
                "action": "opensearch",
                "search": keyword,
                "limit": limit,
                "format": "json",
                "formatversion": "2",
            }
            
            try:
                import curl_cffi.requests
                response = curl_cffi.requests.get(
                    api_url,
                    params=params,
                    impersonate="chrome120",
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()
                
                if len(data) >= 4:
                    titles = data[1] if len(data) > 1 else []
                    urls = data[3] if len(data) > 3 else []
                    
                    for i, title in enumerate(titles):
                        url = urls[i] if i < len(urls) else f"{WIKI_BASE_URL}/wiki/{urllib.parse.quote(title)}"
                        results.append({"title": title, "url": url, "index": i + 1})
            except ImportError:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=30, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }) as client:
                        response = await client.get(api_url, params=params)
                        response.raise_for_status()
                        data = response.json()
                        
                        if len(data) >= 4:
                            titles = data[1] if len(data) > 1 else []
                            urls = data[3] if len(data) > 3 else []
                            
                            for i, title in enumerate(titles):
                                url = urls[i] if i < len(urls) else f"{WIKI_BASE_URL}/wiki/{urllib.parse.quote(title)}"
                                results.append({"title": title, "url": url, "index": i + 1})
                except Exception as e:
                    logger.warning(f"使用httpx获取搜索建议失败: {e}")
            except Exception as e:
                logger.warning(f"使用curl_cffi获取搜索建议失败: {e}")
        except Exception as e:
            logger.warning(f"获取搜索建议失败: {e}")
        
        if not results:
            direct_url = f"{WIKI_BASE_URL}/wiki/{urllib.parse.quote(keyword)}"
            results.append({"title": keyword, "url": direct_url, "index": 1})
        
        return results

    async def _capture_page(self, url: str) -> Optional[str]:
        try:
            await self._init_browser()
        except:
            return None
            
        page = None
        try:
            page = await self._browser.new_page()
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            await page.set_viewport_size({"width": 1280, "height": 2000})
            logger.info(f"访问页面: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(3000)
            temp_dir = tempfile.gettempdir()
            screenshot_path = os.path.join(temp_dir, f"wiki_screenshot_{hash(url)}.png")
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
        '''搜索边狱公司中文维基'''
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
            yield event.plain_result(f"搜索失败: {e}")
            return

        session_id = event.session_id
        
        for idx, r in enumerate(results):
            r['index'] = idx + 1
        
        self._session_data[session_id] = results

        msg_lines = [f"找到 {len(results)} 个与 '{keyword}' 相关的结果:"]
        for r in results:
            msg_lines.append(f"{r['index']}. {r['title']}")
        msg_lines.append("\n请回复数字选择要查看的页面")

        yield event.plain_result("\n".join(msg_lines))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_selection(self, event: AstrMessageEvent):
        session_id = event.session_id
        if session_id not in self._session_data:
            return

        message = event.message_str.strip()
        try:
            selection = int(message)
        except ValueError:
            return

        results = self._session_data.pop(session_id)
        if selection < 1 or selection > len(results):
            self._session_data[session_id] = results
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
                except:
                    pass
            else:
                error_text = f"页面: {title}\n链接: {url}"
                try:
                    yield event.image_result(await event.text_to_image(error_text))
                except Exception:
                    yield event.plain_result(error_text)
        except Exception as e:
            logger.error(f"截图过程出错: {e}")
            error_text = f"页面: {title}\n链接: {url}"
            try:
                yield event.image_result(await event.text_to_image(error_text))
            except Exception:
                yield event.plain_result(error_text)

    async def terminate(self):
        if self._browser:
            try:
                await self._browser.close()
            except:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
        logger.info("LimbusWikiSearchPlugin 已卸载")
