# Usage: make install | make run | make test | make stress-test
# Override the virtualenv folder with e.g. `make run VENV=.venv`.
VENV ?= venv
PY = $(VENV)/bin/python

.PHONY: install run test stress-test

install:
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) -m uvicorn api.main:app --app-dir ots-chatbot-backend --port 8000

test:
	$(PY) -m pip install -q -r requirements-dev.txt
	$(PY) -m pytest ots-chatbot-backend/tests -q

# Ugly third-party page that checks the widget's style isolation. Start the server with `make run` first.
stress-test:
	$(PY) -m http.server 8002 --directory widget-stress-test
