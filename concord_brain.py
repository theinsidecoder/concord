#!/usr/bin/env python3
"""
concord_brain.py - the AI engine of Concord.

Concord reads the documents that different roles of a company write about the SAME
launch (research evals, PRDs, marketing posts, legal reviews, sales decks, security
reviews, support FAQs, executive memos...) and finds the places where those teams
silently disagree: conflicting numbers, conflicting dates, claims that are stated as
done by one team and still in progress for another, absolute claims with no evidence,
and claims that the company's own data contradicts.

Pure Python standard library. No network. No model download. Deterministic.

    from concord_brain import analyze
    report = analyze([{"name": "prd.md", "text": "..."}, {"name": "blog.md", "text": "..."}])
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# Embedded knowledge
# --------------------------------------------------------------------------- #

ROLE_ORDER = ["executive", "product", "research", "engineering", "security", "legal",
              "marketing", "sales", "support"]
EXTERNAL_ROLES = {"marketing", "sales", "support"}      # words that reach customers
EVIDENCE_ROLES = {"research", "engineering", "security", "legal"}

ROLE_ALIASES = {
    "executive": "exec executive board ceo cto coo cfo leadership strategy memo staff",
    "product": "product prd pm roadmap spec",
    "research": "research eval evals evaluation science scientist ml model results",
    "engineering": "eng engineering infra infrastructure platform dev backend sre ops notes",
    "security": "security sec redteam pentest trust",
    "legal": "legal counsel compliance policy privacy review",
    "marketing": "marketing brand blog pr comms press launchpost announcement",
    "sales": "sales deck gtm pitch enterprise",
    "support": "support faq helpdesk cs success",
}
ROLE_ALIASES = {r: set(v.split()) for r, v in ROLE_ALIASES.items()}

ROLE_KEYWORDS = {
    "executive": "strategy revenue board budget market investor competitor headcount okr risk decision reputation",
    "product": "roadmap user feature requirement launch adoption retention priority scope milestone onboarding",
    "research": "benchmark eval evaluation accuracy dataset training ablation baseline metric hallucination "
                "robustness experiment regression",
    "engineering": "latency throughput deploy infrastructure uptime api scaling gpu cluster incident rollback "
                   "capacity pipeline migration",
    "security": "vulnerability threat breach soc pentest jailbreak redteam encryption authentication attack "
                "certification audit injection",
    "legal": "liability compliance gdpr copyright license regulation contract privacy terms indemnity "
             "disclosure advertising claim",
    "marketing": "announce brand campaign blog press messaging audience positioning headline launch",
    "sales": "pricing price customer deal enterprise quota prospect discount renewal demo sla",
    "support": "ticket faq escalation complaint onboarding helpdesk staffing macros",
}

ROLE_FOCUS = {
    "executive": "Where could the launch embarrass the company or miss the business goal?",
    "product": "Are scope, dates and promises consistent with what can actually ship?",
    "research": "Is the evidence being quoted correctly, and are weak results being hidden?",
    "engineering": "Do commitments match measured capacity, latency and readiness?",
    "security": "Which security claims are real, and which risks remain unmitigated?",
    "legal": "Which statements create liability, and what must be substantiated?",
    "marketing": "Which claims are safe to publish, and what is the one true number?",
    "sales": "What can you promise a customer without it coming back as a dispute?",
    "support": "What will customers ask about, and what do you honestly know?",
}

STOPWORDS = frozenset(
    """a an the and or but if then else when while of to in on at by for with from as is are was
    were be been being it its this that these those i me my we our you your he she they them their
    do does did done have has had will would can could should may might just also very so than too
    into over about out up down off again there here what which who whom how why all any both each
    few more most other some such only own same s t not no nor per via get got one like use used
    using""".split()
)


def stem(w: str) -> str:
    if len(w) <= 3:
        return w
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("ly", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            if suf == "s" and w.endswith(("ss", "us", "is")):
                continue
            return w[: len(w) - len(suf)] + rep
    return w


_WORD = re.compile(r"[a-z0-9_]+")
UNIT_WORDS = {"ms", "millisecond", "second", "sec", "minute", "hour", "hr", "day", "percent", "week", "month",
              "year"}


def tokenize(text: str) -> list:
    out = []
    for w in _WORD.findall((text or "").lower().replace("\u2019", "'")):
        if len(w) < 2 or w in STOPWORDS or w.isdigit():
            continue
        s = stem(w)
        if s not in UNIT_WORDS:
            out.append(s)
    return out


METRIC_WORDS = {stem(w) for w in
                "accuracy latency price pricing cost uptime throughput language revenue error rate memory "
                "context window parameter score recall precision hallucination speed availability sla "
                "downtime".split()}
TOPIC_PREFIXES = ("hallucinat", "error", "bias", "downtim", "vulnerab", "bug", "risk", "breach")

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

_MON = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATE_A = re.compile(r"\b" + _MON + r"\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>\d{4}))?", re.I)
DATE_B = re.compile(r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r"\b(?:,?\s+(?P<year>\d{4}))?", re.I)
DATE_ISO = re.compile(r"\b(?P<year>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}

MILESTONES = [("beta", re.compile(r"\bbeta\b", re.I)),
              ("launch", re.compile(r"\b(launch\w*|releas\w*|ship\w*|go[- ]?live|general availability|ga|"
                                    r"goes live|rollout|roll out)\b", re.I)),
              ("deadline", re.compile(r"\b(deadline|due|freeze|cutoff)\b", re.I))]

NUM_RE = re.compile(
    r"(?<![\w.\-/])(?P<cur>[$\u20ac\u00a3])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<plus>\+)?"
    r"(?:(?P<tight>%|[kmb]\b|x\b|s\b)"
    r"|\s?(?P<word>ms|milliseconds?|seconds?|secs?|minutes?|hours?|hrs?|days?|weeks?|months?|years?|"
    r"million|billion|thousand|percent|gb|tb|mb|qps|rps)\b"
    r"|\s+(?P<noun>[a-z][a-z\-]+))?", re.I)

TIME_MS = {"ms": 1, "millisecond": 1, "milliseconds": 1, "s": 1e3, "sec": 1e3, "secs": 1e3, "second": 1e3,
           "seconds": 1e3, "minute": 6e4, "minutes": 6e4, "hour": 3.6e6, "hours": 3.6e6, "hr": 3.6e6,
           "hrs": 3.6e6, "day": 8.64e7, "days": 8.64e7, "week": 6.048e8, "weeks": 6.048e8,
           "month": 2.592e9, "months": 2.592e9, "year": 3.15e10, "years": 3.15e10}
MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9}
BYTES = {"mb": 1e6, "gb": 1e9, "tb": 1e12}

TARGET_RE = re.compile(r"\b(target|goal|aim\w*|budget|projected|forecast|estimated?|roadmap|hope)\b", re.I)
GE_RE = re.compile(r"\b(over|above|exceed\w*|more than|at least|greater than|minimum|beyond)\W*$", re.I)
LE_RE = re.compile(r"\b(under|below|less than|at most|up to|within|no more than|maximum|fewer than|"
                   r"lower than)\W*$", re.I)
AP_RE = re.compile(r"\b(about|around|approximately|roughly|nearly|almost)\W*$|~\s*$", re.I)

ABS_RE = re.compile(
    r"\b(never|always|guarantee[ds]?|perfect(ly)?|flawless(ly)?|completely safe|fully secure|unbreakable|"
    r"bulletproof|risk[- ]free|no risk|100\s?%|zero (hallucinations?|errors?|bias|risk|downtime|bugs?)|"
    r"no (hallucinations?|errors?|bias|downtime|bugs?|vulnerabilit\w+)|"
    r"best|fastest|most (accurate|powerful|capable|advanced|secure|reliable|trusted)|"
    r"state[- ]of[- ]the[- ]art|industry[- ]leading|world[- ]class|breakthrough|revolutionary|"
    r"outperforms? (all|every)\w*|#1|ever built)\b|\b100%", re.I)
EVIDENCE_RE = re.compile(r"\b(source|sources|benchmark\w*|eval\w*|study|studies|report|cited?|citation|"
                         r"according to|footnote|appendix|third[- ]party|audited|measured)\b|\[\d+\]|https?://",
                         re.I)

POS_STATUS = re.compile(r"\b(certified|approved|compliant|completed?|launched|shipped|released|available|"
                        r"ready|passed|signed off|live|resolved|fixed|done)\b", re.I)
NEG_STATUS = re.compile(r"\b(in progress|pending|not yet|not (?:been )?(?:certified|approved|completed?|"
                        r"released|available|ready|fixed|resolved|deployed)|awaiting|blocked|failed|failing|"
                        r"planned|draft|under review|unresolved|still|on hold|delayed)\b", re.I)

DECISION_RE = re.compile(r"\b(we (have )?decided|decision\s*:|agreed|signed off|no-go|go decision|"
                         r"we (will|shall) ship|do not claim)\b", re.I)
ACTION_RE = re.compile(r"\b(todo|action item|owner\s*:|assigned to|needs? to be|must be|before [a-z]+ \d{1,2})\b|"
                       r"@\w+\s+(to|will)\b", re.I)
RISK_RE = re.compile(r"\b(risk|concern|blocker|blocked|vulnerab\w*|incident|regression|breach|"
                     r"not yet deployed|unresolved|exposure|liability|reputational)\b", re.I)
QUESTION_RE = re.compile(r"(\?\s*$)|\b(tbd|to be determined|open question|unclear|unknown)\b", re.I)
OWNER_RE = re.compile(r"(@\w+)|\bowner\s*:\s*([A-Za-z][\w .-]{1,30})", re.I)
META_RE = re.compile(r"^\s*(role|team|department|title|author)\s*:\s*(.+?)\s*$", re.I)

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
PENALTY = {"critical": 12, "high": 7, "medium": 3, "low": 1, "info": 0}
ROLE_ALWAYS = {"legal": ("contradicted_claim", "unsupported_claim", "status_conflict"),
               "security": ("status_conflict", "contradicted_claim"),
               "research": ("contradicted_claim",)}
HINTS = {
    "metric_conflict": "Agree on ONE number and name its source of truth.",
    "date_conflict": "Confirm ONE date and broadcast it to every team.",
    "status_conflict": "Verify the real status before anyone states it externally.",
    "unsupported_claim": "Add evidence or soften the wording.",
    "contradicted_claim": "Remove or correct the claim - the company's own data contradicts it.",
    "risk": "Assign an owner and a mitigation.",
    "decision": "Make sure every affected team knows this decision.",
    "action": "Confirm owner and deadline.",
    "question": "Get an answer before launch.",
}


# --------------------------------------------------------------------------- #
# Document parsing
# --------------------------------------------------------------------------- #

def normalize_role(value: str) -> str:
    v = re.sub(r"[^a-z0-9 _-]+", "", (value or "").lower()).strip()
    if not v:
        return ""
    for role, aliases in ROLE_ALIASES.items():
        if v == role or v in aliases:
            return role
    return re.sub(r"[ _]+", "-", v)[:24]


def detect_role(name: str, text: str, explicit: str = "", meta: dict = None) -> tuple:
    """Return (role, how). Order: explicit > front-matter > filename > keywords."""
    r = normalize_role(explicit)
    if r:
        return r, "given"
    for key in ("role", "team", "department"):
        r = normalize_role((meta or {}).get(key, ""))
        if r:
            return r, "front-matter"
    stem_name = re.sub(r"\.[A-Za-z0-9]+$", "", name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).lower()
    parts = [p for p in re.split(r"[^a-z0-9]+", stem_name) if p]
    for p in parts:
        r = normalize_role(p)
        if r in ROLE_ALIASES:
            return r, "filename"
    toks = set(tokenize(text))
    best, score = "general", 1
    for role, words in ROLE_KEYWORDS.items():
        s = len(toks & {stem(w) for w in words.split()})
        if s > score:
            best, score = role, s
    return best, ("keywords" if best != "general" else "unknown")


def split_sentences(lines: list) -> list:
    out = []
    for line in lines:
        t = line.strip()
        if not t or re.fullmatch(r"[-=*_`~\s|]{3,}", t) or t.startswith("#"):
            continue
        t = re.sub(r"^(?:[-*+>]\s+|\d+[.)]\s+|\[[ xX]\]\s+)+", "", t)
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", t)
        t = re.sub(r"[*`]+", "", t)
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[$@])", t):
            s = s.strip()
            if len(s.split()) >= 3:
                out.append(s)
    return out


def parse_doc(raw: dict, index: int) -> dict:
    name = str(raw.get("name") or "document-%d" % (index + 1))
    text = str(raw.get("text") or raw.get("body") or "")
    lines, meta = text.splitlines(), {}
    for k, line in enumerate(lines[:8]):
        m = META_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2)
            lines[k] = ""
    role, how = detect_role(name, text, str(raw.get("role") or ""), meta)
    sents = split_sentences(lines)
    return {"name": name, "role": role, "role_source": how, "sentences": sents}


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def _mask_dates(s: str):
    spans, dates = [], []
    for rx, kind in ((DATE_A, "a"), (DATE_B, "b"), (DATE_ISO, "iso")):
        for m in rx.finditer(s):
            if any(m.start() < e and m.end() > b for b, e in spans):
                continue
            if kind == "iso":
                mo, d, y = int(m.group("m")), int(m.group("d")), int(m.group("year"))
            else:
                word = m.group("mon")
                if word.lower() == "may" and word[0] != "M":
                    continue
                mo, d = MONTHS[word.lower()[:3]], int(m.group("day"))
                y = int(m.group("year")) if m.group("year") else None
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            spans.append((m.start(), m.end()))
            dates.append((mo, d, y, m.group(0)))
    masked = s
    for b, e in spans:
        masked = masked[:b] + " " * (e - b) + masked[e:]
    return masked, dates


def extract_dates(s: str, masked_dates: list) -> list:
    out = []
    if not masked_dates:
        return out
    key = next((k for k, rx in MILESTONES if rx.search(s)), None)
    if not key:
        return out
    for mo, d, y, raw in masked_dates:
        out.append({"milestone": key, "month": mo, "day": d, "year": y, "raw": raw})
    return out


def extract_metrics(s: str, masked: str) -> list:
    out = []
    for m in NUM_RE.finditer(masked):
        num = float(m.group("num").replace(",", ""))
        cur, plus = m.group("cur"), bool(m.group("plus"))
        tight = (m.group("tight") or "").lower()
        word = (m.group("word") or "").lower()
        noun = (m.group("noun") or "").lower()
        before = masked[: m.start()]
        if re.search(r"\bper\s*$", before, re.I):
            continue  # pricing basis such as "per 1M tokens"
        extra = []
        if cur:
            unit, val = "usd", num * MULT.get(tight, MULT.get(word, 1))
        elif tight == "%" or word == "percent":
            unit, val = "pct", num
        elif tight == "x":
            unit, val = "x", num
        elif tight == "s" or word in TIME_MS:
            unit, val = "time", num * TIME_MS.get(word or "s")
        elif word in BYTES:
            unit, val = "bytes", num * BYTES[word]
        elif word in ("qps", "rps"):
            unit, val = "rate", num
        elif tight in ("k", "m", "b") or word in ("million", "billion", "thousand"):
            mult = MULT.get(tight) or MULT.get(word, 1)
            if noun and noun not in STOPWORDS and len(noun) >= 3:
                unit = "count:" + stem(noun)
                extra.append(stem(noun))
            else:
                unit = "count"
            val = num * mult
        elif noun and noun not in STOPWORDS and len(noun) >= 3 and not (1900 <= num <= 2100):
            unit, val = "count:" + stem(noun), num
            extra.append(stem(noun))
        else:
            continue
        clause = re.split(r"[;:,]", before)[-1]
        tail = before[-25:]
        if TARGET_RE.search(clause):
            qual = "tg"
        elif plus or GE_RE.search(tail):
            qual = "ge"
        elif LE_RE.search(tail):
            qual = "le"
        elif AP_RE.search(tail):
            qual = "ap"
        else:
            qual = "eq"
        ctx = set(tokenize(before)[-5:]) | set(tokenize(masked[m.end():])[:5]) | set(extra)
        out.append({"value": val, "unit": unit, "qual": qual, "ctx": ctx, "raw": m.group(0).strip()})
    return out


def _tol(unit: str, v: float, loose: bool) -> float:
    if unit == "pct":
        return 3.0 if loose else 1.0
    return abs(v) * (0.10 if loose else 0.03)


def compatible(a: dict, b: dict) -> bool:
    qa, qb, va, vb, unit = a["qual"], b["qual"], a["value"], b["value"], a["unit"]
    eq = ("eq", "ap")
    tol = _tol(unit, max(abs(va), abs(vb)), "ap" in (qa, qb))
    if qa in eq and qb in eq:
        return abs(va - vb) <= tol
    if qa in eq:  # make `a` the bounded one
        a, b, qa, qb, va, vb = b, a, qb, qa, vb, va
    if qb in eq:
        return vb >= va - tol if qa == "ge" else vb <= va + tol
    if qa == qb:
        return True
    ge, le = (va, vb) if qa == "ge" else (vb, va)
    return ge <= le + tol


def context_ok(a: dict, b: dict) -> bool:
    shared = a["ctx"] & b["ctx"]
    return len(shared) >= 2 or any(t in METRIC_WORDS for t in shared)


def fmt_value(m: dict) -> str:
    v, u = m["value"], m["unit"]
    if u == "pct":
        s = ("%g%%" % v)
    elif u == "usd":
        s = ("$%.2f" % v) if v < 100 else ("$%g" % v)
    elif u.startswith("count:"):
        s = m["raw"]
    elif u == "time":
        s = ("%gs" % (v / 1e3)) if v >= 1000 and v < 8.64e7 else ("%g ms" % v if v < 1000 else "%g days" % (v / 8.64e7))
    else:
        s = "%g" % v
    return {"ge": "\u2265 ", "le": "\u2264 ", "ap": "\u2248 "}.get(m["qual"], "") + s


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #

class _Builder:
    def __init__(self):
        self.items = []

    def add(self, ftype, severity, title, detail, evidence, extra=None):
        roles = sorted({e["role"] for e in evidence})
        toks = set()
        for e in evidence:
            toks.update(tokenize(e["text"]))
        item = {"id": "F%03d" % (len(self.items) + 1), "type": ftype, "severity": severity,
                "title": title, "detail": detail, "evidence": evidence, "roles": roles,
                "hint": HINTS[ftype], "_tokens": toks}
        if extra:
            item.update(extra)
        self.items.append(item)


def _ev(doc, sentence):
    return {"doc": doc["name"], "role": doc["role"], "text": sentence}


def _short(s: str, n: int = 90) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def find_findings(docs: list) -> list:
    B = _Builder()
    mentions, dates, absolutes = [], [], []

    for d in docs:
        sents = d["sentences"]
        for i, s in enumerate(sents):
            masked, dts = _mask_dates(s)
            for m in extract_metrics(s, masked):
                m.update(doc=d, sentence=s)
                mentions.append(m)
            for dt in extract_dates(s, dts):
                dt.update(doc=d, sentence=s)
                dates.append(dt)
            for am in ABS_RE.finditer(s):
                nxt = sents[i + 1] if i + 1 < len(sents) else ""
                absolutes.append({"doc": d, "sentence": s, "match": am.group(0),
                                  "evidence": bool(EVIDENCE_RE.search(s) or EVIDENCE_RE.search(nxt))})
                break

    # ---- metric conflicts (union-find over conflicting mention pairs) ---- #
    by_unit = defaultdict(list)
    for idx, m in enumerate(mentions):
        if m["qual"] != "tg":
            by_unit[m["unit"]].append(idx)
    parent = list(range(len(mentions)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = 0
    for idxs in by_unit.values():
        for p in range(len(idxs)):
            for q in range(p + 1, len(idxs)):
                a, b = mentions[idxs[p]], mentions[idxs[q]]
                if a["doc"] is b["doc"] or not context_ok(a, b) or compatible(a, b):
                    continue
                parent[find(idxs[p])] = find(idxs[q])
                edges += 1
    groups = defaultdict(list)
    if edges:
        in_conflict = set()
        for idxs in by_unit.values():
            for p in range(len(idxs)):
                for q in range(p + 1, len(idxs)):
                    a, b = mentions[idxs[p]], mentions[idxs[q]]
                    if a["doc"] is not b["doc"] and context_ok(a, b) and not compatible(a, b):
                        in_conflict.update((idxs[p], idxs[q]))
        for i in in_conflict:
            groups[find(i)].append(i)
    for members in groups.values():
        ms = [mentions[i] for i in sorted(members)]
        freq = Counter(t for m in ms for t in m["ctx"])
        key = [t for t, _ in freq.most_common(8) if t in METRIC_WORDS or freq[t] >= 2][:3] or \
              [t for t, _ in freq.most_common(2)]
        values = []
        for m in ms:
            v = fmt_value(m)
            if v not in values:
                values.append(v)
        roles = {m["doc"]["role"] for m in ms}
        sev = "critical" if (roles & EXTERNAL_ROLES and roles & (EVIDENCE_ROLES | {"product", "executive"})) \
            else "high"
        evid, seen = [], set()
        for m in ms:
            k = (m["doc"]["name"], m["sentence"])
            if k not in seen:
                seen.add(k)
                evid.append(_ev(m["doc"], m["sentence"]))
        B.add("metric_conflict", sev,
              "Conflicting numbers for \u201c%s\u201d: %s" % (" ".join(key), " vs ".join(values)),
              "%d documents quote different values for the same metric." % len({m["doc"]["name"] for m in ms}),
              evid, {"values": values})

    # ---- date conflicts ---- #
    by_ms = defaultdict(list)
    for dt in dates:
        by_ms[dt["milestone"]].append(dt)
    for ms_key, lst in by_ms.items():
        distinct = {}
        for dt in lst:
            distinct.setdefault((dt["month"], dt["day"]), []).append(dt)
        if len(distinct) < 2 or len({dt["doc"]["name"] for dt in lst}) < 2:
            continue
        label = "%s date" % ms_key
        vals = ["%s (%s)" % (v[0]["raw"], ", ".join(sorted({x["doc"]["role"] for x in v})))
                for v in distinct.values()]
        evid, seen = [], set()
        for dt in lst:
            k = (dt["doc"]["name"], dt["sentence"])
            if k not in seen:
                seen.add(k)
                evid.append(_ev(dt["doc"], dt["sentence"]))
        B.add("date_conflict", "high", "Teams disagree on the %s: %s" % (label, " vs ".join(vals)),
              "%d different dates are circulating." % len(distinct), evid, {"values": vals})

    # ---- status conflicts ---- #
    sent_info = []
    for d in docs:
        for s in d["sentences"]:
            if s.rstrip().endswith("?"):
                continue
            neg = bool(NEG_STATUS.search(s))
            pos = bool(POS_STATUS.search(NEG_STATUS.sub(" ", s)))
            if neg == pos:
                continue
            core = POS_STATUS.sub(" ", NEG_STATUS.sub(" ", s))
            sent_info.append((d, s, pos, set(tokenize(core))))
    seen_pairs = set()
    for i, (da, sa, pa, ta) in enumerate(sent_info):
        for db, sb, pb, tb in sent_info[i + 1:]:
            if da is db or pa == pb:
                continue
            shared = ta & tb
            if len(shared) < 2:
                continue
            pair = (da["name"], sa, db["name"], sb)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            claimer = da if pa else db
            sev = "critical" if claimer["role"] in EXTERNAL_ROLES else "high"
            B.add("status_conflict", sev,
                  "Status disagreement on \u201c%s\u201d" % " ".join(
                      [t for t in tokenize(sa) if t in shared][:4]),
                  "One team states it as done; another says it is still open.",
                  [_ev(da, sa), _ev(db, sb)])

    # ---- absolute / unsupported / contradicted claims ---- #
    for a in absolutes:
        d, s = a["doc"], a["sentence"]
        low = s.lower()
        topic = next((p for p in TOPIC_PREFIXES if p in low), None)
        contra = None
        if topic:
            for m in mentions:
                if (m["doc"] is not d and m["value"] > 0 and m["unit"] in ("pct", "count:" + topic)
                        and any(t.startswith(topic) for t in m["ctx"])):
                    contra = m
                    break
        if contra:
            B.add("contradicted_claim", "critical",
                  "Claim contradicted by internal data: \u201c%s\u201d" % _short(s, 70),
                  "%s reports %s for this topic." % (contra["doc"]["name"], fmt_value(contra)),
                  [_ev(d, s), _ev(contra["doc"], contra["sentence"])])
        elif not a["evidence"]:
            sev = "high" if d["role"] in EXTERNAL_ROLES else "medium"
            B.add("unsupported_claim", sev,
                  "Absolute/superlative claim with no evidence: \u201c%s\u201d" % a["match"],
                  "No source, benchmark or citation found next to this statement.", [_ev(d, s)])

    # ---- risks, decisions, actions, questions ---- #
    for d in docs:
        for s in d["sentences"]:
            for rx, ftype, sev in ((DECISION_RE, "decision", "info"), (QUESTION_RE, "question", "low"),
                                   (ACTION_RE, "action", "info"), (RISK_RE, "risk", "medium")):
                if rx.search(s):
                    owner = ""
                    om = OWNER_RE.search(s)
                    if om:
                        owner = (om.group(1) or om.group(2) or "").strip()
                    B.add(ftype, sev, _short(s), "", [_ev(d, s)], {"owner": owner})
                    break
    order = sorted(B.items, key=lambda f: (SEV_ORDER[f["severity"]], f["id"]))
    for n, f in enumerate(order, 1):
        f["id"] = "F%03d" % n
    return order


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def build_briefs(findings: list, roles: list) -> dict:
    briefs = {}
    for role in roles:
        kws = {stem(w) for w in ROLE_KEYWORDS.get(role, "").split()}
        scored = []
        for f in findings:
            involved = role in f["roles"]
            overlap = min(3, len(f["_tokens"] & kws))
            informational = f["type"] in ("decision", "action", "risk", "question")
            if informational and not (involved and f["type"] in ("action", "question", "risk")) and overlap < 2:
                continue
            always = (f["type"] in ROLE_ALWAYS.get(role, ())) or \
                     (role == "executive" and f["severity"] == "critical")
            if not (involved or overlap or always):
                continue
            score = (5 if involved else 0) + overlap + (3 if always else 0) + (4 - SEV_ORDER[f["severity"]]) * 2
            if score > 3:
                scored.append((score, f))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        items = [{"id": f["id"], "severity": f["severity"], "type": f["type"], "title": f["title"],
                  "hint": f["hint"], "with": [r for r in f["roles"] if r != role],
                  "yours": role in f["roles"]} for _, f in scored[:6]]
        briefs[role] = {"focus": ROLE_FOCUS.get(role, "What do you need to know before launch?"), "items": items}
    return briefs


def analyze(raw_docs: list) -> dict:
    if not isinstance(raw_docs, list) or not raw_docs:
        raise ValueError("Provide a list of documents, each like {'name': ..., 'text': ...}.")
    docs = [parse_doc(r, i) for i, r in enumerate(r for r in raw_docs if isinstance(r, dict))]
    docs = [d for d in docs if d["sentences"]]
    if len(docs) < 1:
        raise ValueError("No readable text found in the documents.")
    findings = find_findings(docs)
    penalty = sum(PENALTY[f["severity"]] for f in findings)
    score = int(round(100 * math.exp(-penalty / 60.0)))
    verdict = "ALIGNED" if score >= 85 else "NEEDS ALIGNMENT" if score >= 60 else "MISALIGNED"
    walls = Counter()
    for f in findings:
        if f["type"] in ("metric_conflict", "date_conflict", "status_conflict", "contradicted_claim"):
            rs = f["roles"]
            for i in range(len(rs)):
                for j in range(i + 1, len(rs)):
                    walls[(rs[i], rs[j])] += 1
    roles = [r for r in ROLE_ORDER if any(d["role"] == r for d in docs)] + \
            sorted({d["role"] for d in docs} - set(ROLE_ORDER))
    counts = Counter(f["severity"] for f in findings)
    top = [f["title"] for f in findings if f["severity"] in ("critical", "high")][:3]
    headline = ("%d cross-team problems found; top issue: %s" % (
        sum(1 for f in findings if f["severity"] in ("critical", "high")), top[0])) if top else \
        "No cross-team contradictions detected."
    briefs = build_briefs(findings, roles)
    for f in findings:
        f.pop("_tokens", None)
    return {
        "tool": "concord", "version": VERSION, "score": score, "verdict": verdict, "headline": headline,
        "docs": [{"name": d["name"], "role": d["role"], "role_source": d["role_source"],
                  "sentences": len(d["sentences"])} for d in docs],
        "counts": {k: counts.get(k, 0) for k in ("critical", "high", "medium", "low", "info")},
        "findings": findings,
        "wall_map": [{"roles": list(k), "conflicts": v} for k, v in walls.most_common()],
        "briefs": briefs,
    }


# --------------------------------------------------------------------------- #
# Built-in demo: the launch of a fictional model, as seen by 9 different teams
# --------------------------------------------------------------------------- #

DEMO_DOCS = [
    {"name": "research_eval_report.md", "text": """# Nimbus-2 Evaluation Report
