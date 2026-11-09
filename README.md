<div align="center">

```
   ____
  / ___|___  _ __   ___ ___  _ __ __| |
 | |   / _ \| '_ \ / __/ _ \| '__/ _` |
 | |__| (_) | | | | (_| (_) | | | (_| |
  \____\___/|_| |_|\___\___/|_|  \__,_|
```

# 🤝 Concord

### The zero-dependency AI that finds where your teams silently contradict each other

*Research says 87%. Marketing says 95%. Sales promises 99.9% uptime. Engineering measured 98.2%.*
*Concord finds the walls between roles **before** your customers, regulators or press do.*

![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)
![Offline](https://img.shields.io/badge/works-offline%20%26%20private-8A2BE2)
![Tests](https://img.shields.io/badge/self--test-31%2F31-success)

[**Quick start**](#-quick-start) · [**What it finds**](#-what-concord-finds) · [**Example**](#-example-output) · [**Role briefs**](#-role-briefs) · [**How it works**](#-how-it-works) · [**Limits**](#️-honest-limitations)

</div>

---

## 😩 The problem

At every AI company, a launch is described by **nine different teams in nine different documents**:

| Team | Writes | Cares about |
|---|---|---|
| 🔬 Research | eval reports | what the model *actually* scores |
| 🛠️ Engineering | readiness notes | measured latency, uptime, capacity |
| 📦 Product | PRD | scope, price, date |
| ⚖️ Legal | review memo | what creates liability |
| 🛡️ Security | audit notes | what is certified vs. still open |
| 📣 Marketing | launch blog | the boldest true-sounding sentence |
| 💼 Sales | deck | what a customer can be promised |
| 🎧 Support | FAQ | what customers will ask |
| 👔 Executives | memo | the date and the story |

Nobody reads all nine side by side. So numbers drift, dates diverge, a certification that is *"in progress"* becomes *"certified"*, and "never hallucinates" ships next to an eval that says 3.1%. This is not a money problem — it is a **communication-wall problem**, and more budget does not read documents for you.

## ✨ The solution

**Concord** reads what every role wrote and shows **exactly where they disagree**, who is on each side, how risky it is, and what each role must do about it — in seconds, 100 % offline, in **two Python files with zero dependencies**.

```bash
python concord.py demo
```

## 🚀 Quick start

Needs only **Python 3.8+**. No `pip install`, no API key, no internet.

```bash
git clone https://github.com/theinsidecoder/concord.git
cd concord

python concord.py selftest                  # 31 checks - verify everything works
python concord.py demo                      # a launch seen by 9 teams
python concord.py analyze ./launch-docs     # YOUR documents (folder, files, or .json)
python concord.py brief --role legal ./launch-docs
python concord.py serve                     # local web dashboard
```

> Don't want to clone? Download just `concord.py` and `concord_brain.py` into one folder.

## 🔍 What Concord finds

| | Finding | Example caught in the demo |
|---|---|---|
| 🔢 | **Conflicting numbers** (understands `over`, `under`, `50+`, `≈`, `$`, `%`, `ms`, `s`, counts) | Research `87.4%` vs marketing `over 95%` accuracy |
| 📅 | **Conflicting dates** for launch / beta / deadlines | March 3 vs March 14 vs March 21 — four teams, three dates |
| 🚦 | **Status contradictions** (done vs. in progress) | Blog: *"SOC 2 certified"* — Security: *"still in progress"* |
| 💥 | **Claims your own data contradicts** | *"Never hallucinates"* vs. eval hallucination rate of 3.1 % |
| 🚩 | **Absolute claims with no evidence** | *"most accurate model ever built"*, *"guaranteed"*, *"fully secure"* |
| ⚠️ | **Risks · ❓ open questions · ✅ decisions · 📌 actions** (with `@owners`) | Collected from every document into one place |
| 🧱 | **Wall map** | Which pairs of teams contradict each other the most |
| 📊 | **Alignment score** | 0–100 with verdict: `ALIGNED` / `NEEDS ALIGNMENT` / `MISALIGNED` |

It deliberately **ignores targets/goals** (a PRD target of 90 % is not a conflict with a measured 87 %) and treats compatible values as compatible (`under 200 ms` vs `150 ms` is fine).

## 🖥️ Example output

Real output of `python concord.py demo`:

```text
Concord 1.0.0 - cross-team consistency check
==========================================================================
Alignment : ███░░░░░░░░░░░░░░░░░░░░░ 11/100  MISALIGNED
Findings  : critical 7  high 4  medium 5  low 4  info 4

WALL MAP - where teams contradict each other most
  marketing <-> research     █████████ 3
  engineering <-> marketing  ██████ 2
  marketing <-> product      ██████ 2

CRITICAL F001  Conflicting numbers for “nimbus accuracy”: 87.4% vs ≥ 95%
    [research] research_eval_report.md: On our held-out benchmark, Nimbus-2 reaches 87.4% accuracy.
    [marketing] marketing_launch_blog.md: Nimbus-2 is the most accurate model ever built, with over 95%…
    -> Agree on ONE number and name its source of truth.
CRITICAL F002  Conflicting numbers for “latency p95”: 420 ms vs ≤ 200 ms vs 410 ms
CRITICAL F004  Conflicting numbers for “uptime sla”: 99.9% vs 98.2%
CRITICAL F006  Status disagreement on “soc type ii”
CRITICAL F007  Claim contradicted by internal data: “Nimbus-2 never hallucinates.”
HIGH     F008  Teams disagree on the launch date: March 14 (product) vs March 3 (executive, marketing) vs March 21 (engineering)
```

## 🎯 Role briefs

Every role gets **its own** pre-launch brief — what is *yours* to fix and what *affects you*:

```bash
python concord.py brief --role legal ./launch-docs
```

```text
Concord brief for: LEGAL
Which statements create liability, and what must be substantiated?

CRITICAL [AFFECTS YOU] Claim contradicted by internal data: “Nimbus-2 never hallucinates.” - align with: marketing, research
    -> Remove or correct the claim - the company's own data contradicts it.
HIGH     [AFFECTS YOU] Absolute/superlative claim with no evidence: “fully secure” - align with: support
```

## 📖 Feeding it your documents

- **Files / folders** of `.md`, `.txt`, `.rst` — folders are scanned recursively.
- **JSON**: a list like `[{"name": "blog.md", "role": "marketing", "text": "..."}]`.
- **Which role wrote it?** Concord figures it out from, in order: `--role`/JSON `role` → front-matter (`role: legal` in the first lines) → filename (`legal_review.md`, `sales_deck.md`) → vocabulary.
- Export Google Docs / Notion / Confluence pages to Markdown or text and drop them in a folder.

| Command | Purpose |
|---|---|
| `analyze PATH...` | Full report. `--format text\|json\|md\|html`, `--out FILE`, `-v` for low/info items |
| `brief --role ROLE [PATH...]` | One role's brief |
| `demo` | Built-in nine-team scenario |
| `serve [--port N] [--no-open]` | Local dashboard (binds to `127.0.0.1` only) |
| `selftest` | 31 built-in checks |

Use it as a library:

```python
from concord_brain import analyze
report = analyze([{"name": "prd.md", "text": "..."}, {"name": "blog.md", "text": "..."}])
print(report["score"], report["findings"][0]["title"])
```

## 🧠 How it works

```
 documents ─► role detection ─► sentence splitting
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
  metric extractor           date/milestone              claim & status
  (value + unit +            extractor                   analyzers
   qualifier + context)           │                      (absolutes, evidence,
        │                         │                       done vs in-progress)
        └─► conflict engine ◄─────┘                           │
             (interval logic, context matching,               │
              union-find grouping)  ◄─────────────────────────┘
                         │
                         ▼
     severity ─► alignment score ─► wall map ─► per-role briefs
```

Classic NLP and interval logic - **no black box, no model weights, deterministic**: the same documents always give the same report, and every rule is readable in `concord_brain.py`.

## ⚠️ Honest limitations

- It finds **likely** contradictions to review, not proven truths. Expect some false positives and misses — a human decides.
- It is rule-based and **English-first**; it focuses on numbers, dates, status words and absolute claims. It does not understand deep reasoning or tables/images.
- A contradiction is only found when two documents describe the same metric with overlapping wording.
- It cannot know which document is *right*; it tells you who must talk.
- Nothing is uploaded anywhere. Keep confidential documents out of git (`docs/` is in `.gitignore`).

## 📁 Project layout

```
concord/
├── concord.py          # CLI, reports (text/md/html/json), web dashboard, self-test
├── concord_brain.py    # analysis engine + embedded knowledge & demo launch
├── README.md
├── LICENSE             # MIT
└── .gitignore
```

## 🤝 Contributing

Add roles, patterns, languages, or smarter rules. Golden rule: **zero dependencies**. Run `python concord.py selftest` before a PR.

## 👤 Author

Created and maintained by **[theinsidecoder](https://github.com/theinsidecoder)**.

## 📜 License

[MIT](LICENSE) — free for personal and commercial use.

<div align="center">

**If Concord saved you from shipping a contradiction, give it a ⭐**

</div>
