from app.pipelines.tools import load_tool_specs
from app.pipelines.tools.court_practice import try_build_tool_specs


def test_no_opensearch_config_yields_no_specs():
    assert try_build_tool_specs({}) == []
    assert try_build_tool_specs({"opensearch": {}}) == []
    assert load_tool_specs({}) == []


def test_opensearch_config_builds_court_practice_specs():
    # Client construction is lazy (no connection), so this needs no live server.
    specs = try_build_tool_specs({"opensearch": {"url": "http://localhost:9200"}})
    names = [spec.tool.name for spec in specs]
    assert names == ["search_court_practice", "get_court_decision"]


def test_load_tool_specs_aggregates_builders():
    specs = load_tool_specs({"opensearch": {"url": "http://localhost:9200"}})
    assert {spec.tool.name for spec in specs} == {"search_court_practice", "get_court_decision"}