On our held-out benchmark, Nimbus-2 reaches 87.4% accuracy.
The hallucination rate is 3.1% on the factuality suite, which is a regression for long documents.
The p95 latency is 420 ms on production traffic.
Nimbus-2 supports 32 languages with evaluated quality.
Robustness testing is not yet complete and the red-team results are pending.
Open question: should we publish the weaker long-document results?
Decision: do not claim parity with competitors until the long-document regression is fixed.
"""},
    {"name": "product_prd.md", "text": """# Nimbus-2 PRD
Target accuracy for general availability is 90%.
The launch date is March 14.
We will support 32 languages at launch.
The price is $0.40 per 1M tokens.
Owner: @priya to finalize the onboarding flow before March 1.
"""},
    {"name": "marketing_launch_blog.md", "text": """# Introducing Nimbus-2
Nimbus-2 is the most accurate model ever built, with over 95% accuracy.
Nimbus-2 never hallucinates.
Responses arrive with latency under 200 ms.
Available in 50+ languages from day one.
Launching March 3.
We are SOC 2 Type II certified.
Pricing starts at just $0.30 per 1M tokens.
"""},
    {"name": "legal_review.md", "text": """# Legal review
Marketing claims about accuracy must be substantiated before publication.
Absolute statements about hallucinations create false advertising exposure.
The terms of service for the EU launch are still in draft.
Open question: do we need a transparency notice under EU rules?
Action item: @maria to review the blog post before March 10.
"""},
    {"name": "sales_deck.md", "text": """# Enterprise sales deck
