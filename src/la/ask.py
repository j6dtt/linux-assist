"""la ask "<question>": the model answers from KB and man page excerpts; every command
it gives is checked against this release's man pages before it is shown.

  question -> retrieve sources -> mask -> model (JSON) -> check each command
           -> all pass: 🟡 / some fail: retry once with the errors -> still failing: 🔴
  model unreachable -> say why, then show the KB search (la find) instead.
"""
import json
import os
import re
import sys

from . import danger, find, llm, out, search
from .mask import Masker
from .manpage import first_sentence
from .verify import PAGED, check_line

MAX_SOURCES_CHARS = 12000
MAX_INPUT_CHARS = 6000
MAN_OPTIONS = 40


def log(verbose, text):
    if verbose:
        print(f"[la ask] {text}", file=sys.stderr)


# ---------- retrieval ----------

def _card_block(rel, kb, name):
    c = kb.cards[name]
    here, _ = kb.examples_for(c, rel.release)
    lines = [f"[kb:{name}] {name}: {c['summary']}", f"Examples checked on Ubuntu {rel.release}:"]
    lines += [f"  {ex['cmd']}   # {ex['does']} ({ex['danger']})" for ex in here]
    if c.get("pitfalls"):
        lines.append("Watch out: " + " ".join(c["pitfalls"]))
    return "\n".join(lines)


def _man_block(page, limit=2500, rel=None):
    lines = [f"[man:{page.ref}] {page.describe()}"]
    syn = page.synopsis_text(4)
    if syn:
        lines.append("Synopsis: " + " | ".join(syn))
    opts = []
    for tag, desc in page.options_list()[:MAN_OPTIONS]:
        opts.append(f"  {tag}: {first_sentence(desc, 100)}" if desc else f"  {tag}")
    if opts:
        lines.append("Options:")
        lines += opts
    subs = _subcommands(page, rel)
    if subs:
        lines.append("Subcommands: " + ", ".join(subs))
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0]


def _subcommands(page, rel=None):
    """The COMMANDS a group page lists (kubectl, docker compose), or the subcommand pages
    a docker group page links to (docker-container-ls(1) -> ls)."""
    start = page.headings.get("COMMANDS")
    if start is not None:
        subs = []
        for line in page.lines[start + 1:]:
            if line and not line[0].isspace():
                break
            m = re.match(r"^ {7}([a-z][\w-]*)$", line)
            if m:
                subs.append(m.group(1))
        if subs:
            return subs
    pre = re.escape(page.name) + "-"
    subs = sorted(set(re.findall(r"(?<![\w-])" + pre + r"([a-z][a-z0-9]*)\(1\)",
                                 re.sub(r"-\n\s*", "-", re.sub(r"‐\n\s*", "", page.text)))))
    if not subs and rel and page.name in PAGED:      # docker(1): "Use docker help"
        subs = sorted({n[len(page.name) + 1:] for n, sec, _ in rel.whatis()
                       if sec == "1" and re.fullmatch(pre + r"[a-z][a-z0-9]*", n)})
    return subs


def _sub_pages(rel, name, texts):
    """docker / kubectl pages for the subcommands written in texts: 'kubectl logs' -> kubectl-logs."""
    found = []
    for t in texts:
        for m in re.finditer(r"\b" + re.escape(name) + r"((?:\s+[a-z][\w-]*){1,3})", t):
            words = m.group(1).split()
            for n in range(len(words), 0, -1):
                p = rel.man("-".join([name] + words[:n]))
                if p:
                    if p not in found:
                        found.append(p)
                    break
    return found[:2]


def _guide_block(rel, kb, gid):
    g = kb.guides[gid]
    lines = [f"[guide:{gid}] {g['title']}"]
    for n, st in enumerate(g["steps"], 1):
        lines.append(f"  {n}. {st['say']}: {st['cmd']} ({st['danger']})")
    return "\n".join(lines)


