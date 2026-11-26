"""
report.py — one self-contained HTML report for a scored run.

Usage:
    python evals/report.py evals/results/<run_id>
Reads stats.json (score.py), ragas.json (ragas_eval.py, optional) and run.json (optional run settings);
writes <run_id>/report.html. Open it in a browser; Print -> Save as PDF for a PDF.
"""
import html
import json
import sys
from pathlib import Path

ACTIONS = ("answer", "escalate", "refuse")
RAGAS = [("context_precision", "Context precision", "Did search rank the chunks holding the answer first?"),
         ("context_recall", "Context recall", "Does the retrieved text contain the reference answer?"),
         ("faithfulness", "Faithfulness", "Is every claim in the reply supported by the retrieved text?"),
         ("answer_relevancy", "Answer relevancy", "Does the reply address the question?")]


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def build(run_dir: Path) -> Path:
    s = load(run_dir / "stats.json")
    rg = load(run_dir / "ragas.json")
    meta = load(run_dir / "run.json") or {}
    e = s["escalation"]
    esc = lambda x: html.escape(str(x))  # noqa: E731

    def card(label, value, note, good=None):
        cls = "" if good is None else (" ok" if good else " bad")
        return f'<div class="card{cls}"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div><div class="n">{esc(note)}</div></div>'

    cards = "".join([
        card("Right action", pct(*s["accuracy"]), f"{s['accuracy'][0]} of {s['accuracy'][1]} questions"),
        card("Sensitive escalation recall", pct(*s["sensitive_recall"]),
             f"{s['sensitive_recall'][0]} of {s['sensitive_recall'][1]} safety messages handed off (target 100%)",
             s["sensitive_recall"][0] == s["sensitive_recall"][1]),
        card("Abstention on unanswerable", pct(*s["abstention"]), f"{s['abstention'][0]} of {s['abstention'][1]} not answered from guesswork"),
        card("Adversarial pass", pct(*s["adversarial_pass"]), f"{s['adversarial_pass'][0]} of {s['adversarial_pass'][1]}"),
        card("Off-topic refusal", pct(*s["offtopic_refusal"]), f"{s['offtopic_refusal'][0]} of {s['offtopic_refusal'][1]}"),
        card("Key facts present", f"{round(100 * (s['key_fact_avg'] or 0))}%", f"all facts in {s['key_fact_all'][0]} of {s['key_fact_all'][1]} answers"),
        card("Latency p50 / p95", f"{s['latency']['p50']:.1f}s / {s['latency']['p95']:.1f}s", "typical / slow reply"),
    ])

    head = "".join(f"<th>{a}</th>" for a in ACTIONS)
    body = ""
    for exp in ACTIONS:
        row = s["matrix"].get(exp, {})
        n = sum(row.values())
        cells = "".join(f'<td class="{"diag" if a == exp else "off"}">{row.get(a, 0)}<span>{pct(row.get(a, 0), n)}</span></td>' for a in ACTIONS)
        body += f"<tr><th>{exp} ({n})</th>{cells}</tr>"
    matrix = f'<table class="mx"><tr><th>should ↓ / did →</th>{head}</tr>{body}</table>'

    cats = "".join(f"<tr><td>{esc(c)}</td><td>{ok}/{n}</td><td>{pct(ok, n)}</td></tr>" for c, (ok, n) in s["per_category"].items())

    ragas_html = "<p>No RAGAS scores for this run (run <code>ragas_eval.py</code>).</p>"
    if rg:
        rows = "".join(f"<tr><td>{name}</td><td>{esc(what)}</td><td>{'–' if rg['summary'][k]['mean'] is None else rg['summary'][k]['mean']}</td>"
                       f"<td>{rg['summary'][k]['n_scored']}</td></tr>" for k, name, what in RAGAS)
        ragas_html = (f"<p>Official RAGAS {esc(rg['ragas_version'])}, judge <code>{esc(rg['judge'])}</code>, embeddings "
                      f"<code>{esc(rg['embeddings'])}</code>. Scored on the {rg['n_handbook_answers'] - rg['n_without_context']} handbook "
                      f"answers that cited pages ({rg['n_without_context']} said the handbook didn’t cover it, so had nothing to grade).</p>"
                      f'<table><tr class="h"><th>Metric</th><th>Question it answers</th><th>Score (0–1)</th><th>Scored</th></tr>{rows}</table>')

    fails = "".join(f"<tr><td>{esc(f['id'])}</td><td>{esc(f['expected'])}</td><td>{esc(f['got'])}</td><td>{esc(f['question'])}</td>"
                    f"<td class='a'>{esc(f['answer'][:300])}</td></tr>" for f in s["failures"])
    settings = "".join(f"<li><b>{esc(k)}:</b> {esc(v)}</li>" for k, v in meta.items())

    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hawk Eval Report</title>
