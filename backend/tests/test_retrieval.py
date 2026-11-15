from nip.domain.models import Region
from nip.rag.store import load_corpus


def test_corpus_chunks_have_metadata():
    chunks = load_corpus()
    assert len(chunks) > 80
    assert all(c.source and c.title and c.text for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)


def test_hybrid_finds_warfarin_doc(retriever):
    hits = retriever.search("I take warfarin, can I eat spinach?", Region.US, k=3)
    assert hits[0][0].doc_id == "warfarin-vitamin-k"


def test_region_filter_excludes_other_region(retriever):
    hits = retriever.search("salt intake guidelines", Region.US, k=10)
    assert all(c.region in ("US", "GLOBAL") for c, _ in hits)
    hits_in = retriever.search("salt intake pickles papad", Region.IN, k=3)
    assert hits_in[0][0].doc_id == "indian-salt-hidden-sodium"


def test_modes_return_results(retriever):
    for mode in ("dense", "sparse", "hybrid"):
        assert retriever.search("creatine kidney safety", Region.IN, k=3, mode=mode)