def _error_block(kb, eid):
    e = next(x for x in kb.errors if x["id"] == eid)
    lines = [f"[error:{eid}] {e['title']}"]
    for c in e["causes"]:
        fix = f"; fix: {c['fix']}" if c.get("fix") else ""
        lines.append(f"  cause: {c['cause']}; check: {c['cmd']}{fix}")
    return "\n".join(lines)


# Commands that are also everyday words: only taken as a command when written like one
# (`find`, or followed by a flag: find -name).
ENGLISH = {"find", "top", "last", "who", "w", "file", "more", "less", "sort", "head", "tail",
           "cut", "watch", "time", "free", "join", "split", "test", "yes", "install", "look",
           "which", "see", "touch", "date", "sync", "link", "write", "wall", "script", "column",
           "kill", "mount", "stat", "id", "nice", "uptime", "groups", "users", "fold", "expand",
           "factor", "shuf", "tac", "comm", "paste", "dir", "pr", "tr", "ln", "cat", "less"}
MIN_SCORE = 1.5     # weaker word matches are left out of the model's sources


def _named_commands(rel, kb, text):
    seen = []
    for m in re.finditer(r"[A-Za-z][\w.+-]*", text):
        w = m.group(0)
        if w in ENGLISH and not (text[max(0, m.start() - 1):m.start()] == "`"
                                 or re.match(r"\s+-", text[m.end():])):
            continue
        if w in kb.cards or (w in rel.commands and rel.man(w)):
            if w not in seen and w not in search.STOP:
                seen.append(w)
    return seen[:4]


def retrieve(rel, kb, question):
    """(blocks, ids, hits). ids are the source names the model may cite."""
    hits = [h for h in search.search(kb, rel, question, limit=6) if h["score"] >= MIN_SCORE]
    blocks, ids = [], []
    cards = [h["id"] for h in hits if h["kind"] == "card"]
    for name in _named_commands(rel, kb, question):
        if name in kb.cards and name not in cards:
            cards.insert(0, name)
    mans = [h["name"] for h in hits if h["kind"] == "man"]
    for name in _named_commands(rel, kb, question):
        if name not in kb.cards and name not in mans:
            mans.insert(0, name)
    for name in cards[:5]:
        blocks.append(_card_block(rel, kb, name))
        ids.append(f"kb:{name}")
    # Option lists for the first commands, so the model can pick real flags.
    for name in (cards[:2] + mans[:2]):
        card = kb.cards.get(name)
        page = None
        for ref in (card or {}).get("man", []):
            page = page or rel.man_ref(ref)
        page = page or rel.man(name)
        pages = [page] if page else []
        if name in PAGED:
            # The subcommands named in the question, or used by the best KB example.
            texts = [question] + [h["example"]["cmd"] for h in hits
                                  if h["kind"] == "card" and h["id"] == name and h.get("example")]
            pages = _sub_pages(rel, name, texts) + pages
        for p in pages:
            if f"man:{p.ref}" not in ids:
                blocks.append(_man_block(p, rel=rel))
                ids.append(f"man:{p.ref}")
    for h in hits:
        if h["kind"] == "guide":
            blocks.append(_guide_block(rel, kb, h["id"]))
            ids.append(f"guide:{h['id']}")
        elif h["kind"] == "error":
            blocks.append(_error_block(kb, h["id"]))
            ids.append(f"error:{h['id']}")
    kept, total = [], 0
    for b, i in zip(blocks, ids):
        if total + len(b) > MAX_SOURCES_CHARS:
            continue
        kept.append((b, i))
        total += len(b)
    return [b for b, _ in kept], [i for _, i in kept], hits


# ---------- prompt ----------

