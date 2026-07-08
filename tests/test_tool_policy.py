from core.tool_policy import ToolPolicy


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"{name} description"
        self.parameters = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
        }


class FakeToolManager:
    def __init__(self):
        self.tools = {
            "astr_kb_search": FakeTool("astr_kb_search"),
            "future_task": FakeTool("future_task"),
            "shell": FakeTool("shell"),
            "python": FakeTool("python"),
        }

    def get_func(self, name):
        return self.tools.get(name)


def test_policy_filters_unavailable_tools_and_dedupes():
    policy = ToolPolicy(FakeToolManager())

    descriptors = policy.descriptors(
        ["astr_kb_search", "missing_tool", "astr_kb_search"]
    )

    assert [descriptor.name for descriptor in descriptors] == ["astr_kb_search"]
    assert policy.is_allowed("astr_kb_search", ["astr_kb_search"])
    assert not policy.is_allowed("missing_tool", ["missing_tool"])


def test_tool_prompt_includes_schema():
    policy = ToolPolicy(FakeToolManager())

    prompt = policy.tool_prompt(["future_task"])

    assert "future_task" in prompt
    assert "JSON Schema" in prompt


def test_tool_set_contains_available_tools_only():
    policy = ToolPolicy(FakeToolManager())

    tool_set = policy.tool_set(["future_task", "missing_tool"])

    assert [tool.name for tool in tool_set.tools] == ["future_task"]


def test_policy_rejects_configured_tools_outside_bridge_allowlist():
    policy = ToolPolicy(FakeToolManager())

    assert not policy.is_allowed("shell", ["shell"])
    assert not policy.is_allowed("python", ["python"])

    tool_set = policy.tool_set(["shell", "astr_kb_search", "python"])

    assert [tool.name for tool in tool_set.tools] == ["astr_kb_search"]