Nimbus-2 comes with a 99.9% uptime SLA, guaranteed.
The pilot is live with 40 customers.
Customers can start on the first day of launch.
"""},
    {"name": "security_review.md", "text": """# Security review
SOC 2 Type II certification is still in progress and the audit completes in Q4.
Jailbreak testing found 14 high-severity prompts that bypass safeguards.
Risk: prompt-injection mitigations are not yet deployed.
Open question: who owns the incident response runbook for launch?
"""},
    {"name": "engineering_notes.md", "text": """# Engineering notes
Measured uptime is 98.2% over the last 30 days.
The p95 latency is 410 ms in staging.
The rollback plan is ready.
The migration to the new inference cluster is blocked by a GPU shortage.
We can ship March 21 at the earliest.
"""},
    {"name": "support_faq.md", "text": """# Support FAQ draft
Customers will be told that Nimbus-2 is fully secure.
Ticket volume is expected to triple after launch and the staffing plan is not yet approved.
"""},
    {"name": "exec_memo.md", "text": """# Leadership memo
Decision: we ship Nimbus-2 on March 3 to beat our biggest competitor.
We currently have 12 customers in the pilot.
The board expects $5M revenue this quarter.
Risk: reputational damage if the launch claims are challenged.
Is the legal team comfortable with the current messaging?
"""},
]