def system_prompt(release):
    return f"""You help a Linux admin on Ubuntu {release} servers find the right commands.
Rules:
- Answer from the SOURCES. Prefer the KB example commands exactly as written.
- Use only commands from Ubuntu {release}'s stock packages, and only flags you see in the
  SOURCES or are certain exist in that release's man page. Every command is checked
  against the man pages, and a command with an unknown flag is rejected.
- Write unknown values as placeholders in angle brackets, e.g. <DEVICE> or <USER>.
  Never invent host names, IP addresses, users or paths.
- One command line per step, no comments, no explanations inside the command.
  Read-only checks first, then changes, then a check that the change worked.
- Put commands only in steps, never in the answer text.
- If the SOURCES do not cover the question and you are not certain, say so and give no steps.
Reply with JSON only, nothing before or after it:
{{"answer": "2 to 4 plain sentences", "steps": [{{"cmd": "one command line", "why": "what it does, one sentence", "source": "one id from SOURCES, or none"}}], "not_covered": "what the SOURCES did not cover, or empty"}}"""


def user_prompt(question, blocks, pasted):
    parts = ["SOURCES:", "\n\n".join(blocks) if blocks else "(none found)", "", f"QUESTION: {question}"]
    if pasted:
        parts += ["", "OUTPUT THE ADMIN PASTED:", pasted]
    return "\n".join(parts)


# ---------- check ----------

class Step:
    def __init__(self, cmd, why, source):
        self.cmd, self.why, self.source = cmd, why, source
        self.kb = None          # (card, example) when it is exactly a KB example
        self.problems = []
        self.pages = []
        self.level, self.reason = "modify", ""

    @property
    def ok(self):
        return not self.problems


def check_steps(rel, kb, steps):
    for st in steps:
        st.problems, st.pages = [], []
        if "\n" in st.cmd.strip():
            st.problems.append("more than one line")
            continue
        match = kb.match_example(st.cmd, rel.release)
        if match:
            st.kb = match
            st.level = match[1]["danger"]
            continue
        try:
            results = check_line(rel, st.cmd)
        except ValueError as e:
            st.problems.append(f"the line can't be read ({e})")
            continue
        if not results:
            st.problems.append("no command in the line")
        for r in results:
            st.problems += r.problems
            if r.pages:
                st.pages.append(r.pages[0].cite)
        try:
            st.level, st.reason = danger.rate(st.cmd)
        except ValueError:
            st.level, st.reason = "modify", ""
    return steps


def to_steps(reply, masker, ids):
    steps = []
    for s in reply.get("steps") or []:
        if not isinstance(s, dict) or not isinstance(s.get("cmd"), str) or not s["cmd"].strip():
            continue
        cmd = masker.show(s["cmd"].strip().strip("`").strip())
        cmd = re.sub(r"^\$\s+", "", cmd)
        source = str(s.get("source") or "none").strip("[] ")
        steps.append(Step(cmd, masker.show(str(s.get("why") or "")), source if source in ids else "none"))
    return steps


# ---------- run ----------

