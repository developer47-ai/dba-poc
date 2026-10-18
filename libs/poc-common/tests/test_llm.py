from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from poc_common.config import Config
from poc_common.llm import (
    ThinkingFilter,
    apply_thinking_mode,
    get_chat_model,
    get_embeddings,
    strip_thinking,
)


def test_chat_model_points_at_local_ollama(config):
    model = get_chat_model(config)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "qwen3:4b-instruct-2507-q4_K_M"
    assert str(model.openai_api_base).startswith("http://localhost:11434/v1")
    assert model.temperature == 0.0


def test_chat_model_overrides(config):
    assert get_chat_model(config, temperature=0.7).temperature == 0.7


def test_embeddings_use_plain_strings_for_ollama(config):
    embeddings = get_embeddings(config)
    assert isinstance(embeddings, OpenAIEmbeddings)
    assert embeddings.model == "bge-m3"
    assert embeddings.check_embedding_ctx_length is False


def test_strip_thinking_removes_reasoning_block():
    assert strip_thinking("<think>\nlet me think\n</think>\npong") == "pong"
    assert strip_thinking("pong") == "pong"


def test_strip_thinking_handles_a_missing_opening_tag():
    assert strip_thinking("Okay, let me check.\n</think>\n\nPPP is down.") == "PPP is down."


HYBRID = Config(chat_model="qwen3:8b")


def test_default_model_is_the_non_thinking_instruct_variant(config):
    assert "instruct" in config.chat_model
    assert apply_thinking_mode("Be brief.", config) == "Be brief."
    assert get_chat_model(config).reasoning_effort is None


def test_no_think_switch_only_for_hybrid_qwen3_with_thinking_off():
    assert apply_thinking_mode("Be brief.", HYBRID) == "Be brief.\n/no_think"
    thinking_on = Config(chat_model="qwen3:8b", llm_thinking=True)
    assert apply_thinking_mode("Be brief.", thinking_on) == "Be brief."
    assert apply_thinking_mode("Be brief.", Config(chat_model="granite3.3:8b")) == "Be brief."


def test_answers_are_length_capped(config):
    assert get_chat_model(config).max_tokens == 800


def test_hybrid_qwen3_thinking_is_switched_off_at_the_api_level():
    assert get_chat_model(HYBRID).reasoning_effort == "none"
    payload = get_chat_model(HYBRID)._get_request_payload([("user", "hi")])
    assert payload["reasoning_effort"] == "none"
    thinking_on = Config(chat_model="qwen3:8b", llm_thinking=True)
    assert get_chat_model(thinking_on).reasoning_effort is None
    assert get_chat_model(Config(chat_model="granite3.3:8b")).reasoning_effort is None


def test_chat_model_streams_by_default(config):
    assert get_chat_model(config).streaming is True


def _stream(chunks: list[str]) -> str:
    stream_filter = ThinkingFilter()
    return "".join(stream_filter.feed(c) for c in chunks) + stream_filter.flush()


def test_thinking_filter_hides_reasoning_split_across_chunks():
    chunks = ["<thi", "nk>\nsecret ", "plan</th", "ink>\n\n", "PPP is ", "down."]
    assert _stream(chunks) == "PPP is down."


def test_thinking_filter_passes_plain_text_through_immediately():
    stream_filter = ThinkingFilter()
    assert stream_filter.feed("The modem ") == "The modem "
    assert stream_filter.feed("is offline.") == "is offline."


def test_thinking_filter_holds_only_a_possible_partial_tag():
    stream_filter = ThinkingFilter()
    assert stream_filter.feed("Reboot <") == "Reboot "
    assert stream_filter.feed("b>now</b>") == "<b>now</b>"
    assert stream_filter.feed("x <th") == "x "
    assert stream_filter.flush() == "<th"


def test_thinking_filter_drops_unfinished_thinking():
    assert _stream(["<think>never closed"]) == ""
