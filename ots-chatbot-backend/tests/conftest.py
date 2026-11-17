from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Keep the tests away from a running dev server. Importing api.main builds the app,
# and building the app deletes and rebuilds the search index. Pointed at the shared
# ots-chatbot-backend/.chroma, that left a running server holding a deleted index
# ("Cannot reach Hawk" until it was restarted). The tests get their own index in the
# system temp folder instead; build_collection reads DB_PATH each time it runs, so this
# must happen before any test imports api.main (conftest.py is loaded first).
# One fixed folder, rebuilt in place by every run: a fresh folder per run can't be
# deleted afterwards on Windows (the index files are still open when Python exits).
from retrieval import chroma_setup  # noqa: E402

chroma_setup.DB_PATH = Path(tempfile.gettempdir()) / "hawk-test-chroma"

# Don't load the Ollama model in the background every time a test starts the app.
# Tests that check the warm-up itself patch this again or call llm.ollama_client.
import api.service  # noqa: E402

api.service.warm_up_in_background = lambda model: None

# Don't ask a real Ollama whether a model can "think" (llm.ollama_client.thinks): tests treat every model
# as a non-thinking one unless they say otherwise (tests/test_thinking.py fills the cache itself).
from llm import ollama_client  # noqa: E402

ollama_client.thinks_from_ollama = ollama_client.thinks   # the real one, for tests/test_thinking.py
ollama_client.thinks = lambda model: ollama_client._thinking.get(model, False)

# The search picker (api/routing.py) never calls a real Ollama in tests: its model call fails, so every test that
# doesn't pass its own fake gets the fallback (the regex router + today's search text = the behavior the older
# tests were written for). tests/test_routing.py passes fakes.
import api.routing  # noqa: E402


def _no_model_in_tests(messages, model, timeout):
    raise ConnectionError("no Ollama in tests")


api.routing.ask_model = _no_model_in_tests