def run(rel, kb, question, pasted="", verbose=False, env=None):
    env = os.environ if env is None else env
    s = llm.Settings(env)
    host = env.get("LA_HOSTNAME", "").strip()
    masker = Masker(words=[w for w in env.get("LA_MASK_WORDS", "").split(",")] +
                    [host, host.split(".")[0]],
                    on=env.get("LA_MASK", "on").strip().lower() != "off")
    blocks, ids, hits = retrieve(rel, kb, question)
    log(verbose, f"Found {len(ids)} source(s) for the question: {', '.join(ids) or 'none'}.")
    problem = s.problem()
    if problem:
        return _fallback(rel, kb, question, hits, problem)
    pasted = (pasted or "")[-MAX_INPUT_CHARS:]
    q, p = masker.hide(question), masker.hide(pasted)
    if masker.counts:
        log(verbose, f"Masked before sending: {masker.summary()}.")
    messages = [{"role": "system", "content": system_prompt(rel.release)},
                {"role": "user", "content": user_prompt(q, blocks, p)}]
    waiting = sys.stderr.isatty() and not verbose
    try:
        if waiting:
            print(f"Asking {s.model} …", end="", file=sys.stderr, flush=True)
        log(verbose, f"Sent the question to {s.model} at {s.url} "
                     f"({sum(len(m['content']) for m in messages):,} characters).")
        text, secs, tokens = llm.chat(s, messages)
        log(verbose, f"The model answered in {secs:.1f} s ({tokens} tokens).")
        reply = llm.parse_json(text)
        steps = check_steps(rel, kb, to_steps(reply, masker, ids))
        failed = [st for st in steps if not st.ok]
        retried = False
        if failed:
            log(verbose, f"Checked {len(steps)} command(s): {len(failed)} failed. Asking the model once more.")
            fix = "\n".join(f"- {masker.hide(st.cmd)}: {'; '.join(st.problems)}" for st in failed)
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content":
                          f"These commands failed the man page check on Ubuntu {rel.release}:\n{fix}\n"
                          "Fix them using only documented commands and flags, or drop those steps. "
                          "Reply with the whole JSON again."}]
            try:
                text2, secs2, tokens2 = llm.chat(s, messages)
                reply2 = llm.parse_json(text2)
                steps2 = check_steps(rel, kb, to_steps(reply2, masker, ids))
                reply, steps, secs, retried = reply2, steps2, secs + secs2, True
                log(verbose, f"Second answer in {secs2:.1f} s: "
                             f"{sum(1 for x in steps if not x.ok)} of {len(steps)} command(s) still fail.")
            except llm.LLMError as e:
                log(verbose, f"The second try failed ({e}); showing the first answer.")
        else:
            log(verbose, f"Checked {len(steps)} command(s): all passed.")
    except llm.LLMError as e:
        if waiting:
            print("\r\033[K", end="", file=sys.stderr, flush=True)
        return _fallback(rel, kb, question, hits, str(e))
    if waiting:
        print("\r\033[K", end="", file=sys.stderr, flush=True)
    return _show(rel, s, masker, reply, steps, ids, secs, retried)


def _show(rel, s, masker, reply, steps, ids, secs, retried):
    model = s.model.split("/")[-1]
    out.say(f"la ask · Ubuntu {rel.release} · {model}")
    out.say(f"🟡 Written by the AI. Every command below was checked against the Ubuntu "
            f"{rel.release} man pages; read each one before you run it.", hang=3)
    answer = masker.show(str(reply.get("answer") or "")).strip()
    if answer:
        print()
        out.say(answer)
    bad = 0
    if steps:
        print()
        for n, st in enumerate(steps, 1):
            out.say(f"{n}. {st.cmd}", hang=3)
            if st.why:
                out.say(st.why, indent=5)
            if st.ok:
                where = (f"✅ KB example ({st.kb[0]})" if st.kb else
                         f"🟡 checked in {', '.join(dict.fromkeys(st.pages))}" if st.pages else "🟡 checked")
                out.say(f"{out.label(st.level)} · {where}", indent=5)
            else:
                bad += 1
                out.say(f"🔴 do not run: {'; '.join(st.problems)}", indent=5, hang=7)
    cited = sorted({st.source for st in steps if st.source != "none"})
    print()
    if cited:
        out.say("Sources: " + ", ".join(cited))
    elif ids:
        out.say("Sources given to the model: " + ", ".join(ids))
    nc = masker.show(str(reply.get("not_covered") or "")).strip()
    if nc:
        out.say(f"Not covered by the sources: {nc}")
    if masker.counts:
        out.say(f"Masked before sending: {masker.summary()}. The real values are shown above.")
    out.say(f"Answered in {secs:.1f} s" + (" (the first answer failed the check; asked once more)." if retried else "."))
    if bad:
        out.say(f"🔴 {bad} command(s) failed the man page check: they may not work on Ubuntu {rel.release}.")
        return 1
    if not steps:
        out.say("No commands in this answer.")
    return 0


def _fallback(rel, kb, question, hits, reason):
    out.say(f"The AI is not available: {reason}")
    out.say("Showing the KB search instead (la find):")
    print()
    if not hits:
        out.say("Not found: nothing in the KB or the man page names matches those words.")
        return 3
    find.show_hits(kb, hits)
    return 3


__all__ = ["run", "retrieve", "check_steps", "Step", "json"]
