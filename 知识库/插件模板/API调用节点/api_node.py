"""API 调用节点实现

演示 ComfyUI 中异步 API 调用的最佳实践：
- 使用 HIDDEN 输入传递 api_key（不在节点 UI 上显示）
- 超时处理（aiohttp.ClientTimeout）
- 错误恢复与重试机制
- 响应解析与类型转换
- IS_CHANGED 控制缓存
"""

import json
import logging
import asyncio
from typing import Tuple

import aiohttp

logger = logging.getLogger("APICallNode")

# ─── 常量 ──────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30  # 默认超时秒数
MAX_RETRIES = 3  # 最大重试次数
RETRY_DELAY = 1.0  # 重试间隔（秒）


class APICallNode:
    """API 调用节点模板

    向指定的 URL 发送 HTTP 请求，支持 GET/POST 方法，
    可选 API Key 认证（通过 HIDDEN 输入传递，不在 UI 上显示）。
    包含超时处理和自动重试。
    """

    @classmethod
    def INPUT_TYPES(cls):
        """定义输入端口和参数控件"""
        return {
            "required": {
                "url": (
                    "STRING",
                    {"default": "https://httpbin.org/get", "multiline": False},
                ),
                "method": (["GET", "POST"], {"default": "GET"}),
                "body": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
                "timeout": (
                    "INT",
                    {"default": DEFAULT_TIMEOUT, "min": 1, "max": 300, "step": 1},
                ),
            },
            "optional": {
                "headers": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
            },
            "hidden": {
                # HIDDEN 输入不会在节点 UI 上显示
                # api_key 由 ComfyUI 的 UNIQUE_ID 机制或外部注入
                "api_key": "API_KEY",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("response_body", "response_headers", "status_code")
    FUNCTION = "call_api"
    CATEGORY = "Custom/API"

    def call_api(
        self,
        url: str,
        method: str = "GET",
        body: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        headers: str = "",
        api_key: str = "",
        unique_id: str = "",
    ) -> Tuple[str, str, int]:
        """执行 API 调用

        参数:
            url: 请求 URL
            method: HTTP 方法（GET / POST）
            body: POST 请求体（JSON 格式字符串）
            timeout: 超时时间（秒）
            headers: 自定义请求头（JSON 格式字符串）
            api_key: API 密钥（HIDDEN，不在 UI 显示）
            unique_id: 节点唯一 ID（HIDDEN）

        返回:
            (response_body, response_headers, status_code)
        """
        # 由于 ComfyUI 节点执行是同步的，我们需要用 asyncio.run 来执行异步请求
        # 如果已在事件循环中，则使用 ensure_future
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已在事件循环中，创建新线程执行
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._async_call(
                            url, method, body, timeout, headers, api_key
                        ),
                    )
                    result = future.result(timeout=timeout + 10)
            else:
                result = asyncio.run(
                    self._async_call(url, method, body, timeout, headers, api_key)
                )
        except Exception as e:
            logger.error("API 调用失败: %s", e)
            return (json.dumps({"error": str(e)}, ensure_ascii=False), "{}", -1)

        return result

    async def _async_call(
        self,
        url: str,
        method: str,
        body: str,
        timeout: int,
        headers_str: str,
        api_key: str,
    ) -> Tuple[str, str, int]:
        """异步执行 API 调用（含重试逻辑）"""

        # 解析请求头
        request_headers = {"Content-Type": "application/json"}
        if headers_str.strip():
            try:
                extra_headers = json.loads(headers_str)
                request_headers.update(extra_headers)
            except json.JSONDecodeError:
                logger.warning("请求头 JSON 解析失败，使用默认请求头")

        # 添加 API Key 认证头
        if api_key:
            request_headers["Authorization"] = f"Bearer {api_key}"

        # 解析请求体
        json_body = None
        if method == "POST" and body.strip():
            try:
                json_body = json.loads(body)
            except json.JSONDecodeError:
                # 非 JSON 格式则作为纯文本发送
                request_headers["Content-Type"] = "text/plain"
                json_body = body

        # 配置超时
        client_timeout = aiohttp.ClientTimeout(total=timeout)

        # 带重试的请求
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession(timeout=client_timeout) as session:
                    async with session.request(
                        method=method,
                        url=url,
                        json=json_body if isinstance(json_body, (dict, list)) else None,
                        data=json_body if isinstance(json_body, str) else None,
                        headers=request_headers,
                    ) as response:
                        response_body = await response.text()
                        response_headers = json.dumps(
                            dict(response.headers), ensure_ascii=False
                        )
                        status_code = response.status

                        logger.info(
                            "API 调用成功: %s %s -> %d (尝试 %d/%d)",
                            method, url, status_code, attempt, MAX_RETRIES,
                        )
                        return (response_body, response_headers, status_code)

            except asyncio.TimeoutError:
                last_error = f"请求超时（{timeout}秒）"
                logger.warning("API 调用超时 (尝试 %d/%d): %s", attempt, MAX_RETRIES, url)
            except aiohttp.ClientError as e:
                last_error = f"网络错误: {str(e)}"
                logger.warning("API 调用网络错误 (尝试 %d/%d): %s", attempt, MAX_RETRIES, e)
            except Exception as e:
                last_error = f"未知错误: {str(e)}"
                logger.error("API 调用未知错误 (尝试 %d/%d): %s", attempt, MAX_RETRIES, e)

            # 非最后一次尝试时等待后重试
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)

        # 所有重试均失败
        error_response = json.dumps(
            {"error": last_error, "retries": MAX_RETRIES}, ensure_ascii=False
        )
        return (error_response, "{}", -1)

    @staticmethod
    def IS_CHANGED(url: str, method: str, body: str, **kwargs) -> float:
        """URL 或方法变化时需要重新调用

        使用 float("nan") 表示每次都重新执行（API 调用结果可能变化）
        """
        return float("nan")
