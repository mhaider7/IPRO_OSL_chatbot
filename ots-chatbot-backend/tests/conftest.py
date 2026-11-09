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
