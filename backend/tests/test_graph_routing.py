from langchain_core.messages import AIMessage, HumanMessage

from app.pipelines.nodes import MAX_TOOL_ROUNDS, route_after_generate


def test_route_to_tools_when_tool_calls_present():
    state = {
        "messages": [HumanMessage(content="найди практику"), AIMessage(content="", tool_calls=[{"name": "search_court_practice", "args": {}, "id": "1"}])],
        "tool_rounds": 0,
    }
    assert route_after_generate(state) == "tools"


def test_route_to_end_when_no_tool_calls():
    state = {
        "messages": [HumanMessage(content="вопрос"), AIMessage(content="ответ")],
        "tool_rounds": 1,
    }
    assert route_after_generate(state) == "end"


def test_route_to_end_when_tool_round_limit_reached():
    state = {
        "messages": [AIMessage(content="", tool_calls=[{"name": "search_court_practice", "args": {}, "id": "1"}])],
        "tool_rounds": MAX_TOOL_ROUNDS,
    }
    assert route_after_generate(state) == "end"
