# Changelog

## v1.5.3

**📊 搜索 token 消耗计入统一统计**

* 搜索与网页抓取的 token 消耗会上报给 token_router 插件统一统计。
* 计入与聊天相同的每日额度桶：插件与聊天合计达到限额时聊天照常顺延。
* 插件自身保持使用指定提供商，不会因满额被切换。

---

## v1.5.2

**🧠 豆包思考强度新增「不传入」选项**

* providers 的 `reasoning_effort` 新增 `none` 选项，选择后不向 API 传入思考强度参数。
* 用于兼容不支持思考强度参数的开源模型。

---

## v1.5.1

**🔗 LLM 工具支持链路选择**

* `grok_web_search` 工具新增 `prefer_quality` 参数。
* 默认 `false` 走速度链路；用户明确要求「用质量链 / 深度搜」时 LLM 设为 `true` 走质量链路。
* 普通搜索请求保持速度链，不影响即时搜索体验。

---

## v1.5.0

**🛣️ 双链路提供商路由上线**

**1. 🚀 速度链路与质量链路独立配置**

* 新增 `speed_chain`（即时搜索场景）和 `quality_chain`（后台研究场景）。
* 两条链路分别填写 providers 中的提供商 ID，互不干扰。
* 未配置时回退到 `providers` 列表原始顺序，完全向后兼容。

**2. 🏷️ 提供商 ID 字段**

* providers 模板新增 `id` 字段，用于链路引用。

**3. 🧠 豆包思考强度配置**

* providers 模板新增 `reasoning_effort` 字段（`minimal` / `low` / `medium` / `high`）。

**4. 📝 插件目录重命名**

* 目录名从 `astrbot_plugin_grok_web_search` 改为 `astrbot_plugin_grok_web_search_Inoryu7z`。

---

## v1.4.1

**🐛 中转商错误响应检测修复**

* 某些 API 中转商在请求不合法时返回 HTTP 200 + JSON，但 content 字段为错误信息。
* 此前被误判为搜索成功，阻止了 fallback 到下一个提供商。
* 现在能识别错误内容并自动切换。

---

## v1.4.0

**🌐 豆包网页抓取 + 文件下载工具**

**1. 🌐 豆包网页解析应用支持**

* 通过豆包 Bot API 调用带网页解析插件的零代码应用进行网页抓取。
* 配置 `fetch_bot_id` 字段即可启用，未配置时回退到 Grok。

**2. 🖼️ 网页图片自动发送**

* 网页抓取结果中的图片自动下载并发送给用户。
* 新增 `doubao_max_images` 控制最大发送数量（默认 3，0 表示不发送）。

**3. 📎 `grok_download_file` LLM 工具**

* 下载指定 URL 的文件并发送给用户。
* 支持图片（直接发送）和文档（PDF / Word / Excel 等）。
* 自动检测 Content-Type 修正文件类型，单文件限制 20MB。

**4. 🔧 `grok_web_fetch` 工具路由优化**

* 优先使用配置了 `fetch_bot_id` 的豆包提供商，回退到 Grok。

---

## v1.3.1

**🤖 豆包搜索集成 + 多项修复**

**1. 🤖 豆包 Responses API 搜索**

* 在 providers 中加入火山方舟端点，自动识别为豆包提供商。
* 支持附加搜索来源（抖音百科 / 墨迹天气 / 头条图文）。
* 支持地理位置优化（`doubao_user_location`）。
* 默认模型 `doubao-seed-2-0-pro-260215`。

**2. 🗑️ 移除旧版 provider 配置**

* 移除 `provider_2_*`、`provider_3_*` 等旧版字段，统一使用 `providers` 列表。

**3. 🔧 多项 Bug 修复**

* 豆包 URL 路径重复导致 404。
* Grok 思考模式下 `reasoning_content` 字段未被读取导致空响应。
* 豆包连通性检查缺失，填错 API Key 无早期反馈。

---

## v1.3.0

**🖼️ 搜索结果图片卡片渲染**

**1. 🖼️ 面板式图片卡片**

* 基于 Pillow 纯本地渲染，搜索结果渲染为精美卡片。
* 支持 Markdown 子集：标题、列表、代码块、引用、粗体、行内代码。

**2. 🌗 日 / 夜自动主题**

* `card_theme` 支持 `auto`（7:00-18:00 浅色）、`dark`、`light`。

**3. 🔤 字体自动下载**

* 首次使用时从清华镜像自动下载 Sarasa Term Slab SC 字体。
* 也可在字体目录放入自定义 `.ttf` 文件替代。

---

## v1.2.0

**⚡ Responses API + 网页抓取工具**

**1. ⚡ xAI Responses API 支持**

* 新增 `use_responses_api` 切换 Chat Completions / Responses API 模式。
* 同时支持 `web_search` 和 `x_search`，可搜索 X / Twitter 平台。

**2. 🌐 `grok_web_fetch` LLM 工具**

* 将 URL 转为结构化 Markdown，利用 Grok 联网能力实现。
* 新增 `enable_fetch` 开关，关闭时初始化阶段直接卸载工具。

**3. 🖼️ 图片搜索增强**

* 通过 PIL 或魔数字节识别图片格式，不支持的格式自动转换。

**4. ⏱️ 时间上下文注入**

* 搜索时自动注入当前日期、星期、时区，提升时效性查询准确度。

**5. 🔁 Retry-After 支持**

* 429 错误时优先使用服务端指定的等待时间。

**6. 🌐 HTTP 代理支持**

* 新增 `proxy` 配置项。

---

## v1.1.0

**🖼️ 多模态图片搜索**

* `/grok` 指令、LLM Tool、Skill 脚本均支持图片输入。
* `/grok` 指令自动提取消息中的图片（直接发送、回复带图消息、QQ 转发消息）。
* LLM Tool 新增 `image_urls` 参数。
* Skill 脚本新增 `--image-files` 参数。

---

<details>
<summary>历史版本</summary>

## v1.0.9

* 修复 `/grok` 指令关键词含空格时只取第一个词的问题。

## v1.0.8

* Skill 安装 / 卸载改用 AstrBot 官方 `SkillManager` 接口。

## v1.0.7

* 内置供应商返回非 JSON 时自动提取纯文本和 URL，不再直接报错。
* `/grok` 指令提示词改为英文指令 + JSON 格式 + 中文回复要求。
* URL 协议白名单校验，拒绝 `javascript:` / `data:` 等危险协议。

## v1.0.6

* 适配 AstrBot 插件元数据规范，新增 `astrbot_version` 和 `support_platforms` 字段。

## v1.0.5

* 新增 `use_builtin_provider` 支持使用 AstrBot 自带供应商。
* 新增 `max_retries` / `retry_delay` / `retryable_status_codes` 重试配置。
* 新增 `custom_system_prompt` 自定义系统提示词。
* `/grok help` 显示当前配置状态。

## v1.0.4

* 兼容 SSE 流式响应。
* 新增 `enable_thinking` / `thinking_budget` 思考模式配置。
* 默认模型从 `grok-4-expert` 改为 `grok-4-fast`。

## v1.0.3

* 新增 `reuse_session` 复用 HTTP 会话。
* 错误信息改为中文友好提示，包含具体原因和解决建议。

## v1.0.2

* 启用 Skill 时自动禁用 LLM Tool，避免 AI 重复调用。
* 新增 `show_sources` / `max_sources` 来源控制。

## v1.0.0

* `/grok` 指令、`grok_web_search` LLM Tool、Skill 脚本支持。
* GitHub Issue 模板 + CI 配置（ruff lint + format check）。

</details>
