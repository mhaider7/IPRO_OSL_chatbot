# Hawk: Office of Student Life chatbot

## Run locally

Requires Python 3.11+ and [Ollama](https://ollama.com), running with the model named in `ots-chatbot-backend/config/config.yaml` (see Model below).

```
make install   # create .venv/ and install dependencies (first time only)
make run       # start the server on port 8001
make test      # install test dependencies and run the tests
```

Open <http://localhost:8001/> for the demo page with the Hawk widget, and <http://localhost:8001/docs> for the API.

The examples in this README assume port 8001 for Hawk and 8002 for the stress-test page. Any free ports work, but the stress-test page loads Hawk from 8001 by default, and Hawk only lets 8002 embed it by default. If you pick other ports, see the stress-test section below and `HAWK_FRAME_ANCESTORS`.

The virtualenv lives in `.venv/`. If you already have one named `venv/`, it keeps working: `make` uses `.venv/` when it exists and otherwise falls back to `venv/`. In the commands below, replace `.venv` with `venv`.

`make` is available on macOS and Linux. Without it, run the underlying commands directly:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn api.main:app --app-dir ots-chatbot-backend --port 8001
```

On Windows (PowerShell), from the repository folder:

```powershell
# First time only: create .venv\ and install dependencies
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Start the server, then open http://localhost:8001/
.\.venv\Scripts\python.exe -m uvicorn api.main:app --app-dir ots-chatbot-backend --port 8001

# CSS stress test (in a second terminal, with the server running), then open http://localhost:8002/
.\.venv\Scripts\python.exe -m http.server 8002 --directory widget-stress-test

# Tests
.\.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest ots-chatbot-backend/tests -q
```

On startup the app reads the files in `config/docs/`, splits them into sections, and rebuilds the index in `ots-chatbot-backend/.chroma/` (git-ignored). Each chunk carries its source file, section path, and page numbers, and the model sees them so it can cite pages.

Adding documents to `config/docs/`:

- **PDF**: sections come from the PDF's built-in outline (bookmarks). A PDF without an outline falls back to paragraph splitting.
- **`.md` / `.txt`**: mark headings with `#`, `##`, `###`. A file with no headings falls back to paragraph splitting.
- Sections under 150 characters are merged into a neighbour, and sections over 2,000 characters are split at paragraph breaks (`MIN_CHARS` / `MAX_CHARS` in `config/loader.py`).

## Embeddable widget

Hawk is delivered as a floating "Ask Hawk" button that opens the chat in a panel on a website, such as the OSL pages.

| Address | What it is |
| --- | --- |
| `/` | Demo page: a look-alike of the OSL page (placeholder photos, inactive links) with the widget on it. |
| `/chat` | The chat app itself. The widget shows it in its panel as `/chat?embed=1`. Opened directly, it fills the window, which is handy for debugging. |
| `/embed.js` | The loader script that host sites include. |

Add one line to the host page, just before `</body>`, using the address of your Hawk server:

```html
<script src="http://localhost:8001/embed.js" defer></script>
```

**Cookie banners.** To keep the launcher clear of a cookie banner (or anything else fixed to the bottom of the page), name it with `data-avoid`. While that element covers the launcher's corner, the launcher and panel move up above it; once it's dismissed, they move back. For iit.edu, whose banner comes from Klaro:

```html
<script src="http://localhost:8001/embed.js" data-avoid=".klaro .cm-modal" defer></script>
```

The demo page has a look-alike banner built the same way and uses this exact snippet. Its footer's "Cookie settings" link shows the banner again.

How it works: `embed.js` runs on the host page and draws the launcher inside a Shadow DOM, so the page's CSS can't change it. Clicking it opens an iframe of `/chat?embed=1`, the chat app with a compact header and a minimize button. The chat runs entirely inside the iframe and calls the API on its own origin, so no CORS change is needed. Closing the panel minimizes it: the conversation is kept and, because it is saved in the iframe's `sessionStorage`, when the visitor moves to another page on the same site in the same tab. If the panel was open, it reopens on the next page, without moving keyboard focus into it. A new tab starts a fresh conversation.

Keyboard: Tab reaches the launcher; Enter or Space opens the panel and puts focus in the message box. In the message box, Enter sends and Shift+Enter adds a new line (on touch screens Enter adds a new line and the send button sends). Escape, the minimize button in the panel header, or the launcher's chevron minimizes the panel and returns focus to the launcher. Only "New chat" ends the conversation. On screens 520px wide or narrower, or 480px tall or shorter, the open panel fills the screen.

**Allow the host site to embed Hawk.** The chat page sends `Content-Security-Policy: frame-ancestors`, which lists the sites allowed to show it in an iframe. Set `HAWK_FRAME_ANCESTORS` (see `.env.example`) to a comma-separated list. The default allows only Hawk itself (including the demo page) and the stress-test page on port 8002. For the OSL site, add `https://www.iit.edu`.

**CSS stress test.** `widget-stress-test/index.html` is a deliberately ugly page whose CSS tries to restyle every font, button, and frame. If the widget looks the same there as on the demo page, host-page styles aren't leaking into it. It runs on its own port so the browser treats it as a separate site from Hawk, like the real OSL site. With the Hawk server running:

```
make stress-test   # or: python -m http.server 8002 --directory widget-stress-test
```

Open <http://localhost:8002/>. It loads Hawk from `http://localhost:8001`; add `?hawk=http://127.0.0.1:8001` to load Hawk from a truly different site (browsers treat every `localhost` port as the same site), `?hawk=<address>` for any other server, or `?hawk=none` to see the page without the widget.

**What the host site would need** (for example https://www.iit.edu/student-life, which runs Drupal 10):

- A site administrator adds the snippet to the site-wide theme or footer, or through a script-injection module. Drupal removes `<script>` tags pasted into ordinary page content.
- Hawk must be served over HTTPS from a public address, and `HAWK_FRAME_ANCESTORS` must include `https://www.iit.edu`. Browsers block public websites from loading scripts from `localhost`, so the widget cannot be tried on the live site against a local server.
- iit.edu currently sends no Content-Security-Policy, so nothing else is needed. If it adds one later, it must allow the Hawk address in `script-src` and `frame-src`.
- iit.edu shows a cookie-consent banner at the bottom of the page. Include `data-avoid=".klaro .cm-modal"` in the snippet so the launcher stays above it. If the site changes its cookie tool, this value needs updating.
- Pages that are wider than the screen on phones (horizontal scrolling) push fixed-position elements, including the launcher, partly off-screen.

## About

Hawk is a student-facing chatbot for the Illinois Tech Office of Student Life (OSL). It answers questions about student organizations, funding, events, and space reservations using the OSL Student Organization Handbook. It retrieves relevant passages from a local ChromaDB index and generates the answer with a local Ollama model. Students can also get a draft email to OSL to review and send themselves; Hawk never sends mail.

## Layout

- `IIT_Chatbot_UI_Design/`: browser frontend, served by the API. `chat.html`, `css/`, and `js/` are the chat app; `embed.js` is the widget loader; `js/embed-mode.js` adapts the app when it runs inside the widget panel; `demo.html` is the demo page at `/`. `hawk.js`, `hawk.css`, and the `.dc.html` file are an older design kept for reference and are not loaded.
- `widget-stress-test/`: ugly page that checks the widget's style isolation.
- `ots-chatbot-backend/`: FastAPI backend.
  - `api/`: app factory, routes, schemas, and `RagService`.
  - `config/`: `config.yaml` (model and system prompt), the handbook in `docs/`, and the loader.
  - `retrieval/`: ChromaDB index and retrieval.
  - `llm/`: Ollama client.
  - `escalation/`: email draft builder.
  - `tests/`: API tests.

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
| `GET /embed.js` | Widget loader script for other websites |

Limits: 60 messages, 4,000 characters per message. Invalid requests return 422; failures return 503. Set `HAWK_ALLOWED_ORIGINS` (see `.env.example`) to allow a frontend on another origin.

## Data handling

Chat history stays in the browser tab's `sessionStorage`; the server does not store it. Email drafts mask email addresses and A-number student IDs. Use fictional questions during development.

## Tests

`make test` runs the suite. Two tests (marked `integration`) call the real Ollama model; to skip them, run `.venv/bin/python -m pytest ots-chatbot-backend/tests -q -m "not integration"`.
