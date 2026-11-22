"""la explain "<command line>": each command and flag, from this release's man pages."""
import re

from . import cmdline, danger, out
from .manpage import first_sentence
from .show import did_you_mean
from .verify import PAGED, SUBCOMMANDS, check_line

STATUS = {
    "defined": "✅",
    "synopsis": "✅ (listed in SYNOPSIS)",
    "mentioned": "✅ (named in the page text)",
    "unchecked": "(shell builtin: see bash(1))",
    "missing": "🔴 not in the man page or --help text",
}


def watch_out(line):
    """Fixed rules for common mistakes. Each returns a plain-English warning."""
    notes = []
    if re.search(r"-(i?name|i?path|i?regex)\s+[^'\"\s]*[*?\[]", line):
        notes.append("Quote the pattern after -name (e.g. '*.log'), or the shell expands it first.")
    if re.search(r"\bsudo\b[^|]*[^2&]>\s*/", line) and "tee" not in line:
        notes.append("With 'sudo cmd > file' the shell, not sudo, opens the file, so it fails "
                     "for root-owned files. Use: cmd | sudo tee file")
    if re.search(r"\bchmod\b.*\b0?777\b", line):
        notes.append("777 lets every user change the files. It is almost never the right fix.")
    if re.search(r"\brm\b.*\s/(\s|$|\*)", line):
        notes.append("This removes from the root of the filesystem (/). Check the path.")
    if re.search(r"\brsync\b.*--delete", line) and "--dry-run" not in line and not re.search(r"\s-\w*n", line):
        notes.append("Run it with --dry-run first to see what --delete would remove.")
    if re.search(r"\b(apt|apt-get)\b.*\s-y\b", line):
        notes.append("-y answers yes to every question, including removals. Read the plan first.")
    if re.search(r"\bdocker\b.*\s(rmi|prune)\b|\bdocker\s+image\s+rm\b", line):
        notes.append("On an air-gapped server a deleted image can't be pulled again. "
                     "Keep a copy first: docker save -o <FILE>.tar <IMAGE>")
    if re.search(r"\bkubectl\b.*\s(delete|apply|drain|scale|replace|patch|edit|cordon|uncordon|"
                 r"create|label|annotate|set|restart|undo|taint)\b", line) and "--context" not in line:
        notes.append("This changes the cluster your current context points to, in its namespace "
                     "unless -n is given. Check it first: kubectl config current-context")
    return notes


def _show_flag(fr):
    status = STATUS.get(fr.status, fr.status)
    name = fr.flag if fr.flag == fr.word or fr.word.startswith("--") else f"{fr.flag} (in {fr.word})"
    desc = first_sentence(fr.desc) if fr.desc else ""
    out.say(f"{name}  {status} {fr.page}".rstrip(), indent=2)
    if desc:
        out.say(desc, indent=6)


def run(rel, kb, line):
    try:
        results = check_line(rel, line)
    except ValueError as e:
        out.say(f"Can't read that command line: {e}")
        return 2
    if not results:
        out.say("No command found in that line.")
        return 2
    out.say(f"la explain · Ubuntu {rel.release}")
    out.cmd(line, indent=0)
    problems = 0
    for r in results:
        inv = r.inv
        print()
        own = r.pages[0] if r.pages else None
        if r.builtin:
            out.say(f"{inv.command} — shell builtin, documented in bash(1). Flags not checked.")
        elif own:
            out.say(own.describe() or inv.command)
            src = rel.source_of(own, inv.command)
            pkg = f" · {src}" if src else ""
            out.say(f"[{own.cite_long}{pkg}]", indent=2)
        if inv.via and inv.via != "find":
            out.say(f"(run by {inv.via})", indent=2)
        elif inv.via == "find":
            out.say("(run by find -exec for each file found)", indent=2)
        if not r.installed and not r.builtin:
            if not own:
                out.say(inv.command)
            out.say(f"🔴 {inv.command} is not in the stock Ubuntu {rel.release} packages this build covers.", indent=2)
            close = did_you_mean(rel, kb, inv.command)
            if close:
                out.say("Did you mean: " + ", ".join(close), indent=2)
            problems += 1
            if not own:
                continue
        if inv.command in SUBCOMMANDS and inv.args and r.pages:
            sub = inv.args[0]
            hit = None
            for p in r.pages:
                if p.name == f"{inv.command}-{sub}":
                    hit = (f"{sub}", p.describe())
                    break
                hit = hit or p.term(sub)
            if hit:
                out.say(f"{sub}  ✅ subcommand", indent=2)
                out.say(first_sentence(hit[1]), indent=6)
            else:
                out.say(f"{sub}  🔴 subcommand not in the man page", indent=2)
                problems += 1
        if inv.command in PAGED:
            for p in r.problems:
                if "subcommand" in p:
                    out.say(f"🔴 {p}", indent=2)
                    problems += 1
        for fr in r.flags:
            _show_flag(fr)
            if fr.status == "missing":
                problems += 1
        if inv.command in PAGED:
            shown_args = r.args
        else:
            shown_args = inv.args[1:] if inv.command in SUBCOMMANDS else inv.args
        values = r.values if inv.command in PAGED else {}
        for n, a in enumerate(shown_args):
            if a in ("SUBST",):
                continue
            kind = "placeholder: fill in" if re.fullmatch(r"<[A-Za-z][\w-]*>", a) else "argument"
            if n in values:
                kind = f"value of {values[n]}" + (", fill in" if kind.startswith("placeholder") else "")
            out.say(f"{a}  ({kind})", indent=2)
        if r.inside:
            out.say(f"{' '.join(r.inside)}  (the command run inside the container: "
                    "not checked here)", indent=2)
    print()
    match = kb.match_example(line, rel.release)
    if match:
        name, ex = match
        out.say(f"Danger: {out.label(ex['danger'])} (from the KB)")
        out.say(f"Matches KB card {name} ✅: {ex['does']}")
    else:
        level, reason = danger.rate(line)
        out.say(f"Danger: {out.label(level)}" + (f" — {reason}" if reason else "") + " (rule-based)")
    notes = watch_out(line)
    if notes:
        print()
        out.heading("WATCH OUT")
        for n in notes:
            out.say(f"- {n}", indent=2, hang=4)
    if problems:
        print()
        out.say(f"🔴 {problems} part(s) could not be found in the Ubuntu {rel.release} man pages "
                "(or --help text). "
                "Check the spelling, or the command may not do what you expect.")
        return 1
    return 0


__all__ = ["run", "watch_out", "cmdline"]
