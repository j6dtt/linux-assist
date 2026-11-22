"""The curated knowledge base: command cards, guides and error entries.

  kb/cmd/<category>.json   a list of command cards
  kb/guides/<id>.json      one guide (read-only runbook)
  kb/errors.json           a list of error entries

load() reads them and returns (kb, problems). Each problem is a plain-English line.
"""
import json
import os
import re


def _read_json(path):
    with open(path) as f:
        return json.load(f)

from .danger import LEVELS
from .data import RELEASES

LEVEL_NAMES = ("basic", "intermediate", "advanced")
STAGES = ("check", "prepare", "change", "verify")
PH_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_-]*)>")


def _need(obj, keys, where, problems):
    for k in keys:
        if not obj.get(k):
            problems.append(f"{where}: '{k}' is missing or empty")


def _releases(obj, default, where, problems):
    rels = obj.get("releases", default)
    bad = [r for r in rels if r not in RELEASES]
    if bad:
        problems.append(f"{where}: unknown release {bad[0]}")
    return [r for r in rels if r in RELEASES]


def _cmd_item(obj, where, problems, default_rels, cmd_key="cmd"):
    """Checks one runnable item (example, step, check) and fills in its releases."""
    if not obj.get(cmd_key):
        problems.append(f"{where}: '{cmd_key}' is missing")
    if obj.get("danger") not in LEVELS:
        problems.append(f"{where}: danger must be one of {', '.join(LEVELS)}")
    obj["releases"] = _releases(obj, default_rels, where, problems)


def load(root):
    problems = []
    kb = {"cards": {}, "guides": {}, "errors": []}
    cmd_dir = os.path.join(root, "cmd")
    for fname in sorted(os.listdir(cmd_dir)):
        if not fname.endswith(".json"):
            continue
        category = fname[:-5]
        try:
            cards = _read_json(os.path.join(cmd_dir, fname))
        except ValueError as e:
            problems.append(f"kb/cmd/{fname} is not valid JSON: {e}")
            continue
        for n, card in enumerate(cards, 1):
            where = f"kb/cmd/{fname} card {n} ({card.get('name', '?')})"
            _need(card, ("name", "summary", "examples", "tasks"), where, problems)
            name = card.get("name", "")
            if name in kb["cards"]:
                problems.append(f"{where}: card {name} is also in {kb['cards'][name]['category']}")
            card["category"] = category
            rels = _releases(card, list(RELEASES), where, problems)
            card["releases"] = rels
            for k, ex in enumerate(card.get("examples", []), 1):
                w = f"{where} example {k}"
                if ex.get("level") not in LEVEL_NAMES:
                    problems.append(f"{w}: level must be one of {', '.join(LEVEL_NAMES)}")
                if not ex.get("does"):
                    problems.append(f"{w}: 'does' is missing")
                _cmd_item(ex, w, problems, rels)
            kb["cards"][name] = card
    gdir = os.path.join(root, "guides")
    for fname in sorted(os.listdir(gdir)):
        if not fname.endswith(".json"):
            continue
        where = f"kb/guides/{fname}"
        try:
            g = _read_json(os.path.join(gdir, fname))
        except ValueError as e:
            problems.append(f"{where} is not valid JSON: {e}")
            continue
        _need(g, ("id", "title", "tasks", "steps"), where, problems)
        if g.get("id") != fname[:-5]:
            problems.append(f"{where}: id must match the file name ({fname[:-5]})")
        rels = _releases(g, list(RELEASES), where, problems)
        g["releases"] = rels
        declared = g.get("placeholders", {})
        for ph, info in declared.items():
            _need(info, ("means", "find"), f"{where} placeholder <{ph}>", problems)
        for k, st in enumerate(g.get("steps", []), 1):
            w = f"{where} step {k}"
            if st.get("stage") not in STAGES:
                problems.append(f"{w}: stage must be one of {', '.join(STAGES)}")
            _need(st, ("say",), w, problems)
            _cmd_item(st, w, problems, rels)
            if st.get("danger") in ("modify", "destructive") and not st.get("rollback"):
                problems.append(f"{w}: a {st.get('danger')} step needs a 'rollback' note")
            for ph in PH_RE.findall(st.get("cmd", "") + " " + st.get("rollback_cmd", "")
                                    + " " + st.get("content", "")):
                if ph not in declared:
                    problems.append(f"{w}: <{ph}> is used but not listed in 'placeholders'")
        kb["guides"][g.get("id", fname)] = g
    epath = os.path.join(root, "errors.json")
    if os.path.exists(epath):
        try:
            errors = _read_json(epath)
        except ValueError as e:
            problems.append(f"kb/errors.json is not valid JSON: {e}")
            errors = []
        seen = set()
        for n, e in enumerate(errors, 1):
            where = f"kb/errors.json entry {n} ({e.get('id', '?')})"
            _need(e, ("id", "title", "match", "causes"), where, problems)
            if e.get("id") in seen:
                problems.append(f"{where}: id is used twice")
            seen.add(e.get("id"))
            for k, c in enumerate(e.get("causes", []), 1):
                w = f"{where} cause {k}"
                _need(c, ("cause",), w, problems)
                _cmd_item(c, w, problems, list(RELEASES))
            kb["errors"].append(e)
    return kb, problems


def runnable_items(kb):
    """(where, item dict, card command or None, extra man refs) for every command in the KB."""
    for name, card in kb["cards"].items():
        for k, ex in enumerate(card.get("examples", []), 1):
            yield f"card {name}, example {k}", ex, name, card.get("man", [])
    for gid, g in kb["guides"].items():
        for ph, info in g.get("placeholders", {}).items():
            yield (f"guide {gid}, how to find <{ph}>",
                   {"cmd": info.get("find", ""), "releases": g["releases"], "danger": "read-only"},
                   None, [])
        for k, st in enumerate(g.get("steps", []), 1):
            yield f"guide {gid}, step {k}", st, None, []
            if st.get("rollback_cmd"):
                yield (f"guide {gid}, step {k} rollback",
                       {"cmd": st["rollback_cmd"], "releases": st["releases"],
                        "danger": st.get("rollback_danger", "modify")}, None, [])
    for e in kb["errors"]:
        for k, c in enumerate(e.get("causes", []), 1):
            yield f"error {e.get('id')}, cause {k}", c, None, []
            if c.get("fix_cmd"):
                yield (f"error {e.get('id')}, cause {k} fix",
                       {"cmd": c["fix_cmd"], "releases": c["releases"],
                        "danger": c.get("fix_danger", "modify")}, None, [])
