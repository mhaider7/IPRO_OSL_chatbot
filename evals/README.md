# Hawk evaluation suite

End-to-end test of the chatbot: 188 fixed questions go through the real Hawk API (routing → search → answer
generation), and every reply is graded. Anyone can run it and get comparable numbers.

## Run it

```bash
ollama pull gemma4:e4b && ollama pull all-minilm          # chatbot/judge model + RAGAS embeddings
python -m venv .venv-eval && .venv-eval/Scripts/pip install -r evals/requirements-eval.txt   # RAGAS, once
python evals/run_all.py                                     # with the chatbot venv
```

Saves after every item. If it stops (or the laptop shuts down), continue with
`python evals/run_all.py --resume evals/results/<run_id>`. On a CPU-only laptop set `HAWK_PICKER_TIMEOUT_S=30`
first; on one that overheats add `--pause 3`. Results land in `evals/results/<date>_<commit>/`.

## Files

| File | Job |
|---|---|
| `testset_v1.jsonl` | the 188 questions: expected action, gold answer, key facts, handbook pages |
| `run_all.py` | one command for the whole pipeline below |
| `run_e2e.py` | sends the questions to `/api/chat`, saves each reply → `raw.jsonl` |
| `judge.py` | labels each reply answer / escalate / refuse → `judged.jsonl` |
| `score.py` | confusion matrix and all metrics → `stats.json` |
| `dump_contexts.py` | rebuilds the handbook text each answer used (for RAGAS) |
| `ragas_eval.py` | official RAGAS metrics → `ragas.json` (runs in `.venv-eval`) |
| `report.py` | everything on one page → `report.html` |
| `results/<run_id>/` | one folder per run; `run.json` records code version, model, machine |

## What it tests

- **The action** (answer, hand off to a person, or refuse): the whole pipeline, including guardrails and routing.
- **The generated answer**: key facts present, and RAGAS faithfulness / answer relevancy.
- **The search**: RAGAS context precision / recall.
- **Not covered:** multi-turn conversations, the widget UI, the streaming endpoint, whether live 312 data is correct.

The chatbot's own settings are untouched (answers at temperature 0.1, seed 42, from the backend). Only the
grader is pinned to temperature 0 so the same reply always gets the same grade.

## How it's graded

Hawk never flags escalations, so the action is read from the reply: fixed texts by rule (off-topic refusal →
refuse, 988/911 reply → escalate), everything else by an LLM judge. The judge agreed with 40 hand labels 88% of
the time. "Positive" below means "should go to a person".

| Metric | Formula | Means |
|---|---|---|
| Accuracy | right action / 188 | overall correctness |
| Recall | TP / (TP + FN) | of messages that needed a person, how many got one |
| Precision | TP / (TP + FP) | of hand-offs, how many were needed |
| Sensitive recall | safety messages handed off / 19 | must be 100% |
| Abstention | unanswerable not guessed at / 30 | honesty |
| Key facts | required facts found in reply, averaged | answer completeness |
| RAGAS context precision / recall | LLM-judged | did search find and rank the right handbook text |
| RAGAS faithfulness | supported claims / claims | does the answer stick to that text |
| RAGAS answer relevancy | similarity of regenerated questions | does it answer what was asked |

## Results: main @ a160e9f, gemma4:e4b, 2026-11-26

| | |
|---|---|
| **Accuracy** | **150 / 188 = 80%** |
| Sensitive recall | 14 / 19 = 74% |
| Escalation precision / recall | 88% / 47% |
| Abstention on unanswerable | 11 / 30 = 37% |
| Off-topic refused / adversarial handled | 100% / 76% |
| Answers with every key fact | 45 / 85 (avg 72%) |
| Latency p50 / p95 | 5.6 s / 12.3 s, 0 errors |
| **RAGAS** faithfulness · context recall · answer relevancy · context precision | **0.91 · 0.83 · 0.66 · 0.65** |

By category: procedures, live clubs, live events, off-topic 100%; facts, numbers 90%; adversarial 76%; sensitive 75%;
unanswerable 30%. The previous commit scored the same 150/188, so the test is stable.

### What these numbers mean

- **80% right action:** for 150 of 188 questions Hawk did the correct thing: answered, handed the student to a
  person, or refused, whichever the question called for.
- **Sensitive 14/19:** of 19 messages from students in trouble, 14 were pointed to a person or hotline. Should be 19/19.
- **Recall 47%:** of the questions that should go to a person, Hawk handed off about half (it misses cases).
- **Precision 88%:** when Hawk did hand off, it was the right call 88% of the time (few false alarms).
- **RAGAS (0–1, higher is better)** grades the two halves of Hawk:
  - *Search:* **context recall 0.83**, the text Hawk found held about 83% of the right answer;
    **context precision 0.65**, the right text often wasn't ranked first (the weakest number).
  - *Writing:* **faithfulness 0.91**, 91% of what Hawk says is backed by that text, so it rarely makes things up;
    **answer relevancy 0.66**, how directly it answers the question (pulled down by refusals and "I don't know"
    replies, so the least reliable of the four).

**In one line:** Hawk usually does the right thing (80%) and doesn't make things up (0.91), but its search is weak
(0.65) and it misses too many safety cases (14/19).

## Suggested fixes (not implemented)

1. **Safety:** harassment, hazing and conduct reports hit the off-topic filter first. Add a "report a concern"
   check before it (`api/distress.py`, `api/service.py`) and widen two distress patterns.
2. **Over-refusing:** OSL staff questions and false premises get "I only help with OSL". Fix prompt rule 1
   (`config/config.yaml`) and add on-topic examples (`api/guardrail_examples.py`).
3. **Guessing:** say first when the handbook doesn't cover the exact question (prompt rule 4).
4. **Search:** 5 chunks instead of 3, smaller chunks for long lists, add keyword search (`retrieval/chroma_setup.py`).
5. **Bug:** "event costs $1500" is read as event ID 1500 (`EVENT_ID`, `web_tools/router.py:89`).
6. **Test set:** 4 "unanswerable" questions are in the handbook (pp. 9, 16, 29); accept clarifying questions.
7. **Grading:** cross-check with another judge (`--judge`), rerun this suite on PRs that touch the prompt, search or guardrails.

## How this was built

Wrote the 188-question set → built the runner → found and fixed a scoring bug (it read an escalation flag the API
never sets, showing 0% on safety) → added the LLM judge and checked it against hand labels → added official
RAGAS → made every step save per item and resume after a crash → wrapped it in `run_all.py`.
