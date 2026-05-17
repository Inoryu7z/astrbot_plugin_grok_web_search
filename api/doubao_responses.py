"""
豆包 Responses API 异步客户端

通过火山方舟 Responses API (/api/v3/responses) 调用豆包模型进行联网搜索。
使用 web_search 工具实现联网搜索，支持附加搜索来源、搜索限制等豆包特有功能。
也支持通过 Bot API (/api/v3/bots/chat/completions) 调用带网页解析插件的零代码应用。
"""

import json
import time
from typing import Any

import aiohttp

from ..tool.tool import (
    DOUBAO_DEFAULT_SYSTEM_PROMPT,
    IMAGE_UNSUPPORTED_ERROR,
    build_headers,
    format_http_error,
    get_local_time_info,
    make_error_result,
    merge_extra_body,
    normalize_base_url,
    normalize_image,
    retry_request,
    validate_config,
)

DOUBAO_API_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_DOMAIN_MARKER = "ark.cn-beijing.volces.com"


def is_doubao_provider(base_url: str) -> bool:
    return DOUBAO_DOMAIN_MARKER in base_url


async def doubao_responses_search(
    query: str,
    base_url: str,
    api_key: str,
    model: str = "doubao-seed-2-0-pro-260215",
    timeout: float = 60.0,
    extra_body: dict | None = None,
    extra_headers: dict | None = None,
    session: aiohttp.ClientSession | None = None,
    system_prompt: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    retryable_status_codes: set[int] | None = None,
    images: list[str] | None = None,
    proxy: str | None = None,
    sources: list[str] | None = None,
    max_keyword: int | None = None,
    limit: int | None = None,
    max_tool_calls: int | None = None,
    user_location: dict | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """
    通过豆包 Responses API 进行联网搜索（异步）

    使用 /api/v3/responses 端点，支持 web_search 工具。

    Args:
        query: 搜索查询内容
        base_url: 火山方舟 API 端点
        api_key: API 密钥
        model: 模型名称
        timeout: 超时时间（秒）
        extra_body: 额外请求体参数
        extra_headers: 额外请求头
        session: 可选的 aiohttp.ClientSession
        system_prompt: 自定义系统提示词
        max_retries: 最大重试次数
        retry_delay: 重试间隔时间（秒）
        retryable_status_codes: 可重试的 HTTP 状态码集合
        images: 可选的 base64 编码图片列表
        proxy: HTTP 代理地址
        sources: 附加搜索来源列表（如 ["douyin", "moji", "toutiao"]）
        max_keyword: 单轮搜索最大关键词数量（1-50）
        limit: 单轮搜索返回最大结果条数（1-50）
        max_tool_calls: 一次响应中工具调用最大轮次（1-10）
        user_location: 用户地理位置信息，优化地域相关搜索（如 {"country": "中国", "region": "浙江", "city": "杭州"}）

    Returns:
        {
            "ok": bool,
            "content": str,
            "sources": list,
            "raw": str,
            "error": str,
            "elapsed_ms": int,
            "retries": int,
        }
    """
    started = time.time()

    config = validate_config(
        base_url, api_key, started, base_url_label="火山方舟 API 端点"
    )
    if isinstance(config, dict):
        return config
    base_url, api_key = config

    url = f"{normalize_base_url(base_url)}/api/v3/responses"

    final_system_prompt = (
        system_prompt if system_prompt is not None else DOUBAO_DEFAULT_SYSTEM_PROMPT
    )

    time_context = get_local_time_info()
    enriched_query = f"{time_context}\n{query}"

    if images:
        user_content: list[dict[str, Any]] = [
            {"type": "input_text", "text": enriched_query}
        ]
        for img_b64 in images:
            result = normalize_image(img_b64)
            if result is None:
                return IMAGE_UNSUPPORTED_ERROR
            mime, img_b64 = result
            user_content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{img_b64}",
                }
            )
        user_input = user_content
    else:
        user_input = [{"type": "input_text", "text": enriched_query}]

    web_search_tool: dict[str, Any] = {"type": "web_search"}
    if max_keyword is not None and 1 <= max_keyword <= 50:
        web_search_tool["max_keyword"] = max_keyword
    if limit is not None and 1 <= limit <= 50:
        web_search_tool["limit"] = limit
    if sources and isinstance(sources, list):
        valid_sources = [s for s in sources if s in ("douyin", "moji", "toutiao")]
        if valid_sources:
            web_search_tool["sources"] = valid_sources
    if user_location and isinstance(user_location, dict):
        loc = {"type": "approximate"}
        if user_location.get("country"):
            loc["country"] = str(user_location["country"])
        if user_location.get("region"):
            loc["region"] = str(user_location["region"])
        if user_location.get("city"):
            loc["city"] = str(user_location["city"])
        if len(loc) > 1:
            web_search_tool["user_location"] = loc

    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": final_system_prompt}],
            },
            {"role": "user", "content": user_input},
        ],
        "tools": [web_search_tool],
    }

    if max_tool_calls is not None and 1 <= max_tool_calls <= 10:
        body["max_tool_calls"] = max_tool_calls

    if reasoning_effort and reasoning_effort in ("minimal", "low", "medium", "high"):
        body["reasoning"] = {"effort": reasoning_effort}
        if reasoning_effort == "minimal":
            body["thinking"] = {"type": "disabled"}
        else:
            body["thinking"] = {"type": "enabled"}

    merge_extra_body(
        body,
        extra_body,
        {
            "model",
            "input",
            "tools",
            "stream",
            "max_tool_calls",
            "reasoning",
            "thinking",
        },
    )
    headers = build_headers(api_key, extra_headers)

    async def _do_request(
        s: aiohttp.ClientSession,
        req_proxy: str | None = None,
    ) -> dict[str, Any]:
        async with s.post(
            url,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            proxy=req_proxy,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                return format_http_error(resp.status, error_text, started, resp.headers)

            raw_text = await resp.text()

            try:
                data = json.loads(raw_text)
                return {"ok": True, "data": data}
            except json.JSONDecodeError:
                return make_error_result(
                    "响应解析失败，API 返回了非 JSON 格式的数据",
                    started,
                    raw=raw_text[:2000] if raw_text else "",
                )

    result = await retry_request(
        _do_request,
        session=session,
        proxy=proxy,
        max_retries=max_retries,
        retry_delay=retry_delay,
        retryable_status_codes=retryable_status_codes,
        timeout=timeout,
        started=started,
    )

    if not result.get("ok") or "data" not in result:
        return result
    data = result["data"]
    retry_count = result.get("retries", 0)

    message = ""
    citations: list[dict[str, str]] = []
    usage_info = {}
    parse_error = ""

    try:
        if "error" in data and isinstance(data.get("error"), (dict, str)):
            error_info = data["error"]
            error_msg = (
                error_info.get("message", str(error_info))
                if isinstance(error_info, dict)
                else str(error_info)
            )
            return make_error_result(
                f"API 返回错误: {error_msg}",
                started,
                raw=json.dumps(data, ensure_ascii=False)[:2000],
            )

        output = data.get("output", [])
        if not output:
            parse_error = "响应缺少 output 字段"
        else:
            for item in output:
                if item.get("type") == "message":
                    content_list = item.get("content", [])
                    for content_item in content_list:
                        if content_item.get("type") == "output_text":
                            message = content_item.get("text", "")
                            annotations = content_item.get("annotations", [])
                            for ann in annotations:
                                if ann.get("type") == "url_citation":
                                    citations.append(
                                        {
                                            "url": ann.get("url", ""),
                                            "title": ann.get("title", ""),
                                        }
                                    )
                            break
                    break

            usage = data.get("usage", {})
            if usage:
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                usage_info = {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }
                tool_usage = usage.get("tool_usage")
                if tool_usage:
                    usage_info["tool_usage"] = tool_usage
                tool_usage_details = usage.get("tool_usage_details")
                if tool_usage_details:
                    usage_info["tool_usage_details"] = tool_usage_details

        if not message:
            parse_error = parse_error or "API 返回了空响应"

    except (KeyError, IndexError, TypeError) as e:
        parse_error = f"响应结构解析失败: {type(e).__name__}: {e}"

    if not message:
        error_detail = parse_error or "API 返回了空响应"
        return make_error_result(
            f"{error_detail}，请稍后重试",
            started,
            retry_count,
            raw=json.dumps(data, ensure_ascii=False)[:2000] if data else "",
        )

    sources_list: list[dict[str, str]] = []
    if citations:
        for cit in citations:
            url_val = cit.get("url", "")
            if url_val and url_val.startswith("http"):
                sources_list.append(
                    {
                        "url": url_val,
                        "title": cit.get("title", ""),
                        "snippet": "",
                    }
                )

    if not sources_list:
        from ..tool.tool import extract_urls

        for url_str in extract_urls(message):
            sources_list.append({"url": url_str, "title": "", "snippet": ""})

    return {
        "ok": True,
        "content": message,
        "sources": sources_list,
        "raw": message,
        "model": data.get("model") or model,
        "usage": usage_info,
        "elapsed_ms": int((time.time() - started) * 1000),
        "retries": retry_count,
    }


async def doubao_bot_fetch(
    url: str,
    base_url: str,
    api_key: str,
    bot_id: str,
    timeout: float = 60.0,
    extra_body: dict | None = None,
    extra_headers: dict | None = None,
    session: aiohttp.ClientSession | None = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    retryable_status_codes: set[int] | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """
    通过豆包 Bot API 调用带网页解析插件的零代码应用进行网页抓取（异步）

    使用 /api/v3/bots/chat/completions 端点，model 字段填 bot_id。
    零代码应用需在豆包控制台预先配置网页解析插件。

    Args:
        url: 要抓取的网页 URL
        base_url: 火山方舟 API 端点
        api_key: API 密钥
        bot_id: 零代码应用ID（如 bot-20xxxxxxxx）
        timeout: 超时时间（秒）
        extra_body: 额外请求体参数
        extra_headers: 额外请求头
        session: 可选的 aiohttp.ClientSession
        max_retries: 最大重试次数
        retry_delay: 重试间隔时间（秒）
        retryable_status_codes: 可重试的 HTTP 状态码集合
        proxy: HTTP 代理地址

    Returns:
        {
            "ok": bool,
            "content": str,
            "sources": list,
            "raw": str,
            "error": str,
            "elapsed_ms": int,
            "retries": int,
            "image_urls": list[str],
        }
    """
    started = time.time()

    config = validate_config(
        base_url, api_key, started, base_url_label="火山方舟 API 端点"
    )
    if isinstance(config, dict):
        return config
    base_url, api_key = config

    if not bot_id or not bot_id.strip():
        return make_error_result(
            "缺少 fetch_bot_id 配置，请在提供商设置中填写带网页解析插件的零代码应用ID",
            started,
        )

    api_url = f"{normalize_base_url(base_url)}/api/v3/bots/chat/completions"

    fetch_prompt = (
        f"请读取以下网页的完整内容，并将其转换为结构化的 Markdown 格式返回。\n"
        f"网页URL：{url}\n\n"
        f"要求：\n"
        f"1. 保留网页的所有正文内容，不要省略\n"
        f"2. 保持标题层级结构\n"
        f"3. 转换表格、列表、代码块为 Markdown 格式\n"
        f"4. 移除广告、导航栏等非正文内容\n"
        f"5. 【重要】必须提取网页中所有图片的完整 URL，以 Markdown 图片语法 ![描述](URL) 格式嵌入正文中对应位置。如果无法嵌入正文，则在文末单独列出所有图片 URL，每行一个，格式为：![image](图片URL)。不要省略任何图片 URL，即使图片很多也要全部列出\n"
        f"6. 图片 URL 通常以 .jpg .jpeg .png .gif .webp .svg 结尾，或包含 imgsrc pic image photo 等关键词的链接，请仔细识别"
    )

    body: dict[str, Any] = {
        "model": bot_id.strip(),
        "stream": False,
        "messages": [
            {"role": "user", "content": fetch_prompt},
        ],
    }

    merge_extra_body(body, extra_body, {"model", "messages", "stream"})
    headers = build_headers(api_key, extra_headers)

    async def _do_request(
        s: aiohttp.ClientSession,
        req_proxy: str | None = None,
    ) -> dict[str, Any]:
        async with s.post(
            api_url,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            proxy=req_proxy,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                return format_http_error(resp.status, error_text, started, resp.headers)

            raw_text = await resp.text()

            try:
                data = json.loads(raw_text)
                return {"ok": True, "data": data}
            except json.JSONDecodeError:
                return make_error_result(
                    "响应解析失败，API 返回了非 JSON 格式的数据",
                    started,
                    raw=raw_text[:2000] if raw_text else "",
                )

    result = await retry_request(
        _do_request,
        session=session,
        proxy=proxy,
        max_retries=max_retries,
        retry_delay=retry_delay,
        retryable_status_codes=retryable_status_codes,
        timeout=timeout,
        started=started,
    )

    if not result.get("ok") or "data" not in result:
        return result
    data = result["data"]
    retry_count = result.get("retries", 0)

    message = ""
    image_urls: list[str] = []
    sources_list: list[dict[str, str]] = []
    usage_info = {}
    parse_error = ""

    try:
        if "error" in data and isinstance(data.get("error"), (dict, str)):
            error_info = data["error"]
            error_msg = (
                error_info.get("message", str(error_info))
                if isinstance(error_info, dict)
                else str(error_info)
            )
            return make_error_result(
                f"Bot API 返回错误: {error_msg}",
                started,
                raw=json.dumps(data, ensure_ascii=False)[:2000],
            )

        choices = data.get("choices", [])
        if choices and isinstance(choices, list):
            choice = choices[0]
            msg = choice.get("message", {})
            message = msg.get("content", "") or ""

        references = data.get("references", [])
        if isinstance(references, list):
            for ref in references:
                if not isinstance(ref, dict):
                    continue
                ref_url = ref.get("url", "")
                if ref_url and ref_url.startswith("http"):
                    sources_list.append(
                        {
                            "url": ref_url,
                            "title": ref.get("title", ""),
                            "snippet": ref.get("summary", ""),
                        }
                    )
                cover_image = ref.get("cover_image", "")
                if cover_image and cover_image.startswith("http"):
                    image_urls.append(cover_image)

        bot_usage = data.get("bot_usage", {})
        if bot_usage:
            model_usage_list = bot_usage.get("model_usage", [])
            total_prompt = 0
            total_completion = 0
            for mu in model_usage_list:
                total_prompt += mu.get("prompt_tokens", 0)
                total_completion += mu.get("completion_tokens", 0)
            usage_info = {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
            }

        if not message:
            parse_error = parse_error or "Bot API 返回了空响应"

    except (KeyError, IndexError, TypeError) as e:
        parse_error = f"Bot API 响应结构解析失败: {type(e).__name__}: {e}"

    if not message:
        error_detail = parse_error or "Bot API 返回了空响应"
        return make_error_result(
            f"{error_detail}，请稍后重试",
            started,
            retry_count,
            raw=json.dumps(data, ensure_ascii=False)[:2000] if data else "",
        )

    from ..tool.tool import extract_urls

    img_url_pattern = r"https?://[^\s)\]\}>\"']+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^\s)\]\}>\"']*)?"
    for match in __import__("re").finditer(
        img_url_pattern, message, __import__("re").IGNORECASE
    ):
        img_url = match.group().rstrip(".,;:!?\"'")
        if img_url and img_url not in image_urls:
            image_urls.append(img_url)

    if not sources_list:
        for url_str in extract_urls(message):
            sources_list.append({"url": url_str, "title": "", "snippet": ""})

    return {
        "ok": True,
        "content": message,
        "sources": sources_list,
        "raw": message,
        "model": data.get("model") or bot_id,
        "usage": usage_info,
        "elapsed_ms": int((time.time() - started) * 1000),
        "retries": retry_count,
        "image_urls": image_urls,
    }
