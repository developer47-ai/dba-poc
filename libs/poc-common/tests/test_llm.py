from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from poc_common.llm import get_chat_model, get_embeddings, strip_thinking


def test_chat_model_points_at_local_ollama(config):
    model = get_chat_model(config)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "qwen3:4b"
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
