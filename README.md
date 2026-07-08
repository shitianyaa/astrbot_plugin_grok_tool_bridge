# Grok 工具桥接

`astrbot_plugin_grok_tool_bridge` 是 AstrBot 的模型能力兼容层。

它面向 Grok / xAI 等“回复质量好、可能有原生搜索能力，但在 AstrBot 中不能稳定调用工具”的模型：插件先用路由模型判断是否需要工具；需要时复用 AstrBot 内置工具执行；最后把工具结果交回 Grok 或配置的最终模型生成回复。插件不修改 AstrBot Core，也不重写内置工具。

## 功能

- 自动桥接：在目标 Grok/xAI 会话触发 LLM 前判断是否需要工具。
- 触发网关：默认群聊需 @bot / 唤醒词 / 回复 bot 才进入自动桥接，私聊不受影响（`require_at_or_wake`）。
- 手动桥接：使用 `/grok工具 <需求>`、`/groktool <需求>` 或 `/工具桥接 <需求>` 明确触发。
- 主动任务：接管 `future_task` 到点后的 cron/background 事件，由工具模型负责取材料和发送。
- 最近文件：缓存当前会话最近一个白名单附件，支持“总结刚才的文件”这类后续引用。
- 定时文件：创建引用最近文件的 future_task 时保存稳定副本，避免 TTL 或重启后文件路径丢失。
- 诊断命令：本地查看 provider、工具白名单、最近文件、主动任务状态，不调用外部模型。
- 原生工具透传：为未来 Grok provider 稳定支持工具调用预留保守跳过模式。

默认自动工具白名单：

- `future_task`
- `astr_kb_search`
- `astrbot_file_read_tool`
- `astrbot_grep_tool`

手动命令默认额外允许：

- `send_message_to_user`
- `astrbot_upload_file`
- `astrbot_download_file`

主动任务默认额外保留：

- `send_message_to_user`

## 工作流

普通自动桥接：

```text
收到 LLM 请求
-> 是否 @bot 或唤醒词（require_at_or_wake，群聊默认开启）
-> 当前 Provider 是否匹配 Grok/xAI
-> 是否需要原生工具透传
-> 路由模型输出 JSON 决策
-> no_tool：放行给原 Grok
-> tool_call：执行白名单内置工具
-> Grok / final_provider 整理工具结果
-> 插件发送最终回复
```

主动任务：

```text
future_task 到点
-> 识别 cron_job/background_task_result
-> ProactivePlanner 选择执行模式
-> delivery_only：简单提醒直接发送
-> grok_first：天气/新闻等实时信息交给当前会话模型生成内容
-> tool_first：文件/知识库/grep 先用工具模型准备材料
-> hybrid：先取工具材料，再让当前会话模型结合实时能力生成
-> 最后由工具模型优先调用 send_message_to_user
-> 如果工具模型没有发送或异常，插件直接发送准备好的文本兜底
-> 插件停止默认 Agent 继续处理，避免同一主动任务重复回复
```

## 使用示例

自动模式：

```text
明天早上 9 点提醒我交日报
读取 README.md，总结内容
搜索刚才文件里关于早餐的段落
```

手动模式：

```text
/grok工具 明天早上 9 点提醒我交日报
/grok工具 查一下知识库里关于群规的内容
/grok工具 读取 README.md 总结一下
/grok工具 在项目里搜 send_message_to_user
```

最近文件：

```text
<先发送 txt / md / pdf / docx 等白名单附件>
总结我刚才发的文件
1分钟后总结我刚上传的文件
```

如果任务创建时引用了最近文件，插件会把文件复制到插件数据目录的 `scheduled_files`，并把稳定路径写进 future_task 的 note。到点后工具模型会读取这个稳定路径。

## 诊断命令

诊断命令不会调用 LLM：

```text
/grok工具 status
/grok工具 recent-file
/grok工具 proactive-status
/grok工具 clear-cache
```

- `status`：显示当前 provider、router/final/proactive provider、工具白名单和配置告警。
- `recent-file`：显示当前会话最近文件名、大小、是否可用和过期时间。
- `proactive-status`：显示主动任务开关、策略、发送工具和定时文件数量。
- `clear-cache`：清理当前会话最近文件缓存，并清理已过期的定时文件副本。

## 配置建议

- `router_provider_id`：建议选择稳定输出 JSON 的模型。
- `require_at_or_wake`：默认开启；群聊需 @bot / 唤醒词 / 回复 bot 才触发自动桥接，私聊不受影响。关闭后恢复对所有命中的 Grok 请求判断桥接。
- `final_provider_id`：留空时使用当前会话模型，通常就是 Grok。
- `proactive_agent_provider_id`：主动任务必须配置一个稳定支持 tools/function calling 的模型。
- `proactive_mode_policy`：默认 `auto`；可强制为 `grok_first`、`tool_first`、`hybrid` 或 `delivery_only`。
- `target_provider_keywords`：默认 `grok`、`xai`；留空表示不自动桥接任何 Provider。
- `enabled_auto_tools`：自动模式白名单，建议保持默认。
- `enabled_manual_tools`：手动命令白名单，默认比自动模式多 `send_message_to_user`、上传和下载工具。
- `enabled_proactive_tools`：主动任务白名单，建议保留 `send_message_to_user`。
- `recent_file_allowed_extensions` / `recent_file_max_size_kb`：限制可缓存附件类型和大小。
- `scheduled_file_retention_days`：引用最近文件创建 future_task 时，稳定副本保留天数，默认 7 天。
- `auto_process_uploaded_text_file`：单独发送文件时是否自动总结，默认关闭，避免误触发和影响其他文件插件。
- `native_tool_passthrough_mode`：默认 `off`；未来 provider 明确支持原生工具时可设为 `auto` 或 `log_only`。
- `debug_mode`：开启后输出每步 provider、planner 模式、router 决策、工具参数、结果预览和 fallback 原因。

## 边界

- 不默认开放 shell、python、写文件、编辑文件、浏览器自动化等高风险工具。
- 不接管所有消息，只处理命中目标 provider 且确实需要工具的请求。
- 不把固定人格写入运行时提示词；主动任务会优先复用当前会话的人格提示和历史对话。
- 不实现独立联网搜索；实时信息优先交给 Grok / xAI 原生能力。
- 不删除未过期的定时文件副本，避免破坏已创建的未来任务。
- 主动任务优先走 `send_message_to_user`，但发送工具不可用或失败时会直接向原事件发送兜底文本，保证提醒可达。

## 平台支持

- 已声明支持：`aiocqhttp`
- 其他平台未验证，暂不在 `metadata.yaml` 中声明。

## 验证

```powershell
python -m json.tool _conf_schema.json
$files = @('main.py') + (Get-ChildItem core -Filter *.py | ForEach-Object { $_.FullName }); python -m py_compile $files
python -m pytest -q
```

可选质量检查：

```powershell
python -m ruff check .
python -m ruff format --check .
```
