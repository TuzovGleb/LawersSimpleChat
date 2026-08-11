"""Tests for finish_reason normalization: streaming responses arrive with the
value doubled ("stopstop", "tool_callstool_calls") because OpenRouter sends
finish_reason on two chunks and langchain merges same-key strings by
concatenation. dedup_finish_reason collapses the repetition before the value
reaches logs and the SSE final metadata."""
import pytest
from langchain_core.messages import AIMessage

from app.pipelines.nodes import _extract_metadata
from app.utils import dedup_finish_reason


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tool_callstool_calls", "tool_calls"),
        ("stopstop", "stop"),
        ("lengthlength", "length"),
        ("stopstopstop", "stop"),  # any whole-string repetition, not just x2
        ("stop", "stop"),
        ("tool_calls", "tool_calls"),
        (None, None),
        ("", ""),
    ],
)
def test_dedup_finish_reason(raw, expected):
    assert dedup_finish_reason(raw) == expected


def test_extract_metadata_dedups_merged_finish_reason():
    response = AIMessage(
        content="ok",
        response_metadata={"finish_reason": "tool_callstool_calls"},
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    metadata = _extract_metadata("fast", response, 1200, tool_rounds=2)
    assert metadata["finishReason"] == "tool_calls"
    assert metadata["totalTokens"] == 15
    assert metadata["toolCallsCount"] == 2


def test_extract_metadata_defaults_to_stop_when_missing():
    response = AIMessage(content="ok", response_metadata={})
    metadata = _extract_metadata("fast", response, 100)
    assert metadata["finishReason"] == "stop"
