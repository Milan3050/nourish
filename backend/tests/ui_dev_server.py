"""UI development server: the real API and graph, with a scripted LLM instead of a model server.

For working on the frontend without Ollama. Not a product mode: answers are canned.
    uv run python -m tests.ui_dev_server
"""

import os
import tempfile

os.environ.setdefault("NIP_EMBEDDINGS_PROVIDER", "hash")
os.environ.setdefault("NIP_TOOL_TRANSPORT", "inprocess")

import pgserver  # noqa: E402

srv = pgserver.get_server(tempfile.mkdtemp(prefix="nip-ui-"), cleanup_mode="stop")
os.environ["NIP_DATABASE_URL"] = srv.get_uri()

import uvicorn  # noqa: E402

from nip.llm import set_model_override  # noqa: E402
from nip.rag.embeddings import HashEmbedder  # noqa: E402
from nip.rag.store import connect, index_corpus  # noqa: E402
from tests.scripted import ScriptedChatModel, call, tool_results  # noqa: E402
from tests.test_graph import veg_world  # noqa: E402

index_corpus(connect(), HashEmbedder(768))


import time  # noqa: E402


class DemoWorld(type(veg_world())):
    def __call__(self, role, messages):
        time.sleep(0.45)  # make the hand-offs visible in the UI
        return super().__call__(role, messages)

    def recommendation(self, messages):
        return call("submit_result", headline="A 1-day vegetarian plan that fits your targets",
                    summary="This plan keeps you in a moderate deficit while giving you enough protein to hold on to "
                            "muscle. Soya and paneer carry most of the protein, and rice keeps it filling. "
                            "Every portion was checked against your peanut allergy.",
                    key_points=["Spread protein across all three meals", "Weigh rice raw for the first week"],
                    tips=["Soak soya beans overnight to cut cooking time."],
                    follow_up_questions=["Swap dinner for something lighter", "Make it 3 days"])

    def nutrition(self, messages):
        res = tool_results(messages, "search_guidelines")
        if not res:
            return call("search_guidelines", query="creatine kidney disease safety")
        cid = res[-1]["results"][0]["chunk_id"]
        return call("submit_result", items=[{"question": "Is creatine safe?", "answer": "Generally safe at 3-5 g a day for healthy people; avoid with kidney disease unless your doctor approves.",
                                             "cited_chunk_ids": [cid], "sufficient": True}])


w = veg_world()
w.__class__ = DemoWorld
set_model_override(ScriptedChatModel(policy=w))

if __name__ == "__main__":
    uvicorn.run("nip.api.main:app", port=8000, log_level="warning")
