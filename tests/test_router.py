from core.json_utils import parse_json_object
from core.router import parse_tool_decision


def test_parse_json_object_from_fenced_text():
    payload = parse_json_object(
        '```json\n{"action":"no_tool","confidence":0.2}\n```'
    )

    assert payload == {"action": "no_tool", "confidence": 0.2}


def test_parse_tool_decision_tool_call():
    decision = parse_tool_decision(
        """
        Here is the decision:
        {"action":"tool_call","tool":"future_task","args":{"action":"list"},"confidence":0.91,"reason":"reminder"}
        """
    )

    assert decision.wants_tool
    assert decision.tool == "future_task"
    assert decision.args == {"action": "list"}
    assert decision.confidence == 0.91


def test_parse_tool_decision_invalid_json_is_no_tool():
    decision = parse_tool_decision("not json")

    assert not decision.wants_tool
    assert decision.action == "no_tool"

