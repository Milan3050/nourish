import os
import tempfile

import pytest

os.environ.setdefault("NIP_ENV", "test")
os.environ.setdefault("NIP_EMBEDDINGS_PROVIDER", "hash")
os.environ.setdefault("NIP_TOOL_TRANSPORT", "inprocess")
os.environ.setdefault("NIP_OTEL_ENDPOINT", "")  # no span export from tests, even when .env enables Phoenix


@pytest.fixture(scope="session")
def pg_url():
    """A throwaway Postgres with pgvector. Uses NIP_TEST_DATABASE_URL if set, else embedded pgserver."""
    url = os.environ.get("NIP_TEST_DATABASE_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    d = tempfile.mkdtemp(prefix="nip-pg-")
    srv = pgserver.get_server(d, cleanup_mode="stop")
    yield srv.get_uri()


@pytest.fixture(scope="session")
def retriever(pg_url):
    from nip.rag.embeddings import HashEmbedder
    from nip.rag.store import Retriever, connect, index_corpus

    conn = connect(pg_url)
    emb = HashEmbedder(768)
    index_corpus(conn, emb)
    return Retriever(conn, emb)
