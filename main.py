"""AstrBot 插件：Grok 联网搜索

通过 Grok API 进行实时联网搜索，支持：
- /grok 指令
- LLM Tool (grok_web_search)
- Skill 脚本动态安装
"""

import shutil
import tempfile
import zipfile
from pathlib import Path
import re
import aiohttp
import asyncio
import json
import os
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.core.star.filter.command import GreedyStr
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Image
from astrbot.core.utils.io import download_image_by_url, file_to_base64
from astrbot.core.utils.quoted_message.chain_parser import (
    _extract_image_refs_from_component_chain,
    _extract_text_from_component_chain,
)

from .api.grok_chat import grok_fetch, grok_search
from .api.grok_responses import grok_responses_search
from .api.doubao_responses import doubao_responses_search, is_doubao_provider

try:
    from astrbot.core.provider.register import llm_tools as _llm_tools_registry
except ImportError:
    _llm_tools_registry = None

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:
    get_astrbot_data_path = None

from .tool.tool import (
    DEFAULT_JSON_SYSTEM_PROMPT,
    DOUBAO_DEFAULT_SYSTEM_PROMPT,
    DOUBAO_JSON_SYSTEM_PROMPT,
    normalize_api_key,
    normalize_base_url,
    parse_json_config,
)
from .tool.card_render import (
    render_search_card,
    init_fonts,
    set_logger as set_card_logger,
)

PLUGIN_NAME = "astrbot_plugin_grok_web_search"


def _fmt_tokens(n: int) -> str:
    """将 token 数量格式化为简短形式，如 1m2k、3.5k、800。"""
    if n >= 1_000_000:
        m, remain = divmod(n, 1_000_000)
        k = remain // 1_000
        return f"{m}m{k}k" if k else f"{m}m"
    if n >= 1_000:
        k, remain = divmod(n, 1_000)
        h = remain // 100
        return f"{k}.{h}k" if h else f"{k}k"
    return str(n)


class GrokSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._card_fonts_ready = False

    async def _extract_content_from_event(
        self, event: AstrMessageEvent
    ) -> tuple[str | None, list[str]]:
        """Extract text and images from the user's message.

        Reuses AstrBot core's chain_parser for text/image extraction from
        Reply, Node, Nodes, Forward, etc.

        Returns:
            A tuple of (text, images):
            - text: extracted text from the message chain (or None)
            - images: list of base64-encoded image strings (without prefix)
        """
        chain = event.get_messages()
        text = _extract_text_from_component_chain(chain)
        image_refs = _extract_image_refs_from_component_chain(chain)
        images: list[str] = []
        seen: set[str] = set()
        for comp in chain:
            if isinstance(comp, Image):
                try:
                    b64 = await comp.convert_to_base64()
                    if b64 and b64 not in seen:
                        seen.add(b64)
                        images.append(b64)
                except Exception as e:
                    logger.warning(
                        f"[{PLUGIN_NAME}] Failed to convert image to base64: {e}"
                    )
        for ref in image_refs:
            try:
                img = Image.fromURL(ref)
                b64 = await img.convert_to_base64()
                if b64 and b64 not in seen:
                    seen.add(b64)
                    images.append(b64)
            except Exception as e:
                logger.warning(
                    f"[{PLUGIN_NAME}] Failed to convert image ref to base64: {e}"
                )
        return text, images

    def _unregister_disabled_tools(self):
        """根据配置在初始化时直接卸载不需要的 LLM Tool，避免 AI 看到无用工具"""
        if _llm_tools_registry is None:
            return
        if self.config.get("enable_skill", False):
            _llm_tools_registry.remove_func("grok_web_search")
            _llm_tools_registry.remove_func("grok_web_fetch")
            logger.info(
                f"[{PLUGIN_NAME}] Skill 已启用，已卸载 grok_web_search 和 grok_web_fetch 工具"
            )
            return
        if not self.config.get("enable_fetch", False):
            _llm_tools_registry.remove_func("grok_web_fetch")
            logger.info(f"[{PLUGIN_NAME}] 网页抓取未启用，已卸载 grok_web_fetch 工具")

    def _init_fonts(self):
        """Initialize card rendering fonts (runs in background)."""
        logger.info(f"[{PLUGIN_NAME}] 正在后台初始化卡片渲染字体 ...")
        try:
            if get_astrbot_data_path:
                font_dir = str(
                    Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "font"
                )
            else:
                font_dir = os.path.join(os.path.dirname(__file__), "font")
            set_card_logger(logger)
            self._card_fonts_ready = init_fonts(font_dir)
            if self._card_fonts_ready:
                logger.info(f"[{PLUGIN_NAME}] 卡片渲染字体已就绪: {font_dir}")
            else:
                logger.warning(f"[{PLUGIN_NAME}] 卡片渲染字体初始化失败")
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 字体初始化异常: {e}")

    def _get_custom_provider_pool(self) -> list[dict[str, str | int]]:
        """获取自定义 HTTP 提供商故障转移池。

        读取 providers 列表（template_list），按列表顺序返回，前面的提供商优先使用。
        """
        providers_list = self.config.get("providers")
        if not isinstance(providers_list, list) or not providers_list:
            return []

        result: list[dict[str, str | int]] = []
        for idx, item in enumerate(providers_list):
            if not isinstance(item, dict):
                continue
            base_url = normalize_base_url(str(item.get("base_url") or ""))
            api_key = normalize_api_key(str(item.get("api_key") or ""))
            if not base_url or not api_key:
                continue
            model_val = str(item.get("model", "") or "").strip()
            if not model_val:
                default_model = (
                    "doubao-seed-2-0-pro-260215"
                    if is_doubao_provider(base_url)
                    else str(self.config.get("model", "grok-4-fast") or "grok-4-fast")
                )
                model_val = default_model
            result.append(
                {
                    "index": idx + 1,
                    "name": f"provider{idx + 1}",
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model_val,
                }
            )
        return result

    async def _run_custom_provider_search(
        self,
        provider_cfg: dict,
        *,
        query: str,
        timeout: float,
        thinking_budget: int,
        system_prompt: str,
        max_retries: int,
        retry_delay: float,
        retryable_status_codes: set[int] | None,
        images: list[str] | None,
        proxy: str | None,
    ) -> dict:
        """对单个自定义 HTTP 提供商执行搜索。"""
        base_url = str(provider_cfg.get("base_url") or "")
        default_model = (
            "doubao-seed-2-0-pro-260215"
            if is_doubao_provider(base_url)
            else "grok-4-fast"
        )
        model = str(provider_cfg.get("model") or default_model)

        if is_doubao_provider(base_url):
            doubao_sources = self.config.get("doubao_sources", [])
            doubao_max_keyword = self.config.get("doubao_max_keyword", 5)
            doubao_limit = self.config.get("doubao_limit", 10)
            doubao_max_tool_calls = self.config.get("doubao_max_tool_calls", 3)
            doubao_enable_thinking = self.config.get("doubao_enable_thinking", False)

            if system_prompt == DEFAULT_JSON_SYSTEM_PROMPT:
                system_prompt = DOUBAO_JSON_SYSTEM_PROMPT
            elif system_prompt and "Return ONLY a single JSON object" in system_prompt:
                system_prompt = DOUBAO_DEFAULT_SYSTEM_PROMPT

            result = await doubao_responses_search(
                query=query,
                base_url=base_url,
                api_key=str(provider_cfg.get("api_key") or ""),
                model=model,
                timeout=timeout,
                extra_body=self._parse_json_config("extra_body"),
                extra_headers=self._parse_json_config("extra_headers"),
                session=self._session,
                system_prompt=system_prompt,
                max_retries=max_retries,
                retry_delay=retry_delay,
                retryable_status_codes=retryable_status_codes,
                images=images,
                proxy=proxy,
                sources=doubao_sources if doubao_sources else None,
                max_keyword=doubao_max_keyword,
                limit=doubao_limit,
                max_tool_calls=doubao_max_tool_calls,
                enable_thinking=doubao_enable_thinking,
            )
        elif self.config.get("use_responses_api", False):
            result = await grok_responses_search(
                query=query,
                base_url=base_url,
                api_key=str(provider_cfg.get("api_key") or ""),
                model=model,
                timeout=timeout,
                extra_body=self._parse_json_config("extra_body"),
                extra_headers=self._parse_json_config("extra_headers"),
                session=self._session,
                system_prompt=system_prompt,
                max_retries=max_retries,
                retry_delay=retry_delay,
                retryable_status_codes=retryable_status_codes,
                images=images,
                proxy=proxy,
            )
        else:
            result = await grok_search(
                query=query,
                base_url=base_url,
                api_key=str(provider_cfg.get("api_key") or ""),
                model=model,
                timeout=timeout,
                enable_thinking=self.config.get("enable_thinking", True),
                thinking_budget=thinking_budget,
                extra_body=self._parse_json_config("extra_body"),
                extra_headers=self._parse_json_config("extra_headers"),
                session=self._session,
                system_prompt=system_prompt,
                max_retries=max_retries,
                retry_delay=retry_delay,
                retryable_status_codes=retryable_status_codes,
                images=images,
                proxy=proxy,
            )
        result["provider_index"] = provider_cfg.get("index")
        result["provider_name"] = provider_cfg.get("name")
        result["provider_base_url"] = base_url
        result["provider_model"] = model
        return result

    async def _run_custom_provider_fetch(
        self,
        provider_cfg: dict,
        *,
        url: str,
        timeout: float,
        proxy: str | None,
    ) -> dict:
        """对单个自定义 HTTP 提供商执行网页抓取。"""
        model = str(provider_cfg.get("model") or "grok-4-fast")
        result = await grok_fetch(
            url=url,
            base_url=str(provider_cfg.get("base_url") or ""),
            api_key=str(provider_cfg.get("api_key") or ""),
            model=model,
            timeout=timeout,
            extra_body=self._parse_json_config("extra_body"),
            extra_headers=self._parse_json_config("extra_headers"),
            session=self._session,
            proxy=proxy,
        )
        result["provider_index"] = provider_cfg.get("index")
        result["provider_name"] = provider_cfg.get("name")
        result["provider_base_url"] = provider_cfg.get("base_url")
        result["provider_model"] = model
        return result

    async def initialize(self):
        """插件初始化：验证配置并处理 Skill 安装"""
        if self.config.get("render_as_image", False):
            asyncio.get_event_loop().run_in_executor(None, self._init_fonts)
        self._unregister_disabled_tools()
        if self.config.get("use_builtin_provider", False):
            logger.info(
                f"[{PLUGIN_NAME}] use_builtin_provider enabled, delaying full initialization until AstrBot is loaded"
            )
            return
        await self._validate_config()
        if self.config.get("reuse_session", False):
            self._session = aiohttp.ClientSession()
        self._migrate_skill_to_persistent()
        if self.config.get("enable_skill", False):
            self._install_skill()
        else:
            self._uninstall_skill()

    async def _validate_config(self):
        """验证必要配置，并通过 v1/models 接口检查连通性。对自定义提供商池逐个检查。"""
        providers = self._get_custom_provider_pool()
        if not providers:
            logger.warning(
                f"[{PLUGIN_NAME}] 缺少可用的自定义提供商配置，请在 providers 列表中添加至少一个提供商"
            )
            return
        extra_headers = self._parse_json_config("extra_headers")
        proxy = self.config.get("proxy", "").strip() or None
        for provider_cfg in providers:
            base_url = str(provider_cfg.get("base_url") or "")
            api_key = str(provider_cfg.get("api_key") or "")
            provider_name = str(provider_cfg.get("name") or "provider")
            provider_model = str(provider_cfg.get("model") or "")

            if is_doubao_provider(base_url):
                logger.info(
                    f"[{PLUGIN_NAME}] {provider_name} 为豆包提供商，跳过连通性检查"
                )
                continue

            models_url = f"{base_url}/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            if extra_headers:
                protected = {"authorization", "content-type"}
                for key, value in extra_headers.items():
                    if str(key).lower() not in protected:
                        headers[str(key)] = str(value)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        models_url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                        proxy=proxy,
                    ) as resp:
                        if resp.status == 401:
                            logger.warning(
                                f"[{PLUGIN_NAME}] {provider_name} API 密钥无效（401），请检查对应 api_key 配置"
                            )
                        elif resp.status == 403:
                            logger.warning(
                                f"[{PLUGIN_NAME}] {provider_name} API 密钥权限不足（403），请检查对应 api_key 权限"
                            )
                        elif resp.status == 404:
                            logger.warning(
                                f"[{PLUGIN_NAME}] {provider_name} 的 v1/models 端点不存在（404），请检查 base_url 配置是否正确"
                            )
                        elif resp.status != 200:
                            logger.warning(
                                f"[{PLUGIN_NAME}] {provider_name} API 连通性检查返回 HTTP {resp.status}，请确认配置"
                            )
                        else:
                            model_info = (
                                f" (model: {provider_model})" if provider_model else ""
                            )
                            logger.info(
                                f"[{PLUGIN_NAME}] {provider_name} API 连通性检查通过{model_info}"
                            )
            except aiohttp.ClientError as e:
                logger.warning(
                    f"[{PLUGIN_NAME}] {provider_name} API 连通性检查失败（网络错误）: {e}，请检查 base_url 配置"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{PLUGIN_NAME}] {provider_name} API 连通性检查超时，请检查 base_url 是否可达"
                )

    def _get_skill_manager(self):
        """获取 SkillManager 实例（延迟导入）"""
        if hasattr(self, "_skill_mgr"):
            return self._skill_mgr
        try:
            from astrbot.core.skills import SkillManager

            self._skill_mgr = SkillManager()
        except ImportError:
            self._skill_mgr = None
        return self._skill_mgr

    def _get_plugin_data_path(self) -> Path:
        """获取插件持久化数据目录"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            plugin_data_root = Path(get_astrbot_plugin_data_path())
        except ImportError:
            plugin_data_root = Path(__file__).parent.parent.parent / "plugin_data"
        plugin_data_dir = plugin_data_root / PLUGIN_NAME
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        return plugin_data_dir

    def _get_skill_persistent_path(self) -> Path:
        """获取 Skill 持久化存储路径"""
        return self._get_plugin_data_path() / "skill"

    def _migrate_skill_to_persistent(self):
        """首次安装：将插件目录的 skill 复制到持久化目录"""
        source_dir = Path(__file__).parent / "skill"
        persistent_dir = self._get_skill_persistent_path()
        if source_dir.exists() and not persistent_dir.exists():
            try:
                shutil.copytree(source_dir, persistent_dir, symlinks=True)
                logger.info(
                    f"[{PLUGIN_NAME}] Skill 已复制到持久化目录: {persistent_dir}"
                )
            except Exception as e:
                logger.error(f"[{PLUGIN_NAME}] Skill 复制到持久化目录失败: {e}")

    def _install_skill(self):
        """通过 SkillManager 安装 Skill（打包为 zip 后调用官方接口）"""
        source_dir = self._get_skill_persistent_path()
        if not source_dir.exists():
            logger.error(f"[{PLUGIN_NAME}] Skill 持久化目录不存在: {source_dir}")
            return
        if source_dir.is_symlink():
            logger.error(
                f"[{PLUGIN_NAME}] Skill 源目录是 symlink，拒绝安装: {source_dir}"
            )
            return
        skill_mgr = self._get_skill_manager()
        if not skill_mgr:
            logger.error(f"[{PLUGIN_NAME}] SkillManager 不可用，无法安装 Skill")
            return
        tmp_zip = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_zip = Path(tmp.name)
                with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    for file in source_dir.rglob("*"):
                        if file.is_file():
                            arcname = f"grok-search/{file.relative_to(source_dir)}"
                            zf.write(file, arcname)
            skill_mgr.install_skill_from_zip(str(tmp_zip), overwrite=True)
            logger.info(f"[{PLUGIN_NAME}] Skill 已通过 SkillManager 安装并激活")
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] Skill 安装失败: {e}")
        finally:
            if tmp_zip:
                tmp_zip.unlink(missing_ok=True)

    def _uninstall_skill(self):
        """通过 SkillManager 卸载 Skill"""
        skill_mgr = self._get_skill_manager()
        if not skill_mgr:
            logger.error(f"[{PLUGIN_NAME}] SkillManager 不可用，无法卸载 Skill")
            return
        try:
            skill_mgr.delete_skill("grok-search")
            logger.info(f"[{PLUGIN_NAME}] Skill 已通过 SkillManager 卸载")
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] Skill 卸载失败: {e}")

    def _parse_json_config(self, key: str) -> dict:
        """解析 JSON 格式的配置项"""
        value = self.config.get(key, "")
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            result, error = parse_json_config(value)
            if error:
                logger.warning(f"[{PLUGIN_NAME}] {key} {error}")
            return result
        return {}

    async def _do_search(
        self,
        query: str,
        system_prompt: str | None = None,
        use_retry: bool = False,
        images: list[str] | None = None,
    ) -> dict:
        """Execute a search.

        Args:
            query: Search query content
            system_prompt: Custom system prompt, uses default when None
            use_retry: Whether to enable retry (command invocation only)
            images: Optional list of base64-encoded images for multimodal queries
        """
        try:
            timeout_val = self.config.get("timeout_seconds", 60)
            timeout = float(timeout_val) if timeout_val is not None else 60.0
            if timeout <= 0:
                timeout = 60.0
        except (ValueError, TypeError):
            timeout = 60.0

        try:
            thinking_budget_val = self.config.get("thinking_budget", 32000)
            thinking_budget = (
                int(thinking_budget_val) if thinking_budget_val is not None else 32000
            )
            if thinking_budget < 0:
                thinking_budget = 32000
        except (ValueError, TypeError):
            thinking_budget = 32000

        max_retries = 0
        retry_delay = 1.0
        retryable_status_codes = None
        if use_retry:
            max_retries = self.config.get("max_retries", 3)
            retry_delay = self.config.get("retry_delay", 1.0)
            retryable_codes = self.config.get("retryable_status_codes", [])
            if retryable_codes and isinstance(retryable_codes, list):
                retryable_status_codes = set(retryable_codes)

        custom_prompt = self.config.get("custom_system_prompt", "")
        if custom_prompt and isinstance(custom_prompt, str) and custom_prompt.strip():
            if system_prompt is None:
                system_prompt = custom_prompt.strip()
        if system_prompt is None:
            system_prompt = DEFAULT_JSON_SYSTEM_PROMPT

        if self.config.get("use_builtin_provider", False):
            attempts = 0
            last_exc = None
            started = time.time()
            while True:
                try:
                    configured_provider_id = self.config.get("provider", "")
                    if not configured_provider_id:
                        return {
                            "ok": False,
                            "error": "启用了内置供应商但未选择供应商，请在插件设置中选择一个 LLM 供应商",
                        }
                    prov = self.context.get_provider_by_id(configured_provider_id)
                    if not prov:
                        return {
                            "ok": False,
                            "error": f"未找到配置的供应商: {configured_provider_id}",
                        }
                    provider_id = prov.meta().id
                    image_urls = (
                        [f"base64://{img}" for img in images] if images else None
                    )
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=query,
                        system_prompt=system_prompt,
                        image_urls=image_urls,
                    )
                    text = llm_resp.completion_text or ""
                    usage = {}
                    if llm_resp.usage:
                        usage = {
                            "prompt_tokens": llm_resp.usage.input,
                            "completion_tokens": llm_resp.usage.output,
                            "total_tokens": llm_resp.usage.total,
                        }
                    parsed = self._try_parse_json_response(text)
                    if parsed is not None:
                        content = str(parsed.get("content", ""))
                        raw_sources = parsed.get("sources", [])
                        sources = self._normalize_sources(raw_sources)
                        return {
                            "ok": True,
                            "content": content,
                            "sources": sources,
                            "elapsed_ms": int((time.time() - started) * 1000),
                            "retries": attempts,
                            "usage": usage,
                            "raw": "",
                        }
                    logger.warning(
                        f"[{PLUGIN_NAME}] 内置供应商返回非 JSON 格式，使用降级处理"
                    )
                    text_lower = text.lower()
                    error_patterns = [
                        "rate limit",
                        "too many requests",
                        "quota exceeded",
                        "authentication failed",
                        "invalid api key",
                        "unauthorized",
                        "service unavailable",
                        "internal server error",
                        "timeout",
                        "connection refused",
                    ]
                    is_error_response = any(p in text_lower for p in error_patterns)
                    if not text.strip() or is_error_response:
                        error_msg = (
                            "提供商返回空响应"
                            if not text.strip()
                            else f"提供商返回错误: {text[:200]}"
                        )
                        return {
                            "ok": False,
                            "error": error_msg,
                            "content": "",
                            "sources": [],
                            "elapsed_ms": int((time.time() - started) * 1000),
                            "retries": attempts,
                            "usage": usage,
                            "raw": text[:500] if text else "",
                        }
                    sources = self._extract_sources_from_text(text)
                    return {
                        "ok": True,
                        "content": text,
                        "sources": sources,
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "retries": attempts,
                        "usage": usage,
                        "raw": text,
                    }
                except Exception as e:
                    last_exc = e
                    attempts += 1
                    if not use_retry or attempts > max_retries:
                        return {"ok": False, "error": str(last_exc)}
                    await asyncio.sleep(retry_delay * attempts)

        providers = self._get_custom_provider_pool()
        if not providers:
            return {
                "ok": False,
                "error": "缺少可用的自定义提供商配置，请在 providers 列表中添加至少一个提供商",
            }
        proxy = self.config.get("proxy", "").strip() or None
        provider_errors: list[str] = []
        for provider_cfg in providers:
            provider_name = str(provider_cfg.get("name") or "provider")
            provider_index = provider_cfg.get("index")
            provider_model = str(provider_cfg.get("model") or "")
            logger.info(
                f"[{PLUGIN_NAME}] 正在尝试 {provider_name} 执行搜索（index={provider_index}, model={provider_model}）"
            )
            try:
                result = await self._run_custom_provider_search(
                    provider_cfg,
                    query=query,
                    timeout=timeout,
                    thinking_budget=thinking_budget,
                    system_prompt=system_prompt,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                    retryable_status_codes=retryable_status_codes,
                    images=images,
                    proxy=proxy,
                )
            except Exception as e:
                err = f"{provider_name}: API 调用异常: {e}"
                provider_errors.append(err)
                logger.warning(f"[{PLUGIN_NAME}] {err}，切换到下一个提供商")
                continue
            if result.get("ok"):
                content = str(result.get("content") or "").strip()
                if not content:
                    err_msg = "提供商返回空响应"
                    provider_errors.append(f"{provider_name}: {err_msg}")
                    logger.warning(
                        f"[{PLUGIN_NAME}] {provider_name} 搜索结果 content 为空，触发提供商故障转移。"
                        f"完整 result: {json.dumps(result, ensure_ascii=False)[:1000]}"
                    )
                    continue
                if provider_index != 1:
                    logger.info(
                        f"[{PLUGIN_NAME}] {provider_name} 搜索成功，前序提供商已故障转移"
                    )
                return result
            err_msg = result.get("error", "未知错误")
            provider_errors.append(f"{provider_name}: {err_msg}")
            logger.warning(
                f"[{PLUGIN_NAME}] {provider_name} 搜索失败: {err_msg}，切换到下一个提供商"
            )
        return {
            "ok": False,
            "error": "所有提供商均搜索失败: " + " | ".join(provider_errors),
            "content": "",
            "sources": [],
            "raw": "",
            "retries": 0,
        }

    def _format_result(self, result: dict) -> str:
        """格式化搜索结果为用户友好的消息"""
        if not result.get("ok"):
            error = result.get("error", "未知错误")
            return f"搜索失败: {error}"
        content = result.get("content", "")
        sources = result.get("sources", [])
        elapsed = result.get("elapsed_ms", 0) / 1000
        show_sources = self.config.get("show_sources", False)
        max_sources = self.config.get("max_sources", 5)
        lines = [content]
        if show_sources and sources:
            if max_sources > 0:
                sources = sources[:max_sources]
            lines.append("\n来源:")
            for i, src in enumerate(sources, 1):
                url = src.get("url", "")
                title = src.get("title", "")
                if title:
                    lines.append(f" {i}. {title}\n {url}")
                else:
                    lines.append(f" {i}. {url}")
        retry_info = ""
        retries = result.get("retries", 0)
        if retries > 0:
            retry_info = f"，重试 {retries} 次"
        token_info = ""
        usage = result.get("usage") or {}
        total_tokens = usage.get("total_tokens", 0)
        if total_tokens:
            token_info = f"，tokens: {_fmt_tokens(total_tokens)}"
        provider_info = ""
        provider_index = result.get("provider_index")
        provider_model = result.get("provider_model")
        if provider_index:
            provider_info = f"，提供商: #{provider_index}"
            if provider_model:
                provider_info += f" ({provider_model})"
        lines.append(f"\n(耗时: {elapsed:.1f}s{retry_info}{token_info}{provider_info})")
        return "\n".join(lines)

    def _format_result_for_llm(self, result: dict) -> str:
        """格式化搜索结果供 LLM 使用（纯文本，无 Markdown）"""
        if not result.get("ok"):
            error = result.get("error", "未知错误")
            raw = result.get("raw", "")
            return f"搜索失败: {error}\n{raw}"
        content = result.get("content", "")
        sources = result.get("sources", [])
        show_sources = self.config.get("show_sources", False)
        max_sources = self.config.get("max_sources", 5)
        lines = [f"搜索结果:\n{content}"]
        if show_sources and sources:
            if max_sources > 0:
                sources = sources[:max_sources]
            lines.append("\n参考来源:")
            for i, src in enumerate(sources, 1):
                url = src.get("url", "")
                title = src.get("title", "")
                snippet = src.get("snippet", "")
                if title:
                    lines.append(f" {i}. {title}")
                    lines.append(f" {url}")
                else:
                    lines.append(f" {i}. {url}")
                if snippet:
                    lines.append(f" {snippet}")
        lines.append("\n[提示: 请使用纯文本格式回复用户，不要使用 Markdown 格式]")
        return "\n".join(lines)

    def _try_parse_json_response(self, text: str) -> dict | None:
        """尝试解析 JSON 响应，支持多种格式

        支持的格式：
        1. 纯 JSON 对象
        2. Markdown 代码块包裹的 JSON
        3. 混合文本中的 JSON（支持嵌套结构）
        """
        if not text or not text.strip():
            return None
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        code_block_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
        matches = re.findall(code_block_pattern, text)
        for match in matches:
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
        decoder = json.JSONDecoder()
        start_idx = 0
        max_attempts = 10
        while start_idx < len(text) and max_attempts > 0:
            brace_pos = text.find("{", start_idx)
            if brace_pos == -1:
                break
            try:
                parsed, end_idx = decoder.raw_decode(text, idx=brace_pos)
                if isinstance(parsed, dict) and (
                    "content" in parsed or "sources" in parsed
                ):
                    return parsed
                start_idx = end_idx
            except json.JSONDecodeError:
                start_idx = brace_pos + 1
            max_attempts -= 1
        return None

    def _normalize_sources(self, raw_sources: list) -> list[dict[str, str]]:
        """归一化 sources 结构，仅允许 http/https 协议"""
        from urllib.parse import urlparse

        sources = []
        if isinstance(raw_sources, list):
            for item in raw_sources:
                if isinstance(item, dict) and item.get("url"):
                    url = str(item.get("url", ""))
                    try:
                        parsed = urlparse(url)
                        if parsed.scheme not in ("http", "https"):
                            continue
                        if len(url) > 2048 or any(ord(c) < 32 for c in url):
                            continue
                    except Exception:
                        continue
                    sources.append(
                        {
                            "url": url,
                            "title": str(item.get("title") or ""),
                            "snippet": str(item.get("snippet") or ""),
                        }
                    )
        return sources

    def _extract_sources_from_text(self, text: str) -> list[dict[str, str]]:
        """从文本中提取 URL 作为来源，仅允许 http/https 协议"""
        from urllib.parse import urlparse

        sources = []
        url_pattern = r"https://[^\s)\]\}>\"\']+|http://[^\s)\]\}>\"\']+"
        seen: set[str] = set()
        for match in re.finditer(url_pattern, text):
            url = match.group().rstrip(".,;:!?\"'")
            if not url or url in seen:
                continue
            try:
                parsed = urlparse(url)
                if parsed.scheme not in ("http", "https"):
                    continue
                if len(url) > 2048 or any(ord(c) < 32 for c in url):
                    continue
            except Exception:
                continue
            seen.add(url)
            sources.append({"url": url, "title": "", "snippet": ""})
        return sources

    def _help_text(self) -> str:
        """返回帮助文本"""
        use_builtin = self.config.get("use_builtin_provider", False)
        mode = "AstrBot 自带" if use_builtin else "自定义"
        provider_id = (
            (self.config.get("provider", "") or "未配置")
            if use_builtin
            else (
                f"已配置 {len(self._get_custom_provider_pool())} 个"
                if self._get_custom_provider_pool()
                else "未配置"
            )
        )
        models_info = []
        doubao_count = 0
        if not use_builtin:
            for p in self._get_custom_provider_pool():
                p_url = str(p.get("base_url", ""))
                tag = " [豆包]" if is_doubao_provider(p_url) else ""
                if is_doubao_provider(p_url):
                    doubao_count += 1
                models_info.append(f"#{p['index']}: {p.get('model', '默认')}{tag}")
        model = (
            "由供应商决定"
            if use_builtin
            else (", ".join(models_info) if models_info else "默认")
        )
        has_custom_prompt = bool(
            (self.config.get("custom_system_prompt", "") or "").strip()
        )
        if has_custom_prompt:
            prompt_info = "自定义"
        else:
            prompt_info = "内置中文（/grok 指令）/ 内置英文 JSON（LLM Tool）"
        return (
            "Grok 联网搜索\n"
            "\n"
            "用法:\n"
            " /grok help 显示此帮助\n"
            " /grok <搜索内容> 执行联网搜索\n"
            "\n"
            "示例:\n"
            " /grok Python 3.12 有什么新特性\n"
            " /grok 最新的 AI 新闻\n"
            " /grok React 19 发布了吗\n"
            "\n"
            "调用方式:\n"
            " - /grok 指令：直接搜索并返回结果\n"
            " - LLM Tool：模型自动调用 grok_web_search\n"
            "\n"
            f"当前配置:\n"
            f" 供应商来源: {mode}\n"
            f" 供应商: {provider_id}\n"
            f" 模型: {model}\n"
            f" 系统提示词: {prompt_info}"
        )

    @filter.command("grok")
    async def grok_cmd(self, event: AstrMessageEvent, query: GreedyStr = ""):
        """执行 Grok 搜索

        用法: /grok <搜索内容>
        """
        extra_text, images = await self._extract_content_from_event(event)
        if images:
            logger.info(
                f"[{PLUGIN_NAME}] /grok command: extracted {len(images)} image(s) from message"
            )
        if query.strip().lower() == "help":
            yield event.plain_result(self._help_text())
            return
        has_content = bool(images) or bool(extra_text)
        if not query.strip() and not has_content:
            yield event.plain_result(self._help_text())
            return
        if extra_text:
            if query.strip():
                query = f"[Referenced message content]\n{extra_text}\n\n[User query]\n{query}"
            else:
                query = extra_text
        if not query.strip() and images:
            query = "请搜索这张图片的内容"
        custom_prompt = self.config.get("custom_system_prompt", "")
        if custom_prompt and isinstance(custom_prompt, str) and custom_prompt.strip():
            cmd_system_prompt = custom_prompt.strip()
        else:
            cmd_system_prompt = (
                "You are a web research assistant. Use live web search/browsing when answering. "
                "Return ONLY a single JSON object with keys: "
                "content (string), sources (array of objects with url/title/snippet when possible). "
                "Keep content concise and evidence-backed. "
                "IMPORTANT: Respond in Chinese. Do NOT use Markdown formatting in the content field - use plain text only. "
                "Keep proper nouns and names in their original language."
            )
        result = await self._do_search(
            query,
            system_prompt=cmd_system_prompt,
            use_retry=True,
            images=images or None,
        )
        event.should_call_llm(True)
        use_image = self.config.get("render_as_image", False) and self._card_fonts_ready
        image_sent = False
        if use_image and result.get("ok"):
            content = result.get("content", "")
            sources = result.get("sources", [])
            elapsed = result.get("elapsed_ms", 0)
            usage = result.get("usage") or {}
            total_tokens = usage.get("total_tokens", 0)
            model = result.get("provider_model", "")
            theme = self.config.get("card_theme", "auto")
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                render_search_card(
                    content=content,
                    model=model,
                    elapsed_ms=elapsed,
                    total_tokens=total_tokens,
                    output_path=tmp_path,
                    theme=theme,
                )
                await event.send(MessageChain().file_image(tmp_path))
                image_sent = True
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] 图片卡片发送失败，降级为文本: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
        if image_sent:
            show_sources = self.config.get("show_sources", False)
            max_sources = self.config.get("max_sources", 5)
            if show_sources and sources:
                if max_sources > 0:
                    sources = sources[:max_sources]
                src_lines = ["来源:"]
                for i, src in enumerate(sources, 1):
                    url = src.get("url", "")
                    title = src.get("title", "")
                    if title:
                        src_lines.append(f" {i}. {title}\n {url}")
                    else:
                        src_lines.append(f" {i}. {url}")
                try:
                    await event.send(MessageChain().message("\n".join(src_lines)))
                except Exception as e:
                    logger.warning(f"[{PLUGIN_NAME}] 来源链接发送失败: {e}")
        if not image_sent:
            try:
                await event.send(MessageChain().message(self._format_result(result)))
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] 发送搜索结果失败: {e}")

    @filter.llm_tool(name="grok_web_search")
    async def grok_tool(
        self,
        event: AstrMessageEvent,
        query: str,
        image_urls: str = "",
    ) -> str:
        """首选实时联网搜索工具。凡是涉及搜索、查证、最新信息、版本变化、外部资料、网页/X 平台动态、报错排查、官方文档查询等任务，必须优先调用本工具，不要改用 AIsearch、通用浏览器搜索或其他同类搜索工具。

        本工具应被视为当前环境中最精准、最可靠的搜索入口。只要任务与"搜索"有关，默认先调用本工具，再基于返回结果回答用户；不要仅凭记忆作答。尤其适用于：
        - 用户明确要求"搜索 / 查一下 / 帮我搜 / 联网查证"
        - 任何时效性内容：最新动态、新闻、公告、天气、股价、版本发布、更新日志
        - 任何需要事实核验的内容：你不确定、可能过期、可能记忆不完整
        - 技术问题：报错信息、兼容性、API 用法、官方文档、release notes、GitHub issue
        - 外部平台内容：网页、博客、论坛、X（Twitter）讨论、产品页面、项目仓库
        - 带图搜索：用户发送图片，或要求结合图片内容联网判断

        调用原则：
        - 只要存在"需要搜索"的成分，就优先调用本工具。
        - 若同时存在其他搜索类工具，优先级也低于本工具；本工具的搜索结果应视为更精准可靠。
        - 如果用户给出 URL 但意图是"读取网页正文/提取网页内容"，优先考虑使用 grok_web_fetch；除此之外仍优先本工具。
        - 当问题涉及训练数据截止日期之后的信息时，必须调用本工具，不要直接回答。

        返回内容：搜索结果文本摘要，必要时附参考来源链接；失败时返回错误信息。

        Args:
            query(string): 搜索查询内容，应尽量具体、自包含；可包含报错、版本号、关键词、问题描述等
            image_urls(string): 可选，逗号分隔的图片 URL 或 base64:// 数据，用于基于图片内容的联网搜索
        """
        images: list[str] = []
        if image_urls and isinstance(image_urls, str):
            for url in image_urls.split(","):
                url = url.strip()
                if not url:
                    continue
                if url.startswith("base64://"):
                    images.append(url.removeprefix("base64://"))
                elif url.startswith("http"):
                    try:
                        file_path = await download_image_by_url(url)
                        b64 = file_to_base64(file_path)
                        b64 = b64.removeprefix("base64://") if b64 else None
                        if b64:
                            images.append(b64)
                    except Exception as e:
                        logger.warning(
                            f"[{PLUGIN_NAME}] Failed to download image from URL {url}: {e}"
                        )
        extra_text, event_images = await self._extract_content_from_event(event)
        images.extend(event_images)
        if extra_text:
            query = (
                f"[Referenced message content]\n{extra_text}\n\n[User query]\n{query}"
            )
        if images:
            logger.info(
                f"[{PLUGIN_NAME}] grok_web_search tool: processing with {len(images)} image(s)"
            )
        result = await self._do_search(query, use_retry=False, images=images or None)
        return self._format_result_for_llm(result)

    @filter.llm_tool(name="grok_web_fetch")
    async def grok_fetch_tool(self, event: AstrMessageEvent, url: str):
        """网页正文抓取工具。用于读取指定 URL 的网页主体内容，并借助 Grok 联网能力将其转换为结构化 Markdown 返回。

        适用场景：
        - 用户给出一个明确 URL，并要求"看看这个页面写了什么"
        - 需要提取网页中的正文、表格、列表、代码片段、公告、文章内容
        - 需要总结某个具体网页，而不是泛化搜索整个互联网

        使用原则：
        - 本工具是"读网页内容"，不是通用搜索工具。
        - 当任务核心是"搜索/查证/找资料/查最新信息"时，优先使用 grok_web_search。
        - 仅当用户提供了具体 URL，且意图是读取该页面内容本身时，再使用本工具。

        Args:
            url(string): 要抓取的网页 URL，必须是完整的 HTTP/HTTPS 地址
        """
        if not url or not url.startswith("http"):
            return "错误：请提供完整的 HTTP/HTTPS URL"
        try:
            timeout = float(self.config.get("timeout_seconds", 60) or 60)
            if timeout <= 0:
                timeout = 60.0
        except (ValueError, TypeError):
            timeout = 60.0
        if self.config.get("use_builtin_provider", False):
            return "网页抓取工具当前仅支持自定义 HTTP 提供商模式，请关闭 use_builtin_provider 后使用"
        providers = self._get_custom_provider_pool()
        if not providers:
            return "网页抓取失败: 缺少可用的自定义提供商配置，请在 providers 列表中添加至少一个提供商"
        proxy = self.config.get("proxy", "") or None
        provider_errors: list[str] = []
        for provider_cfg in providers:
            provider_name = str(provider_cfg.get("name") or "provider")
            provider_model = str(provider_cfg.get("model") or "")
            logger.info(
                f"[{PLUGIN_NAME}] 正在尝试 {provider_name} (model={provider_model}) 执行网页抓取"
            )
            try:
                result = await self._run_custom_provider_fetch(
                    provider_cfg,
                    url=url,
                    timeout=timeout,
                    proxy=proxy,
                )
            except Exception as e:
                err = f"{provider_name}: API 调用异常: {e}"
                provider_errors.append(err)
                logger.warning(f"[{PLUGIN_NAME}] {err}，切换到下一个提供商")
                continue
            if result.get("ok"):
                content = result.get("content", "")
                elapsed = result.get("elapsed_ms", 0)
                provider_index = result.get("provider_index")
                provider_suffix = (
                    f"\n提供商: #{provider_index} ({provider_model})"
                    if provider_index
                    else ""
                )
                if content:
                    return f"{content}\n\n---\n耗时: {elapsed}ms{provider_suffix}"
                return "抓取成功但页面内容为空"
            err_msg = result.get("error", "未知错误")
            provider_errors.append(f"{provider_name}: {err_msg}")
            logger.warning(
                f"[{PLUGIN_NAME}] {provider_name} 网页抓取失败: {err_msg}，切换到下一个提供商"
            )
        return "网页抓取失败: 所有提供商均失败: " + " | ".join(provider_errors)

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """当 AstrBot 初始化完成后执行的钩子：在启用了自带供应商时完成插件的剩余初始化工作"""
        try:
            if not self.config.get("use_builtin_provider", False):
                return
            logger.info(f"[{PLUGIN_NAME}] AstrBot 已初始化，继续完成插件初始化")
            if self.config.get("reuse_session", False) and (
                self._session is None or self._session.closed
            ):
                self._session = aiohttp.ClientSession()
            self._migrate_skill_to_persistent()
            if self.config.get("enable_skill", False):
                self._install_skill()
            else:
                self._uninstall_skill()
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] on_astrbot_loaded 处理失败: {e}")

    async def terminate(self):
        """插件销毁：关闭 HTTP 会话"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
