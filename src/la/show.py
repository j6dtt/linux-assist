"""la show <command>: the KB card, or else the man page summary, or else 'not found'."""
import difflib
import re

from . import out
from .data import RELEASES, Release, parse_ref

LEVEL_ORDER = ("basic", "intermediate", "advanced")


def _own_page(rel, card, name):
    if card:
        for ref in card.get("man", []):
            p = rel.man_ref(ref)
            if p:
                return p
    return rel.man(name)


def _pkg(rel, name):
    pkg, ver = rel.commands.get(name, ("", ""))
    return f"{pkg} {ver}".strip()


def did_you_mean(rel, kb, name):
    pool = set(kb.cards) | set(rel.commands) | {n for n, _, _ in rel.whatis()}
    return difflib.get_close_matches(name, sorted(pool), n=4, cutoff=0.75)


def run(rel, kb, name, full_man=False):
    card = kb.cards.get(name)
    page = _own_page(rel, card, name)
    if full_man:
        if not page:
            out.say(f"No man page for {name} in stock Ubuntu {rel.release} packages.")
            return 1
        if page.help_cmd:
            print(f"[Made from \"{page.help_cmd}\": {name.split('-')[0]} has no man pages]\n")
        print(page.text, end="")
        return 0
    if card:
        return _card(rel, kb, card, page)
    if page:
        return _man_only(rel, kb, name, page)
    return not_found(rel, kb, name)


def _card(rel, kb, card, page):
    name = card["name"]
    here, elsewhere = kb.examples_for(card, rel.release)
    out.say(f"{name} — {card['summary']}")
    bits = [f"Ubuntu {rel.release}"]
    if _pkg(rel, name):
        bits.append(_pkg(rel, name))
    if page:
        bits.append(page.cite_long)
    out.say(" · ".join(bits))
    ref = page.cite if page else name
    if name == "kubectl":
        ref = "kubectl's --help text, one page per subcommand"
    elif name == "docker":
        ref = "docker's man pages and docker compose's --help text"
    out.say(f"Source: KB ✅ every flag checked against {ref} on {rel.release}")
    for level in LEVEL_ORDER:
        exs = [e for e in here if e["level"] == level]
        if not exs:
            continue
        print()
        out.heading(level.upper())
        for ex in exs:
            out.cmd(ex["cmd"])
            out.say(f"{ex['does']}. {out.label(ex['danger'])}", indent=6)
    if elsewhere:
        print()
        other = [r for r in RELEASES if r != rel.release]
        out.say(f"Not on {rel.release} (checked on {', '.join(other)} only):")
        for ex in elsewhere:
            out.cmd(ex["cmd"], indent=4)
    if card.get("pitfalls"):
        print()
        out.heading("WATCH OUT")
        for p in card["pitfalls"]:
            out.say(f"- {p}", indent=2, hang=4)
    guides = kb.guides_using(name)
    if guides:
        print()
        out.say("Used in guides: " + ", ".join(f"la guide {g}" for g in guides))
    if card.get("see_also"):
        out.say("See also: " + ", ".join(card["see_also"]))
    out.say(f"Full man page: la show {name} --man")
    return 0


def _man_only(rel, kb, name, page, max_options=25):
    out.say(page.describe() or name)
    bits = [f"Ubuntu {rel.release}"]
    if rel.source_of(page, name):
        bits.append(rel.source_of(page, name))
    bits.append(page.cite_long)
    out.say(" · ".join(bits))
    if page.help_cmd:
        out.say(f"Source: {page.help_cmd} [help] (no man page; no KB card for this command)")
    else:
        out.say("Source: man page [man] (no KB card for this command)")
    syn = page.synopsis_text()
    if syn:
        print()
        out.heading("SYNOPSIS")
        out.say(join_synopsis(syn), indent=2, hang=4)
    opts = page.options_list()
    if opts:
        print()
        out.heading("OPTIONS" + (f" (first {max_options} of {len(opts)})" if len(opts) > max_options else ""))
        for tag, desc in opts[:max_options]:
            out.say(tag, indent=2)
            if desc:
                out.say(desc, indent=6)
    print()
    out.say(f"Full man page: la show {name} --man")
    return 0


def join_synopsis(lines):
    """Man page lines as one paragraph: words split at a line end ("--git‐" "file") joined."""
    text = ""
    for line in lines:
        if text.endswith(("‐", "-")) and not text.endswith((" -", " ‐")):
            text = text[:-1] + line
        else:
            text = (text + " " + line).strip()
    return re.sub(r"\s+", " ", text)


def not_found(rel, kb, name):
    out.say(f"Not found: no KB card and no man page for '{name}' in stock Ubuntu "
            f"{rel.release} packages.")
    for other in RELEASES:
        if other != rel.release:
            o = Release(other)
            if name in o.commands or o.man(name):
                out.say(f"It exists on Ubuntu {other}: la show {name} --release {other}")
    close = did_you_mean(rel, kb, name)
    if close:
        out.say("Did you mean: " + ", ".join(close))
    out.say(f'To search by task: la find "{name}"')
    return 1


__all__ = ["run", "not_found", "did_you_mean", "parse_ref"]
