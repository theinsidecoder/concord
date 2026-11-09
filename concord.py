#!/usr/bin/env python3
"""
Concord - find where the teams in your company silently contradict each other.

Usage:
    python concord.py demo                         # offline demo: a launch seen by 9 teams
    python concord.py analyze docs/ [more files]   # analyse a folder of .md/.txt/.json documents
    python concord.py brief --role legal [paths]   # one role's personal pre-launch brief
    python concord.py serve                        # local web dashboard
    python concord.py selftest                     # verify everything works

Only the Python standard library is used (Python 3.8+).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    import concord_brain as brain
except ImportError:  # pragma: no cover
    sys.stderr.write("concord_brain.py must sit next to concord.py\n")
    raise SystemExit(2)

VERSION = brain.VERSION
LOGO = r"""
   ____
  / ___|___  _ __   ___ ___  _ __ __| |
 | |   / _ \| '_ \ / __/ _ \| '__/ _` |
 | |__| (_) | | | | (_| (_) | | | (_| |
  \____\___/|_| |_|\___\___/|_|  \__,_|
"""
TEXT_EXT = (".md", ".markdown", ".txt", ".rst")
MAX_FILE = 2 * 1024 * 1024


class ConcordError(Exception):
    """User-facing error."""


# --------------------------------------------------------------------------- #
# Loading documents
# --------------------------------------------------------------------------- #

def _read_text(path: str) -> str:
    if os.path.getsize(path) > MAX_FILE:
        raise ConcordError("%s is larger than 2 MB." % path)
    with open(path, "rb") as fh:
        data = fh.read()
    if b"\x00" in data[:2048]:
        raise ConcordError("%s looks like a binary file." % path)
    return data.decode("utf-8", errors="replace")


def _docs_from_json(text: str, source: str) -> list:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ConcordError("%s is not valid JSON: %s" % (source, e))
    if isinstance(data, dict):
        data = data.get("docs") or data.get("documents")
    if not isinstance(data, list):
        raise ConcordError("%s must hold a list of {\"name\", \"role\", \"text\"} objects." % source)
    return [d for d in data if isinstance(d, dict)]


def load_docs(paths: list) -> list:
    docs = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    full = os.path.join(root, f)
                    if f.startswith("."):
                        continue
                    if f.lower().endswith(TEXT_EXT):
                        docs.append({"name": os.path.relpath(full, p), "text": _read_text(full)})
                    elif f.lower().endswith(".json"):
                        try:
                            docs.extend(_docs_from_json(_read_text(full), full))
                        except ConcordError:
                            pass  # unrelated JSON files in a folder are skipped
        elif os.path.isfile(p):
            text = _read_text(p)
            if p.lower().endswith(".json"):
                docs.extend(_docs_from_json(text, p))
            else:
                docs.append({"name": os.path.basename(p), "text": text})
        else:
            raise ConcordError("Path not found: %s" % p)
    if not docs:
        raise ConcordError("No documents found (looking for %s or .json)." % ", ".join(TEXT_EXT))
    return docs


def run(docs: list) -> dict:
    try:
        return brain.analyze(docs)
    except ValueError as e:
        raise ConcordError(str(e))


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

class Style:
    def __init__(self, on):
        self.on = on

    def _w(self, c, s):
        return "\033[%sm%s\033[0m" % (c, s) if self.on else s

    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def red(self, s): return self._w("31", s)
    def green(self, s): return self._w("32", s)
    def yellow(self, s): return self._w("33", s)
    def blue(self, s): return self._w("34", s)
    def cyan(self, s): return self._w("36", s)

    def sev(self, s):
        fn = {"critical": self.red, "high": self.yellow, "medium": self.blue}.get(s, self.dim)
        return fn(s.upper().ljust(8))


def _clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def _bar(score, width=24):
    filled = int(round(width * score / 100.0))
    return "\u2588" * filled + "\u2591" * (width - filled)


def render_text(rep, color=True, verbose=False) -> str:
    c = Style(color)
    verdict_c = {"ALIGNED": c.green, "NEEDS ALIGNMENT": c.yellow}.get(rep["verdict"], c.red)
    L = [c.bold(c.cyan("Concord %s" % VERSION)) + " - cross-team consistency check",
         c.dim("=" * 74),
         "Alignment : %s %d/100  %s" % (_bar(rep["score"]), rep["score"], c.bold(verdict_c(rep["verdict"]))),
         "Documents : " + ", ".join("%s [%s]" % (d["name"], d["role"]) for d in rep["docs"]),
         "Findings  : " + "  ".join("%s %d" % (k, v) for k, v in rep["counts"].items()),
         "Headline  : " + rep["headline"], ""]
    if rep["wall_map"]:
        L.append(c.bold("WALL MAP - where teams contradict each other most"))
        for w in rep["wall_map"][:6]:
            L.append("  %-26s %s %d" % (" <-> ".join(w["roles"]), "\u2588" * min(w["conflicts"] * 3, 30),
                                        w["conflicts"]))
        L.append("")
    shown = [f for f in rep["findings"] if verbose or f["severity"] in ("critical", "high", "medium")]
    L.append(c.bold("FINDINGS") + c.dim("  (use -v to include low/info items)" if not verbose else ""))
    for f in shown:
        L.append("%s %s  %s" % (c.sev(f["severity"]), c.dim(f["id"]), c.bold(f["title"])))
        for e in f["evidence"][:4]:
            L.append(c.dim("    [%s] %s: " % (e["role"], e["doc"])) + _clip(e["text"], 70))
        L.append("    -> " + f["hint"])
    if not shown:
        L.append("  Nothing significant found.")
    return "\n".join(L)


def render_brief(rep, role, color=True) -> str:
    c = Style(color)
    b = rep["briefs"].get(role)
    if not b:
        raise ConcordError("No documents for role '%s'. Roles found: %s" % (
            role, ", ".join(rep["briefs"]) or "none"))
    L = [c.bold(c.cyan("Concord brief for: %s" % role.upper())), c.dim(b["focus"]), ""]
    if not b["items"]:
        L.append("Nothing needs your attention.")
    for it in b["items"]:
        tag = "YOURS" if it["yours"] else "AFFECTS YOU"
        with_ = (" - align with: " + ", ".join(it["with"])) if it["with"] else ""
        L.append("%s [%s] %s%s" % (c.sev(it["severity"]), tag, it["title"], with_))
        L.append("    -> " + it["hint"])
    return "\n".join(L)


def render_markdown(rep) -> str:
    L = ["# Concord alignment report", "",
         "**Score: %d/100 - %s**  " % (rep["score"], rep["verdict"]), rep["headline"], "",
         "| Severity | Count |", "|---|---|"] + ["| %s | %d |" % kv for kv in rep["counts"].items()]
    if rep["wall_map"]:
        L += ["", "## Wall map", ""] + ["- %s: %d" % (" <-> ".join(w["roles"]), w["conflicts"])
                                        for w in rep["wall_map"]]
    L += ["", "## Findings", ""]
    for f in rep["findings"]:
        L.append("### %s `%s` %s" % (f["id"], f["severity"], f["title"]))
        for e in f["evidence"]:
            L.append("- **%s** (%s): %s" % (e["role"], e["doc"], e["text"]))
        L.append("- _Next step:_ " + f["hint"])
        L.append("")
    L += ["## Role briefs", ""]
    for role, b in rep["briefs"].items():
        L += ["### %s" % role.title(), "_%s_" % b["focus"], ""]
        L += ["- `%s` %s" % (i["severity"], i["title"]) for i in b["items"]] or ["- Nothing needs attention."]
        L.append("")
    return "\n".join(L)


CSS = """
:root{--bg:#f5f7fb;--card:#fff;--fg:#17202e;--mut:#64748b;--bd:#e2e8f0;--acc:#6d28d9}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f17;--card:#141a26;--fg:#e8edf5;--mut:#93a0b4;--bd:#273146;--acc:#a78bfa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px 70px}h1{margin:0;font-size:28px}h2{margin:28px 0 10px;font-size:18px}
.sub{color:var(--mut);margin:4px 0 18px}.hero{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:var(--card);
border:1px solid var(--bd);border-radius:16px;padding:18px}.score{font-size:54px;font-weight:800;line-height:1}
.verdict{font-weight:700;padding:4px 12px;border-radius:999px;display:inline-block}.ALIGNED{background:#dcfce7;color:#166534}
.NEEDS{background:#fef9c3;color:#854d0e}.MISALIGNED{background:#fee2e2;color:#991b1b}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.chip{background:var(--card);border:1px solid var(--bd);border-radius:999px;padding:3px 12px;font-size:13px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.sev{font-size:11px;font-weight:800;letter-spacing:.04em;padding:2px 8px;border-radius:6px;margin-right:8px}
.critical{background:#fee2e2;color:#b91c1c}.high{background:#ffedd5;color:#c2410c}.medium{background:#dbeafe;color:#1d4ed8}
.low,.info{background:#f1f5f9;color:#475569}.ev{margin:6px 0 0;padding:8px 10px;border-left:3px solid var(--acc);background:var(--bg);
border-radius:0 8px 8px 0;font-size:13.5px}.ev b{color:var(--acc)}.hint{margin-top:8px;color:var(--mut);font-size:13px}
.bar{height:10px;border-radius:5px;background:var(--acc);display:inline-block;vertical-align:middle;margin-right:8px}
details{margin-bottom:8px}summary{cursor:pointer;font-weight:600}
"""


def render_html(rep) -> str:
    e = html.escape
    v = rep["verdict"].split()[0]
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Concord report</title>"
           "<style>%s</style></head><body><div class='wrap'><h1>\U0001F91D Concord</h1>"
           "<div class='sub'>Cross-team consistency report &middot; generated locally, zero dependencies</div>" % CSS,
           "<div class='hero'><div class='score'>%d</div><div><span class='verdict %s'>%s</span>"
           "<div style='margin-top:8px'>%s</div></div></div><div class='chips'>" % (
               rep["score"], e(v), e(rep["verdict"]), e(rep["headline"]))]
    out += ["<span class='chip'><b>%d</b> %s</span>" % (n, k) for k, n in rep["counts"].items()]
    out += ["</div><h2>Documents</h2><div class='chips'>"]
    out += ["<span class='chip'>%s &middot; <b>%s</b></span>" % (e(d["name"]), e(d["role"])) for d in rep["docs"]]
    out.append("</div>")
    if rep["wall_map"]:
        out.append("<h2>Wall map</h2><div class='card'>")
        for w in rep["wall_map"][:8]:
            out.append("<div><span class='bar' style='width:%dpx'></span>%s &mdash; %d</div>" % (
                18 * w["conflicts"], e(" \u2194 ".join(w["roles"])), w["conflicts"]))
        out.append("</div>")
    out.append("<h2>Findings</h2>")
    for f in rep["findings"]:
        out.append("<div class='card'><span class='sev %s'>%s</span><b>%s</b>" % (
            e(f["severity"]), e(f["severity"].upper()), e(f["title"])))
        for ev in f["evidence"][:5]:
            out.append("<div class='ev'><b>%s</b> &middot; %s<br>%s</div>" % (
                e(ev["role"]), e(ev["doc"]), e(ev["text"])))
        out.append("<div class='hint'>&rarr; %s</div></div>" % e(f["hint"]))
    out.append("<h2>Role briefs</h2>")
    for role, b in rep["briefs"].items():
        out.append("<details><summary>%s</summary><div class='card'><div class='hint'>%s</div>" % (
            e(role.title()), e(b["focus"])))
        for i in b["items"]:
            out.append("<div style='margin-top:8px'><span class='sev %s'>%s</span>%s<div class='hint'>%s%s</div></div>" % (
                e(i["severity"]), e(i["severity"].upper()), e(i["title"]), e(i["hint"]),
                (" Align with: " + e(", ".join(i["with"]))) if i["with"] else ""))
        if not b["items"]:
            out.append("<div>Nothing needs your attention.</div>")
        out.append("</div></details>")
    out.append("</div></body></html>")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Web dashboard (local only)
# --------------------------------------------------------------------------- #

UI_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Concord</title><style>
""" + CSS + r"""
input,textarea,button,select{font:inherit;border-radius:8px;border:1px solid var(--bd);padding:8px 10px;background:var(--card);color:var(--fg)}
textarea{width:100%;min-height:90px}button{cursor:pointer;background:var(--acc);color:#fff;border:0;font-weight:600}
button.alt{background:var(--card);color:var(--fg);border:1px solid var(--bd)}.row{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.row input{flex:1;min-width:140px}.err{color:#b91c1c;margin:8px 0}
</style></head><body><div class="wrap"><h1>&#129309; Concord</h1>
<div class="sub">Paste what each team wrote. Concord finds where they contradict each other. 100% local.</div>
<div id="docs"></div>
<div class="row"><button class="alt" onclick="addDoc()">+ Add document</button>
<button onclick="go()">Analyse</button><button class="alt" onclick="demo()">Run demo</button></div>
<div id="err" class="err"></div><div id="out"></div></div>
<script>
const $=id=>document.getElementById(id);
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e}
function addDoc(n,r,t){const d=el('div','card');const row=el('div','row');const a=el('input');a.placeholder='Document name (e.g. legal_review.md)';a.value=n||'';
 const b=el('input');b.placeholder='Role (optional: legal, marketing...)';b.value=r||'';const x=el('button','alt','Remove');x.onclick=()=>d.remove();
 row.append(a,b,x);const ta=el('textarea');ta.placeholder='Paste the text here';ta.value=t||'';d.append(row,ta);$('docs').append(d)}
function collect(){return [...$('docs').children].map(d=>{const i=d.querySelectorAll('input');return {name:i[0].value||'document',role:i[1].value,text:d.querySelector('textarea').value}}).filter(d=>d.text.trim())}
async function post(p){$('err').textContent='Analysing...';$('out').replaceChildren();
 try{const r=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
 const d=await r.json();if(!r.ok)throw new Error(d.error||'Request failed');$('err').textContent='';render(d)}catch(e){$('err').textContent=e.message}}
function go(){post({docs:collect()})}function demo(){post({demo:true})}
function render(d){const o=$('out');o.replaceChildren();const h=el('div','hero');h.append(el('div','score',d.score));const v=el('div');
 v.append(el('span','verdict '+d.verdict.split(' ')[0],d.verdict),el('div','',d.headline));h.append(v);o.append(h);
 const ch=el('div','chips');Object.entries(d.counts).forEach(([k,n])=>ch.append(el('span','chip',n+' '+k)));o.append(ch);
 if(d.wall_map.length){o.append(el('h2','','Wall map'));const w=el('div','card');d.wall_map.slice(0,8).forEach(x=>{const r=el('div');const b=el('span','bar');b.style.width=(18*x.conflicts)+'px';
  r.append(b,x.roles.join(' \u2194 ')+' \u2014 '+x.conflicts);w.append(r)});o.append(w)}
 o.append(el('h2','','Findings'));d.findings.forEach(f=>{const c=el('div','card');c.append(el('span','sev '+f.severity,f.severity.toUpperCase()),el('b','',f.title));
  f.evidence.slice(0,5).forEach(e=>{const x=el('div','ev');x.append(el('b','',e.role),' \u00b7 '+e.doc);x.append(document.createElement('br'),e.text);c.append(x)});
  c.append(el('div','hint','\u2192 '+f.hint));o.append(c)});
 o.append(el('h2','','Role briefs'));Object.entries(d.briefs).forEach(([r,b])=>{const dt=el('details');dt.append(el('summary','',r));const c=el('div','card');c.append(el('div','hint',b.focus));
  b.items.forEach(i=>{const x=el('div');x.style.marginTop='8px';x.append(el('span','sev '+i.severity,i.severity.toUpperCase()),i.title,el('div','hint',i.hint+(i.with.length?' Align with: '+i.with.join(', '):'')));c.append(x)});
  if(!b.items.length)c.append('Nothing needs your attention.');dt.append(c);o.append(dt)})}
addDoc('marketing_blog.md','','Our model is 99% accurate and launches March 3.');
addDoc('research_report.md','','Measured accuracy is 87.4%. The launch date is March 14.');
</script></body></html>"""

MAX_BODY = 8 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "Concord/" + VERSION

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, UI_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/health":
            self._send(200, json.dumps({"ok": True, "version": VERSION}))
        else:
            self._send(404, json.dumps({"error": "Not found"}))

    def do_POST(self):
        if self.path != "/api/analyze":
            return self._send(404, json.dumps({"error": "Not found"}))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                raise ConcordError("Request body missing or too large.")
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(req, dict):
                raise ConcordError("Request must be a JSON object.")
            docs = brain.DEMO_DOCS if req.get("demo") else req.get("docs")
            self._send(200, json.dumps(run(docs if isinstance(docs, list) else [])))
        except ConcordError as e:
            self._send(400, json.dumps({"error": str(e)}))
        except (ValueError, UnicodeDecodeError) as e:
            self._send(400, json.dumps({"error": "Invalid input: %s" % e}))
        except Exception as e:
            self._send(500, json.dumps({"error": "Internal error: %s" % e}))


def serve(port=8765, open_browser=True):
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise ConcordError("Cannot start server on port %d (%s). Try --port 9000." % (port, e))
    url = "http://127.0.0.1:%d" % httpd.server_address[1]
    print("Concord dashboard running at %s  (Ctrl+C to stop)" % url)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def selftest() -> int:
    ok, bad = 0, []

    def check(name, cond):
        nonlocal ok
        if cond:
            ok += 1
            print("  [ok]   " + name)
        else:
            bad.append(name)
            print("  [FAIL] " + name)

    print("Concord self-test")
    rep = run(brain.DEMO_DOCS)
    F = rep["findings"]
    types = {f["type"] for f in F}
    title = " | ".join(f["title"] for f in F)
    check("reads all 9 demo documents", len(rep["docs"]) == 9)
    check("detects roles from filenames", {d["role"] for d in rep["docs"]} >=
          {"research", "product", "marketing", "legal", "sales", "security", "engineering", "support", "executive"})
    check("finds accuracy conflict (87.4% vs 95%+)", "87.4%" in title and "95%" in title)
    check("finds latency conflict with bound (<=200 ms)", "latency" in title and "200 ms" in title)
    check("finds price conflict", "$0.40" in title and "$0.30" in title)
    check("finds uptime conflict", "99.9%" in title and "98.2%" in title)
    check("finds customer-count conflict", "40 customers" in title and "12 customers" in title)
    check("finds launch-date conflict", "date_conflict" in types and "March 14" in title and "March 21" in title)
    check("finds SOC 2 status conflict", any(f["type"] == "status_conflict" and "soc" in f["title"] for f in F))
    check("finds 'never hallucinates' contradiction", "contradicted_claim" in types)
    check("flags unsupported superlatives", "unsupported_claim" in types)
    check("does NOT flag target-vs-actual (PRD target 90%)", "90%" not in title)
    check("does NOT flag compatible values (420 ms vs 410 ms)", not [
        f for f in run([{"name": "a.md", "text": "The p95 latency is 420 ms in production."},
                        {"name": "b.md", "text": "The p95 latency is 410 ms in staging."}])["findings"]
        if f["type"] == "metric_conflict"])
    check("extracts risks, questions, decisions, actions", {"risk", "question", "decision", "action"} <= types)
    check("builds wall map", rep["wall_map"] and rep["wall_map"][0]["conflicts"] >= 2)
    check("builds a brief for every role", set(rep["briefs"]) == {d["role"] for d in rep["docs"]})
    check("demo is judged MISALIGNED", rep["verdict"] == "MISALIGNED" and rep["score"] < 40)
    clean = run([{"name": "research.md", "text": "Accuracy is 90% on the benchmark. The launch date is March 14."},
                 {"name": "marketing.md", "text": "We measured 90% accuracy on the benchmark. Launch is March 14."}])
    check("consistent documents score 100", clean["score"] == 100 and not clean["findings"][:0] and
          not [f for f in clean["findings"] if f["severity"] in ("critical", "high")])
    check("front-matter role wins", run([{"name": "x.md", "text": "role: legal\nWe must review the terms today."}])
          ["docs"][0]["role"] == "legal")
    check("bounds logic: 'under 200 ms' vs 150 ms is compatible", not [
        f for f in run([{"name": "a.md", "text": "Latency is under 200 ms for requests."},
                        {"name": "b.md", "text": "Measured latency is 150 ms for requests."}])["findings"]
        if f["type"] == "metric_conflict"])
    check("render text", "WALL MAP" in render_text(rep, color=False))
    check("render brief", "BRIEF" in render_brief(rep, "legal", color=False).upper())
    check("render markdown", "# Concord alignment report" in render_markdown(rep))
    xss = run([{"name": "<script>x</script>.md", "text": "We are 100% safe <img src=x onerror=alert(1)> today."}])
    page = render_html(xss)
    check("html report escapes input", "<script>x" not in page and "<img src=x" not in page)
    check("json output parses", json.loads(json.dumps(rep))["score"] == rep["score"])
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "legal_review.md"), "w", encoding="utf-8") as fh:
            fh.write("Launch is March 3.\nPrice is $0.30 per 1M tokens.")
        with open(os.path.join(tmp, "product_prd.md"), "w", encoding="utf-8") as fh:
            fh.write("Launch is March 14.\nThe price is $0.40 per 1M tokens.")
        with open(os.path.join(tmp, "docs.json"), "w", encoding="utf-8") as fh:
            json.dump([{"name": "sales.md", "text": "We will launch March 3, price $0.30 per 1M tokens."}], fh)
        loaded = load_docs([tmp])
        check("loads folder of .md and .json", len(loaded) == 3)
        check("folder analysis finds conflicts", any(f["type"] == "date_conflict" for f in run(loaded)["findings"]))
    try:
        load_docs(["/definitely/not/here"])
        check("missing path rejected", False)
    except ConcordError:
        check("missing path rejected", True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        with urlopen(base + "/", timeout=10) as r:
            check("web UI served", b"Concord" in r.read())
        req = Request(base + "/api/analyze", data=json.dumps({"demo": True}).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as r:
            check("web API analyses", json.loads(r.read())["verdict"] == "MISALIGNED")
        try:
            urlopen(Request(base + "/api/analyze", data=b"{bad", headers={"Content-Type": "application/json"}))
            check("web API rejects bad JSON", False)
        except HTTPError as e:
            check("web API rejects bad JSON", e.code == 400)
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("\n%d passed, %d failed" % (ok, len(bad)))
    return 1 if bad else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _emit(args, rep, role=None):
    if role:
        text = render_brief(rep, role, color=sys.stdout.isatty() and not args.out)
    elif args.format == "json":
        text = json.dumps(rep, indent=2, ensure_ascii=False)
    elif args.format == "md":
        text = render_markdown(rep)
    elif args.format == "html":
        out = args.out or "concord-report.html"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(render_html(rep))
        print("HTML report written to %s" % os.path.abspath(out))
        return
    else:
        text = render_text(rep, sys.stdout.isatty() and not args.out and not os.environ.get("NO_COLOR"),
                           args.verbose)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("Report written to %s" % os.path.abspath(args.out))
    else:
        print(text)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["text", "json", "md", "html"], default="text")
    common.add_argument("--out", help="write the report to this file")
    common.add_argument("-v", "--verbose", action="store_true", help="include low/info findings")
    p = argparse.ArgumentParser(prog="concord", description="Find where your teams contradict each other.")
    p.add_argument("--version", action="version", version="Concord " + VERSION)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("demo", parents=[common], help="run on a built-in launch scenario (offline)")
    a = sub.add_parser("analyze", parents=[common], help="analyse documents or folders")
    a.add_argument("paths", nargs="+")
    b = sub.add_parser("brief", parents=[common], help="personal pre-launch brief for one role")
    b.add_argument("--role", required=True)
    b.add_argument("paths", nargs="*", help="documents (default: built-in demo)")
    s = sub.add_parser("serve", help="start the local web dashboard")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true")
    sub.add_parser("selftest", help="verify the installation")
    return p


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        os.system("")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        print(LOGO)
        parser.print_help()
        return 0
    try:
        if args.cmd == "selftest":
            return selftest()
        if args.cmd == "serve":
            serve(args.port, not args.no_open)
            return 0
        role = None
        if args.cmd == "demo":
            docs = brain.DEMO_DOCS
        elif args.cmd == "brief":
            docs = load_docs(args.paths) if args.paths else brain.DEMO_DOCS
            role = brain.normalize_role(args.role)
        else:
            docs = load_docs(args.paths)
        _emit(args, run(docs), role)
        return 0
    except ConcordError as e:
        sys.stderr.write("error: %s\n" % e)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
