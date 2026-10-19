"""Turn text into vectors: the real local model, or a deterministic stand-in for tests."""

import hashlib
import math
import re
from typing import Protocol

from poc_common.config import Config, get_config

TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    """bge-m3 (1024 dimensions, multilingual) served by Ollama's OpenAI-compatible API."""

    def __init__(self, config: Config | None = None, batch_size: int = 32) -> None:
        from poc_common.llm import get_embeddings

        config = config or get_config()
        self.dimensions = config.embed_dimensions
        self.batch_size = batch_size
        self._client = get_embeddings(config)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._client.embed_documents(texts[start:start + self.batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


class HashingEmbedder:
    """Signed feature hashing of words into a fixed-size, normalised vector.

    No model and fully deterministic: texts that share words get similar vectors. Each word is
    spread over several slots with +1/-1 signs, so an accidental slot collision between two
    different words adds only a small, sign-random amount of false similarity. Repeated words are
    dampened (1 + log tf). Good enough to test storage, filtering and ranking; not a substitute
    for real semantic embeddings.
    """

    SLOTS_PER_WORD = 4

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        counts: dict[str, int] = {}
        for token in TOKEN.findall(text.lower()):
            if len(token) >= 2:
                counts[token] = counts.get(token, 0) + 1
        vector = [0.0] * self.dimensions
        for token, count in counts.items():
            digest = hashlib.sha256(token.encode()).digest()
            weight = 1.0 + math.log(count)
            for slot in range(self.SLOTS_PER_WORD):
                index = int.from_bytes(digest[slot * 4: slot * 4 + 4], "little")
                sign = 1.0 if digest[16 + slot] & 1 else -1.0
                vector[index % self.dimensions] += sign * weight
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
