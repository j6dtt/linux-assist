"""la guide list | la guide <id> [NAME=value ...]: read-only step-by-step guides.

Values you give fill the <NAME> placeholders. Nothing is filled in otherwise: the tool
cannot see your server, so it tells you which command shows each value.
"""
import difflib
import re

from . import out

SAFE_VALUE = re.compile(r"^[A-Za-z0-9._/:@%+=,-]+$")
STAGE = {"check": "CHECK", "prepare": "PREPARE", "change": "CHANGE", "verify": "VERIFY"}


def list_all(kb):
    out.say("Guides (read-only: la shows the steps, you run them)")
    print()
    for gid in sorted(kb.guides):
        out.say(f"{gid}", indent=2)
        out.say(kb.guides[gid]["title"], indent=6)
    print()
    out.say("Open one: la guide <name>   Fill in values: la guide lvm-extend VG=ubuntu-vg")
    return 0


def parse_values(pairs):
    values, problems = {}, []
    for p in pairs:
        if "=" not in p:
            problems.append(f"'{p}' is not NAME=value")
            continue
        k, v = p.split("=", 1)
        k = k.strip().strip("<>").upper()
        if not SAFE_VALUE.match(v):
            problems.append(f"{k}: the value may only use letters, digits and . _ / : @ % + = , -")
            continue
        values[k] = v
    return values, problems


def fill(text, values):
    return re.sub(r"<([A-Za-z][A-Za-z0-9_-]*)>", lambda m: values.get(m.group(1), m.group(0)), text)


def run(rel, kb, gid, pairs):
    g = kb.guides.get(gid)
    if not g:
        out.say(f"No guide called '{gid}'.")
        close = difflib.get_close_matches(gid, sorted(kb.guides), n=3, cutoff=0.5)
        if close:
            out.say("Did you mean: " + ", ".join(close))
        out.say("All guides: la guide list")
        return 1
    values, problems = parse_values(pairs)
    unknown = [k for k in values if k not in g.get("placeholders", {})]
    for k in unknown:
        problems.append(f"{k} is not a value this guide uses")
    if problems:
        for p in problems:
            out.say(f"🔴 {p}")
        return 2
    ok_here = rel.release in g.get("releases", [])
    out.say(f"Guide: {g['title']}")
    out.say(f"Ubuntu {rel.release} · KB ✅ checked · you run each step yourself" if ok_here else
            f"Ubuntu {rel.release} · ⚠ this guide is not checked for {rel.release}")
    phs = g.get("placeholders", {})
    if phs:
        print()
        out.heading("FILL IN FIRST")
        for k, info in phs.items():
            if k in values:
                out.say(f"<{k}> = {values[k]}  (you gave)", indent=2)
            else:
                out.say(f"<{k}>  {info['means']}", indent=2, hang=6)
                out.say(f"find it: {fill(info['find'], values)}", indent=6)
    for n, st in enumerate(g["steps"], 1):
        print()
        where = f" (on {st['where']})" if st.get("where") else ""
        out.say(f"{n}. {STAGE.get(st['stage'], st['stage'].upper())}{where}  {st['say']}", hang=3)
        out.cmd(fill(st["cmd"], values), indent=3)
        flag = "" if rel.release in st.get("verified", []) else f"  (not checked on {rel.release})"
        out.say(out.label(st["danger"]) + flag, indent=3)
        if st.get("content"):
            out.say("content:", indent=3)
            for line in fill(st["content"], values).rstrip("\n").split("\n"):
                print("     " + line)
        if st.get("rollback") or st.get("rollback_cmd"):
            out.say(f"undo: {st.get('rollback', '')}".rstrip(), indent=3, hang=9)
            if st.get("rollback_cmd"):
                out.cmd(fill(st["rollback_cmd"], values), indent=9)
    if g.get("pitfalls"):
        print()
        out.heading("WATCH OUT")
        for p in g["pitfalls"]:
            out.say(f"- {p}", indent=2, hang=4)
    missing = [k for k in phs if k not in values]
    if missing:
        print()
        example = " ".join(f"{k}=..." for k in missing[:3])
        out.say(f"Fill the values into the steps: la guide {gid} {example}")
    return 0
