"""la why "<error text>": known causes from the KB, each with a command to confirm it.

The text can also be piped in (a log): every line is matched.
"""
from . import out, search

MAX_INPUT = 2_000_000


def match(kb, text):
    """[(entry, matched string, line it was on)] best first. Matching ignores case."""
    found = {}
    lines = text.splitlines() or [text]
    for e in kb.errors:
        for m in e["match"]:
            ml = m.lower()
            for line in lines:
                if ml in line.lower():
                    prev = found.get(e["id"])
                    if not prev or len(m) > len(prev[1]):
                        found[e["id"]] = (e, m, line.strip())
                    break
    # A general entry ("Permission denied") gives way to a specific one that matched the
    # same line ("Permission denied (publickey)").
    hits = list(found.values())
    hits = [h for h in hits if not any(o is not h and o[2] == h[2] and h[1].lower() in o[1].lower()
                                       and len(o[1]) > len(h[1]) for o in hits)]
    return sorted(hits, key=lambda t: -len(t[1]))


def show_entry(rel, kb, e, matched=None, line=None):
    out.say(f"{e['title']}  [KB ✅ {e['id']}]")
    if line and matched and line.lower() != matched.lower():
        out.say(f"matched: {line[:200]}", indent=2)
    for n, c in enumerate(e["causes"], 1):
        ok = rel.release in c.get("verified", [])
        out.say(f"{n}. {c['cause']}", indent=2, hang=5)
        if ok:
            out.say(f"check: {c['cmd']}   {out.label(c['danger'])}", indent=5, hang=12)
        else:
            out.say(f"check: (not checked on {rel.release}) {c['cmd']}", indent=5, hang=12)
        if c.get("fix"):
            out.say(f"fix: {c['fix']}", indent=5, hang=10)
        if c.get("fix_cmd"):
            out.say(c["fix_cmd"], indent=10)
    if e.get("note"):
        out.say(e["note"], indent=2)


def run(rel, kb, text, from_stdin=False):
    text = text[:MAX_INPUT]
    hits = match(kb, text)
    if not hits:
        out.say("No KB error entry matches this text.")
        results = search.search(kb, rel, text, limit=3) if not from_stdin else []
        if results:
            out.say("Closest KB matches by word:")
            for h in results:
                if h["kind"] == "card":
                    out.say(f"la show {h['id']}", indent=2)
                elif h["kind"] == "guide":
                    out.say(f"la guide {h['id']}", indent=2)
                elif h["kind"] == "error":
                    out.say(f"la why \"{kb_error(kb, h['id'])['match'][0]}\"", indent=2)
        out.say('With AI: la ask "<your question>" (paste the error into the question).')
        return 1
    limit = 5 if from_stdin else 3
    if from_stdin:
        out.say(f"{len(hits)} known error type(s) found in the input. Showing {min(len(hits), limit)}.")
        print()
    for i, (e, m, line) in enumerate(hits[:limit]):
        if i:
            print()
        show_entry(rel, kb, e, m, line)
    return 0


def kb_error(kb, eid):
    return next(e for e in kb.errors if e["id"] == eid)
