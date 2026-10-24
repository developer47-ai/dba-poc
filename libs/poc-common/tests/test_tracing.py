import pytest
from langchain_core.messages import AIMessage, HumanMessage

from poc_common.config import Config
from poc_common.fake_llm import ScriptedChatModel, tool_call_message
from poc_common.llm_trace import LlmSpanHandler
from poc_common.testing import capture_spans
from poc_common.tracing import (
    annotate,
    current_trace_id,
    fail,
    setup_tracing,
    span,
    tracing_enabled,
)


def by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def test_tracing_is_off_without_an_endpoint_and_helpers_are_harmless():
    assert setup_tracing("unit", Config(otlp_endpoint=None)) is False
    assert not tracing_enabled()
    with span("anything", {"a": 1}) as current:
        annotate({"b": 2}, name="renamed")
        assert not current.is_recording()
    assert current_trace_id() is None


def test_nested_spans_share_a_trace_and_carry_clean_attributes():
    with capture_spans() as exporter:
        assert tracing_enabled()
        with span("turn", {"session.id": "s1", "skipped": None, "obj": {"x": 1}}):
            trace_id = current_trace_id()
            with span("child"):
                annotate({"step": "S1"}, name="child S1")
    spans = by_name(exporter)
    assert set(spans) == {"turn", "child S1"}
    assert spans["child S1"].parent.span_id == spans["turn"].context.span_id
    assert format(spans["turn"].context.trace_id, "032x") == trace_id
    assert spans["child S1"].attributes["step"] == "S1"
    assert "skipped" not in spans["turn"].attributes
    assert spans["turn"].attributes["obj"] == "{'x': 1}"
    assert spans["turn"].resource.attributes["service.name"] == "test-service"
    assert not tracing_enabled()


def test_an_exception_marks_the_span_as_failed():
    with capture_spans() as exporter:
        with pytest.raises(ValueError):
            with span("boom"):
                raise ValueError("nope")
        with span("soft") as current:
            fail(current, "tool said no")
    spans = by_name(exporter)
    assert spans["boom"].status.status_code.name == "ERROR"
    assert spans["soft"].status.description == "tool said no"


async def test_llm_call_becomes_a_span_with_streaming_details():
    llm = ScriptedChatModel(script=[AIMessage("The modem is stuck."), tool_call_message("lookup")])
    handler = LlmSpanHandler()
    with capture_spans() as exporter:
        with span("turn"):
            chunks = [c async for c in llm.astream(
                [HumanMessage("what is wrong?")], config={"callbacks": [handler]})]
            await llm.ainvoke([HumanMessage("and now?")], config={"callbacks": [handler]})
    assert "".join(c.text for c in chunks) == "The modem is stuck."
    llm_spans = [s for s in exporter.get_finished_spans() if s.name == "llm.chat"]
    assert len(llm_spans) == 2
    first = llm_spans[0]
    turn = by_name(exporter)["turn"]
    assert first.parent.span_id == turn.context.span_id
    assert first.attributes["llm.messages"] == 1
    assert first.attributes["llm.prompt_chars"] == len("what is wrong?")
    assert first.attributes["llm.completion_chars"] == len("The modem is stuck.")
    assert first.attributes["llm.stream_chunks"] >= 4 and "llm.first_token_ms" in first.attributes
    assert first.attributes["llm.tool_calls"] == 0
    assert llm_spans[1].attributes["llm.tool_calls"] == 1


async def test_failed_llm_call_is_recorded():
    llm = ScriptedChatModel(script=[])
    with capture_spans() as exporter:
        with pytest.raises(AssertionError):
            await llm.ainvoke([HumanMessage("hi")], config={"callbacks": [LlmSpanHandler()]})
    (failed,) = [s for s in exporter.get_finished_spans() if s.name == "llm.chat"]
    assert failed.status.status_code.name == "ERROR"
    assert failed.events[0].name == "exception"
