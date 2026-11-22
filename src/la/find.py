"""la find "<task in plain words>": KB cards, guides and errors first, then man page names."""
from . import out, search


def show_hits(kb, hits):
    for n, h in enumerate(hits, 1):
        if n > 1:
            print()
        if h["kind"] == "card":
            c = kb.cards[h["id"]]
            out.say(f"{n}. {h['id']} — {c['summary']}  [KB ✅]", hang=3)
            ex = h.get("example")
            if ex:
                out.cmd(ex["cmd"], indent=5)
                out.say(f"{ex['does']}. {out.label(ex['danger'])}", indent=5)
            out.say(f"more: la show {h['id']}", indent=5)
        elif h["kind"] == "guide":
            g = kb.guides[h["id"]]
            out.say(f"{n}. guide: {g['title']}  [KB ✅ guide]", hang=3)
            out.cmd(f"la guide {h['id']}", indent=5)
        elif h["kind"] == "error":
            e = next(x for x in kb.errors if x["id"] == h["id"])
            out.say(f"{n}. error: {e['title']}  [KB ✅]", hang=3)
            out.cmd(f'la why "{e["match"][0]}"', indent=5)
        elif h.get("help"):
            words = h["help"].replace(" --help", "")
            out.say(f"{n}. {words} — {h['desc']}  [help]", hang=3)
            out.cmd(f"la show {words}", indent=5)
        else:
            out.say(f"{n}. {h['id']} — {h['desc']}  [man]", hang=3)
            out.cmd(f"la show {h['name']}", indent=5)


def run(rel, kb, text):
    hits = search.search(kb, rel, text)
    out.say(f'la find "{text}" · Ubuntu {rel.release}')
    print()
    if not hits:
        out.say("Not found: nothing in the KB or the man page names matches those words.")
        out.say("Try other words (e.g. 'disk' instead of 'storage'), or: la ask \"<question>\"")
        return 1
    show_hits(kb, hits)
    return 0
