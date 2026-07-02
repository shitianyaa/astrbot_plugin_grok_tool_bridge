# Grok 工具桥接

`astrbot_plugin_grok_tool_bridge` 为 Grok / xAI 等在 AstrBot 中不稳定调用函数工具的模型提供一层工具桥接。

它不会修改 Grok 的原生能力，也不会重新实现 AstrBot 内置工具。插件会在 AstrBot 准备请求目标模型前，先用一个路由模型判断是否需要工具；如果需要，就由插件调用 AstrBot 内置工具，再把工具结果交给 Grok 或配置的最终模型生成回复。

## 功能

- 自动模式：在 Grok 会话触发 LLM 前判断是否需要工具。
- 手动模式：使用 `/grok工具 <需求>` 明确触发桥接。
- 主动任务模式：`future_task` 到点后交给可自定义的工具执行助手处理并通知用户。
- 最近文件模式：缓存当前会话最近一个白名单文本文件，支持“总结刚才的文件”“搜索上一个附件里的 xxx”这类自然语言续接。
- 复用 AstrBot 内置工具，保留原有权限、路径、知识库和定时任务逻辑。
- Grok 自带联网搜索，本插件默认不桥接 `web_search_*`。

默认自动工具白名单：

- `future_task`
- `astr_kb_search`
- `astrbot_file_read_tool`
- `astrbot_grep_tool`

手动命令默认额外允许：

- `send_message_to_user`
- `astrbot_upload_file`
- `astrbot_download_file`

主动任务默认允许：

- `send_message_to_user`
- `future_task`
- `astr_kb_search`
- `astrbot_file_read_tool`
- `astrbot_grep_tool`

## 使用方式

自动模式开启后，满足 AstrBot 原本唤醒条件的 Grok 消息会先经过工具路由：

```text
明天早上 9 点提醒我交日报
```

插件会判断为 `future_task`，调用内置定时任务工具，再让最终模型总结结果。

手动命令：

```text
/grok工具 明天早上 9 点提醒我交日报
/grok工具 查一下知识库里关于群规的内容
/grok工具 读取 README.md 总结一下
/grok工具 在项目里搜 send_message_to_user
```

最近文件桥接示例：

```text
<先发送一个 txt / md / pdf / docx 文件>
总结我刚才发的文件
搜索刚才文件里关于早餐的段落
```

如果开启了 `auto_process_uploaded_text_file`，并且当前会话 provider 命中 `target_provider_keywords`，那么用户单独发送一个白名单文本附件时，插件也可以直接自动总结。

## 平台支持

- 已声明支持：`aiocqhttp`
- 其他平台未验证，暂不在 `metadata.yaml` 中声明。

## 配置建议

- `router_provider_id`：建议选择 `gpt-oss-120b` 这类稳定输出 JSON 的模型。
- `final_provider_id`：留空时使用当前会话模型，通常就是 Grok。
- `proactive_agent_provider_id`：必须选择一个稳定支持 tools/function calling 的模型，用于 future_task 到点后的执行和通知；留空不会回退当前 Grok，只会记录警告并放过原流程。
- `target_provider_keywords`：默认 `grok`、`xai`，只自动接管这类 Provider。
- `confidence_threshold`：默认 `0.65`，低于阈值不接管默认 Grok 回复。
- `enabled_auto_tools`：自动模式白名单，建议保持默认。
- `enabled_proactive_tools`：主动任务白名单，建议保留 `send_message_to_user`。
- `recent_file_bridge_enabled`：是否缓存最近一个聊天文件附件，并支持后续“刚才的文件”式引用。
- `recent_file_allowed_extensions` / `recent_file_max_size_kb`：限制会被缓存的聊天文件类型和大小。
- `auto_process_uploaded_text_file`：是否在用户单独发送文件时立刻自动总结；默认关闭，避免误触发。该模式仍会遵守 `target_provider_keywords` 的 provider 匹配。
- `debug_mode`：开启后会输出详细桥接日志，能看到 `session`、当前/路由/最终使用的 Provider、`step` 序号、router 决策、tool 参数和结果预览，适合在 AstrBot 里排查“为什么没触发/为什么触发错了”。

## 工作流程

```text
收到 LLM 请求
-> 当前 Provider 是否匹配 Grok/xAI
-> 路由模型输出 JSON 决策
-> no_tool：放行给原 Grok
-> tool_call：停止默认请求
-> 执行白名单内置工具
-> 调最终模型整理回复
-> 发送给用户
```

未来任务到点后的主动执行流程：

```text
future_task 到点
-> AstrBot Cron 唤醒主 Agent
-> 插件识别 cron_job/background_task_result
-> 如果 proactive_agent_provider_id 未配置，记录 warning 并放过原流程
-> 使用 proactive_agent_provider_id 调用工具执行助手，并注入当前会话的人格提示和历史对话
-> 工具助手调用 send_message_to_user，或由插件把最终文本直接发回原会话
-> 原 Grok Agent 即使不会调用函数，也不会影响提醒送达
```

## 边界

- 这不是 Grok 原生 function calling。
- 插件只在 AstrBot 已经准备调用 LLM 时工作，不扫描所有群消息。
- 不确定是否需要工具时会放行，不接管。
- 最近文件桥接只缓存每个会话最新一个白名单附件，超时后会自动失效。
- 自动模式默认不开放 shell、python、写文件、编辑文件、浏览器自动化等高风险工具。
- `send_message_to_user` 更适合后台任务主动推送，普通当前会话回复由插件直接发送。
- 主动任务模式不重写 AstrBot 定时系统，只补齐 Cron 唤醒后的工具执行模型。
- 主动任务执行模型不自动回退到当前会话模型，避免当前模型仍是 Grok 时继续无法调用工具。

## 验证

```powershell
python -m json.tool _conf_schema.json
python -m py_compile main.py core\*.py
python -m pytest -q
```
