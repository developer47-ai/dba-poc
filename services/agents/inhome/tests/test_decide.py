import pytest
from langchain_core.messages import AIMessage

from inhome_agent.decide import choose, outcome_tool, parse_decision
from poc_common.fake_llm import ScriptedChatModel, tool_call_message

OPTIONS = ["access_problem", "modem_fault", "modem_stuck"]


def test_tool_schema_limits_the_outcome_to_the_allowed_values():
    schema = outcome_tool(OPTIONS)["function"]["parameters"]["properties"]["outcome"]
    assert schema["enum"] == [*OPTIONS, "unclear"]


def test_function_call_is_the_normal_path():
    reply = tool_call_message("report_outcome", {"outcome": "modem_stuck", "reason": "wan green"})
    decision = parse_decision(reply, OPTIONS)
    assert decision.outcome == "modem_stuck" and decision.reason == "wan green"


@pytest.mark.parametrize("raw", ["Modem Stuck", "modem-stuck", " `modem_stuck` "])
def test_outcome_spelling_is_normalised(raw):
    reply = tool_call_message("report_outcome", {"outcome": raw, "reason": ""})
    assert parse_decision(reply, OPTIONS).outcome == "modem_stuck"


def test_unclear_or_invented_outcomes_are_rejected():
    for raw in ["unclear", "reboot_now", 42]:
        reply = tool_call_message("report_outcome", {"outcome": raw, "reason": ""})
        assert parse_decision(reply, OPTIONS).outcome is None


def test_json_in_text_is_accepted():
    reply = AIMessage('Sure. {"outcome": "modem_fault", "reason": "power light orange"}')
    decision = parse_decision(reply, OPTIONS)
    assert decision.outcome == "modem_fault" and decision.reason == "power light orange"


def test_single_allowed_word_in_text_is_accepted():
    assert parse_decision(AIMessage("The answer is modem_stuck."), OPTIONS).outcome == "modem_stuck"


def test_ambiguous_text_is_unclear():
    reply = AIMessage("Either modem_fault or modem_stuck, hard to say.")
    assert parse_decision(reply, OPTIONS).outcome is None


async def test_choose_sends_the_prompt_and_binds_the_tool():
    llm = ScriptedChatModel(script=[tool_call_message("report_outcome", {"outcome": "up"})])
    decision = await choose(llm, "system", "Is it up?", ["up", "down"])
    assert decision.outcome == "up"
    system, prompt = llm.received[0]
    assert system.content == "system" and prompt.content == "Is it up?"
