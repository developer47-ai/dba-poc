from knowledge_base import HashingEmbedder, cosine


def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder(64)
    a, b = embedder.embed_query("modem lights"), embedder.embed_query("modem lights")
    assert a == b and abs(sum(v * v for v in a) - 1.0) < 1e-9


def test_shared_words_mean_similar_vectors():
    embedder = HashingEmbedder()
    reboot = embedder.embed_query("reboot the modem remotely")
    assert cosine(reboot, embedder.embed_query("remotely reboot a modem")) > 0.8
    assert cosine(reboot, embedder.embed_query("fiber optical signal")) < 0.2


def test_unrelated_text_stays_dissimilar_to_long_documents():
    embedder = HashingEmbedder()
    long_text = " ".join(["internet modem light orange PPP session down reboot"] * 20)
    assert abs(cosine(embedder.embed_query("chocolate cake recipe"),
                      embedder.embed_query(long_text))) < 0.2
