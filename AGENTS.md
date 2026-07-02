# astrbot_plugin_grok_tool_bridge 维护规则

## 项目边界

本插件为 Grok / xAI 等模型提供 AstrBot 内置工具桥接。它不修改 AstrBot Core，不重新实现内置工具，也不接管所有消息。

## 模块职责

- `main.py`：插件生命周期、Agent 开始 hook、LLM 请求 hook、手动命令入口。
- `core/config_manager.py`：配置解析和默认值。
- `core/provider_matcher.py`：判断当前会话 Provider 是否属于目标模型。
- `core/router.py`：调用路由模型并解析 JSON 决策。
- `core/tool_policy.py`：工具白名单、工具说明、schema 暴露。
- `core/tool_executor.py`：复用 AstrBot `FunctionToolExecutor` 执行内置工具。
- `core/bridge_service.py`：自动/手动桥接和主动任务执行流程编排。
- `core/prompts.py`：内置路由和最终回复提示词。

## 硬约束

- 自动模式默认只开放低风险工具：`future_task`、`astr_kb_search`、`astrbot_file_read_tool`、`astrbot_grep_tool`。
- 不要默认开放 shell、python、写文件、编辑文件、浏览器自动化、Neo skill 发布工具。
- 路由模型只能决定工具调用，不负责最终用户回复。
- 不确定是否需要工具时必须放行默认 Grok 回复。
- `future_task` 到点后的主动任务由可配置的工具执行助手处理，必须保留 `send_message_to_user` 可作为通知路径。
- 插件运行数据必须使用 `StarTools.get_data_dir(self.name)`，不要在插件源码目录创建运行态数据。

## 验证

修改后至少运行：

```powershell
python -m json.tool _conf_schema.json
python -m py_compile main.py core\*.py
python -m pytest -q
```
