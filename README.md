# Grok 联网搜索 (astrbot_plugin_grok_web_search)

通过 Grok / 豆包 API 进行实时联网搜索，返回综合答案和来源链接。支持多模态图片搜索、网页内容抓取、网页图片自动发送。

## ✨ 能做什么

- **联网搜索** — `/grok` 指令或 LLM 自动调用，支持网页和 X (Twitter) 平台
- **图片搜索** — 发送图片附带 `/grok` 进行多模态搜索
- **网页抓取** — 抓取 URL 内容并转为结构化 Markdown，可自动提取网页图片发送
- **文件下载** — LLM 可下载并发送图片或文档（PDF/Word/Excel 等），单文件 ≤20MB
- **搜索结果卡片** — 可选将结果渲染为精美图片卡片，支持日/夜自动主题
- **双链路路由** — 速度链路用于即时搜索，质量链路用于深度研究，互不干扰
- **豆包搜索** — 在 providers 中加入火山方舟端点，自动使用豆包 Responses API 搜索

## 🚀 安装

1. AstrBot 插件市场搜索 `Grok联网搜索` 安装
2. 或从链接安装：`https://github.com/Inoryu7z/astrbot_plugin_grok_web_search`

**环境要求**：Python ≥ 3.10，AstrBot ≥ v4.9.2（使用 Skill 功能需 v4.13.2+）

## 📋 使用

### 指令

```
/grok Python 3.12 有什么新特性
/grok 最新的 AI 新闻
/grok help                              # 查看当前配置
[图片] /grok 这张图片里有什么？          # 多模态图片搜索
```

### LLM 自动调用

LLM 在需要实时信息时会自动调用搜索工具。以下用户消息都会触发对应工具：

- "搜一下最近的 AI 新闻" → `grok_web_search`
- "帮我看看 https://example.com 这个页面" → `grok_web_fetch`
- "搜俩洛丽塔裙衣服来" → 搜索 + 网页抓取 + 图片发送
- "搜个XX图片发给我" → 搜索 + 文件下载

需要深度研究时可说"用质量链搜"切换到质量优先提供商。

## ⚙️ 关键配置

### 提供商（`providers` 列表）

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `base_url` | ✓ | Grok: `https://api.x.ai`；豆包: `https://ark.cn-beijing.volces.com` |
| `api_key` | ✓ | API 密钥 |
| `id` |  | 提供商唯一 ID（用于链路引用），如 `grok-fast`、`doubao-pro` |
| `model` |  | 留空使用默认（grok-4-fast / doubao-seed-2-0-pro-260215） |
| `reasoning_effort` |  | 豆包思考强度：`minimal`/`low`/`medium`/`high`，默认 `medium` |
| `fetch_bot_id` |  | 豆包网页解析应用 ID（用于网页抓取） |

> 也可使用 AstrBot 自带供应商（`use_builtin_provider: true`），但自定义 providers 支持多提供商故障转移和链路配置。

### 链路配置

| 配置项 | 说明 |
|--------|------|
| `speed_chain` | 速度优先链路（即时搜索场景），填写 providers 中的 ID |
| `quality_chain` | 质量优先链路（深度研究场景），填写 providers 中的 ID |

未配置时回退到 `providers` 列表原始顺序，完全向后兼容。

### 输出与卡片

| 配置项 | 说明 |
|--------|------|
| `render_as_image` | 将搜索结果渲染为图片卡片（默认关闭） |
| `card_theme` | 卡片主题：auto（按时间自动）/ dark / light |
| `show_sources` | 显示来源 URL（默认关闭） |
| `max_sources` | 最大来源数量（默认 5，0 表示不限制） |
| `custom_system_prompt` | 自定义系统提示词 |

启用卡片渲染后，首次使用会自动下载 Sarasa Term Slab SC 字体；也可在字体目录放入自定义 `.ttf` 替代。

### 豆包专用

| 配置项 | 说明 |
|--------|------|
| `doubao_sources` | 附加搜索来源：`douyin` / `moji` / `toutiao` |
| `doubao_user_location` | 地理位置优化（地域相关搜索），格式 `{"country":"中国","region":"浙江","city":"杭州"}` |
| `doubao_max_images` | 网页抓取时最大发送图片数量（默认 3，0 表示不发送） |

### 功能开关

| 配置项 | 说明 |
|--------|------|
| `enable_fetch` | 启用网页抓取工具（默认关闭） |
| `enable_skill` | 启用 Skill 脚本模式（启用后所有 LLM Tool 不注册） |
| `use_responses_api` | 使用 xAI Responses API（仅官方 API 支持） |

### 豆包网页解析（可选）

如需使用豆包网页抓取能力，需在[豆包控制台](https://console.volcengine.com/ark)创建带**网页解析插件**的零代码应用（注意：网页解析插件不能与联网插件同时开启），获取应用 ID（格式如 `bot-20xxxxxxxx`）后填入 providers 的 `fetch_bot_id` 字段。

未配置 `fetch_bot_id` 的豆包提供商在网页抓取时会被跳过，回退到 Grok 提供商。

完整配置项请查阅 AstrBot 配置界面。

## 🙏 致谢

- [grok-skill](https://github.com/Frankieli123/grok-skill) — 原始 Skill 脚本项目，感谢 [@a3180623](https://linux.do/u/a3180623/summary) 的开源贡献
- [GrokSearch](https://github.com/GuDaStudio/GrokSearch) — 网页内容抓取功能参考，感谢 [GuDa Studio](https://github.com/GuDaStudio)
- [@Stonesan233](https://github.com/Stonesan233) — PR [#5](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search/pull/5) 贡献了 Responses API 支持、X 搜索和代理配置

## 📄 许可证

AGPL-3.0 License