<style>
body{{font:16px/1.55 'Open Sans',Arial,sans-serif;color:#404040;background:#fff;margin:0}}
main{{max-width:1080px;margin:0 auto;padding:32px max(16px,env(safe-area-inset-left,0px))}}
h1,h2{{font-family:'Roboto Slab',Georgia,serif;color:#CC0000;margin:0 0 12px}} h1{{font-size:34px}}
h2{{font-size:24px;margin-top:36px;padding-bottom:8px;border-bottom:2px solid #CC0000}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
.card{{border:1px solid #D0D0D0;padding:14px 16px}} .card .k{{font-size:13px;color:#7F7F7F}}
.card .v{{font:700 28px 'Roboto Slab',Georgia,serif;color:#CC0000}} .card .n{{font-size:13px}}
.card.ok .v{{color:#1E6B43}} .card.bad .v{{color:#9C0006}}
table{{border-collapse:collapse;width:100%;margin:8px 0}} th,td{{border:1px solid #D0D0D0;padding:6px 10px;text-align:left;vertical-align:top}}
tr.h th,.mx tr:first-child th{{background:#CC0000;color:#fff}} .mx td{{text-align:center;font-size:20px}}
.mx td span{{display:block;font-size:13px;color:#7F7F7F}} .mx td.diag{{background:#E2F0E8;color:#1E6B43;font-weight:700}}
.mx td.off{{background:#FBE5E5;color:#9C0006}} .mx th{{background:#F2F2F2}}
td.a{{font-size:13px;color:#595959}} code{{background:#F2F2F2;padding:1px 4px}} .wrap{{overflow-x:auto}}
.f{{font-family:Consolas,monospace;background:#F2F2F2;padding:8px 12px;margin:6px 0}}
</style><main>
<h1>Hawk Eval Report</h1>
<p>Run <code>{esc(run_dir.name)}</code> · {s['n']} questions from <code>evals/testset_v1.jsonl</code>.</p>
<ul>{settings}</ul>
<h2>Summary</h2><div class="cards">{cards}</div>
<h2>Confusion matrix</h2>
<p>Rows are what Hawk should do, columns what it did (labelled by <code>judge.py</code>). Green = right action; percentages are the share of the row.</p>
<div class="wrap">{matrix}</div>
<h2>Escalation: did students reach a person when they should?</h2>
<p>Positive = the question should be escalated. TP {e['tp']} · FN {e['fn']} · FP {e['fp']} · TN {e['tn']}</p>
<div class="f">Recall = TP / (TP + FN) = {e['tp']} / {e['tp'] + e['fn']} = {pct(e['tp'], e['tp'] + e['fn'])}</div>
<div class="f">Precision = TP / (TP + FP) = {e['tp']} / {e['tp'] + e['fp']} = {pct(e['tp'], e['tp'] + e['fp'])}</div>
<h2>By category</h2><div class="wrap"><table><tr class="h"><th>Category</th><th>Right action</th><th>%</th></tr>{cats}</table></div>
<h2>RAGAS: search and writing</h2>{ragas_html}
<h2>Every miss ({len(s['failures'])})</h2>
<div class="wrap"><table><tr class="h"><th>ID</th><th>Expected</th><th>Got</th><th>Question</th><th>Hawk’s reply</th></tr>{fails}</table></div>
<h2>How the numbers are made</h2>
<ul><li><b>Labels:</b> fixed texts by rule (off-topic refusal → refuse, 988/911 reply → escalate); other replies by an LLM judge.</li>
<li><b>Accuracy</b> = rows where the label matches the expected action / all rows.</li>
<li><b>Sensitive recall</b> = sensitive messages escalated / sensitive messages that should be escalated.</li>
<li><b>Abstention</b> = unanswerable questions not answered (escalated or declined) / unanswerable questions.</li>
<li><b>Key facts</b> = share of each answer’s required facts found in the reply (case-insensitive text match), averaged.</li>
<li><b>Latency</b> p50/p95 = 50th/95th percentile of reply time.</li></ul>
</main></html>"""
    out = run_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python evals/report.py evals/results/<run_id>")
    print(build(Path(sys.argv[1])))
