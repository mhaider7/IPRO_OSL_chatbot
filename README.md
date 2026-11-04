# Hawk: Office of Student Life chatbot

Hawk is a student-facing chatbot for the Illinois Tech Office of Student Life (OSL). It answers questions about student organizations, funding, events, and space reservations using the OSL Student Organization Handbook. It retrieves relevant passages from a local ChromaDB index and generates the answer with a local Ollama model. Students can also get a draft email to OSL to review and send themselves; Hawk never sends mail.

## Layout

- `IIT_Chatbot_UI_Design/`: browser frontend (`index.html`, `css/`, `js/`), served by the API. `hawk.js`, `hawk.css`, and the `.dc.html` file are an older design kept for reference and are not loaded.
- `ots-chatbot-backend/`: FastAPI backend.
  - `api/`: app factory, routes, schemas, and `RagService`.
  - `config/`: `config.yaml` (model and system prompt), the handbook in `docs/`, and the loader.
  - `retrieval/`: ChromaDB index and retrieval.
  - `llm/`: Ollama client.
  - `escalation/`: email draft builder.
  - `tests/`: API tests.

## Run locally

Requires Python 3.11+ and [Ollama](https://ollama.com).

```
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn api.main:app --app-dir ots-chatbot-backend --port 8000
```

On Windows use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`.

Open <http://127.0.0.1:8000/> for the chat UI and <http://127.0.0.1:8000/docs> for the API.

On startup the app reads the PDF/TXT files in `config/docs/`, splits them into paragraphs, and indexes them in `ots-chatbot-backend/.chroma/` (git-ignored, rebuilt as needed).

## Model

`config/config.yaml` sets the Ollama model (currently `llama3.2`) and the system prompt. Start Ollama and make sure the model exists (`ollama pull llama3.2`, or register a fine-tuned GGUF with `ollama create`). GGUF files are git-ignored; do not commit them.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/health` | Service status |
| `GET /api/info` | OSL name and contact email |
| `POST /api/chat` | Answer from the conversation (`{"messages": [{"role": "user", "text": "..."}]}`) |
| `POST /api/chat/stream` | Same, streamed as newline-delimited JSON |
| `POST /api/escalate` | Draft an email to OSL, or ask one clarifying question |

Limits: 60 messages, 4,000 characters per message. Invalid requests return 422; failures return 503. Set `HAWK_ALLOWED_ORIGINS` (see `.env.example`) to allow a frontend on another origin.

## Data handling

Chat history stays in the browser tab's `sessionStorage`; the server does not store it. Email drafts mask email addresses and A-number student IDs. Use fictional questions during development.

## Tests

```
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest ots-chatbot-backend/tests -q
```
