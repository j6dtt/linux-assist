"""Checks a command line against one release's man pages, without running anything.

For each command in the line: is it installed in that release (stock packages),
which package it comes from, and is each flag in its man page?
"""
import re

from . import cmdline
from .data import BUILTINS

# Pages that hold more of a command's flags than its own page.
# Commands whose first word is a subcommand that must be in the man page too.
SUBCOMMANDS = {"systemctl", "netplan", "apt", "apt-get", "apt-cache", "apt-mark", "ufw",
               "nft", "git", "resolvectl", "timedatectl", "hostnamectl", "systemd-analyze",
               "loginctl", "ip", "openssl"}

EXTRA_PAGES = {
    "iptables": ["iptables-extensions(8)"],
    "ip6tables": ["iptables-extensions(8)"],
    "kubectl": ["kubectl-options(1)"],
}

# One page per subcommand: docker-container-ls(1), "kubectl rollout status --help".
# The subcommand words pick the page; a first word that names none is a problem.
PAGED = {"docker", "kubectl"}
# After these, the first argument (image, container, service) is followed by the
# command run inside it: its words are not this command's flags.
RUNS_INSIDE = {"docker-run", "docker-exec", "docker-create", "docker-container-run",
               "docker-container-exec", "docker-container-create", "docker-compose-run",
               "docker-compose-exec"}
NOT_COVERED = {
    "kubectl": "kubectl plugins, such as kubectl vsphere, are not covered",
    "docker": "docker plugins other than compose, such as buildx, are not covered",
}


class FlagResult:
    def __init__(self, word, flag, status, page="", tag="", desc=""):
        self.word = word          # as written, e.g. '-tulpn'
        self.flag = flag          # one flag from it, e.g. '-t'
        self.status = status      # defined | synopsis | mentioned | missing | unchecked
        self.page = page          # e.g. 'ss(8)'
        self.tag = tag
        self.desc = desc

    @property
    def ok(self):
        return self.status in ("defined", "synopsis", "mentioned", "unchecked")


class InvResult:
    def __init__(self, inv):
        self.inv = inv
        self.subpath = []         # docker/kubectl: the subcommand words, e.g. ['rollout', 'status']
        self.args = list(inv.args)
        self.inside = []          # docker run/exec, kubectl exec --: the command run inside
        self.values = {}          # position in args -> the flag it is the value of
        self.installed = False
        self.builtin = False
        self.package = ""
        self.version = ""
        self.pages = []           # ManPage objects searched, own page first
        self.flags = []
        self.problems = []

    @property
    def ok(self):
        return not self.problems


def _pages_for(rel, inv, extra_refs):
    pages = []
    own = rel.man(inv.command)
    if own:
        pages.append(own)
    if inv.args:                                   # git log -> git-log(1)
        sub = rel.man(f"{inv.command}-{inv.args[0]}")
        if sub:
            pages.append(sub)
    for ref in EXTRA_PAGES.get(inv.command, []) + list(extra_refs or []):
        p = rel.man_ref(ref)
        if p and p not in pages:
            pages.append(p)
    return pages


def _find(pages, flag, allow_mentioned):
    for p in pages:
        hit = p.find(flag)
        if hit and (allow_mentioned or hit[0] != "mentioned"):
            return hit, p
    return None, None


def _has_subcommand(pages, cmd, sub):
    """The page set documents 'sub': its own page (git-log), a line starting with it
    (possibly in a list: "install, reinstall, remove, purge"), or the phrase 'cmd sub'."""
    if any(p.name == f"{cmd}-{sub}" for p in pages):
        return True
    start = re.compile(r"^\s+(?:[\w-]+,\s+)*" + re.escape(sub) + r"(?![\w-])", re.M)
    phrase = re.compile(r"(?<![\w-])" + re.escape(cmd) + r"\s+" + re.escape(sub) + r"(?![\w-])")
    return any(start.search(p.text) or phrase.search(p.text) for p in pages)


def _takes_value(pages, word):
    for p in pages:
        tv = p.takes_value(word.split("=", 1)[0])
        if tv is not None:
            return tv
    if not word.startswith("--") and len(word) > 2:      # -it: the last letter decides
        return _takes_value(pages, "-" + word[-1])
    return False


def _needs_sub(page):
    """A group page that only runs subcommands: kubectl rollout ("kubectl rollout
    SUBCOMMAND"), docker compose ("... COMMAND"), or one with no options of its own that
    lists its subcommands (kubectl top) or links to their pages (docker container)."""
    if "COMMANDS" in page.headings and any(
            re.search(r"\b(SUB)?COMMAND\b", line) for line in page.synopsis_text()):
        return True
    return not page.options and ("COMMANDS" in page.headings or f"{page.name}-" in page.text)


class PagedSplit:
    def __init__(self):
        self.path = []
        self.pages = []           # deepest subcommand page first
        self.flags = []
        self.flag_pages = []      # per flag: the pages for where it stands ("compose -f" vs "logs -f")
        self.args = []
        self.inside = []
        self.values = {}          # position in args -> the flag it is the value of
        self.problems = []


def split_paged(rel, inv, extra_pages):
    """docker / kubectl words in order: subcommand path, flags (and their values), arguments,
    and the command run inside a container."""
    sp = PagedSplit()
    own = rel.man(inv.command)
    chain = [own] if own else []
    sp.path = [inv.command]
    want_sub = True
    words = inv.words
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--":
            sp.inside = words[i + 1:]
            break
        if cmdline._is_flag(w):
            sp.flags.append(w)
            sp.flag_pages.append(chain + [p for p in extra_pages if p not in chain])
            if "=" not in w and _takes_value(chain + extra_pages, w) and i + 1 < len(words):
                sp.values[len(sp.args)] = w
                sp.args.append(words[i + 1])
                i += 2
                continue
            i += 1
            continue
        if want_sub:
            page = rel.man("-".join(sp.path + [w]))
            if page:
                sp.path.append(w)
                chain.insert(0, page)
                i += 1
                continue
            want_sub = False
            if chain and (len(sp.path) == 1 or _needs_sub(chain[0])):
                where = " ".join(sp.path)
                note = f" ({NOT_COVERED[inv.command]})" if len(sp.path) == 1 and inv.command in NOT_COVERED else ""
                sp.problems.append(f"'{w}' is not a {where} subcommand in {chain[0].cite}{note}")
        sp.args.append(w)
        if "-".join(sp.path) in RUNS_INSIDE:
            sp.inside = words[i + 1:]
            break
        i += 1
    sp.pages = chain + [p for p in extra_pages if p not in chain]
    return sp


def split_flag(word, style, pages):
    """The flags one word stands for: '-tulpn' -> -t -u -l -p -n; '--size=+20G' -> --size."""
    if word.startswith("--"):
        return [word.split("=", 1)[0]]
    if style == "single":
        return [word.split("=", 1)[0]]
    whole, _ = _find(pages, word, allow_mentioned=False)
    if len(word) == 2 or whole:
        return [word]
    flags = []
    for ch in word[1:]:
        if not ch.isalnum():
            break                                  # the rest is a value, e.g. -L+20G
        flags.append("-" + ch)
    return flags


def check_invocation(rel, inv, extra_refs=None):
    r = InvResult(inv)
    name = inv.command
    if name in BUILTINS:
        r.builtin = True
        r.installed = True
        bash = rel.man("bash")
        r.pages = [bash] if bash else []
        r.flags = [FlagResult(w, w, "unchecked", "bash(1)") for w in inv.flags]
        return r
    if name in rel.commands:
        r.installed = True
        r.package, r.version = rel.commands[name]
    else:
        r.problems.append(f"{name} is not in the stock Ubuntu {rel.release} packages this build covers")
    flag_words, flag_pages = inv.flags, None
    if name in PAGED and rel.man(name):
        extra = [p for p in (rel.man_ref(x) for x in EXTRA_PAGES.get(name, []) + list(extra_refs or [])) if p]
        sp = split_paged(rel, inv, extra)
        r.pages, r.subpath, r.args, r.inside = sp.pages, sp.path[1:], sp.args, sp.inside
        r.values = sp.values
        r.problems += sp.problems
        flag_words, flag_pages = sp.flags, sp.flag_pages
    else:
        r.pages = _pages_for(rel, inv, extra_refs)
    if not r.pages:
        r.problems.append(f"no man page for {name} in Ubuntu {rel.release}")
        r.flags = [FlagResult(w, w, "missing") for w in inv.flags]
        return r
    if name in SUBCOMMANDS and inv.args:
        sub = inv.args[0]
        if not _has_subcommand(r.pages, name, sub):
            r.problems.append(f"subcommand {sub} is not in {', '.join(p.cite for p in r.pages)} "
                              f"on {rel.release}")
    for k, word in enumerate(flag_words):
        pages = flag_pages[k] if flag_pages else r.pages
        for flag in split_flag(word, inv.style, pages):
            # long flags, and number flags such as -4, may be named anywhere in the page
            is_long = flag.startswith("--") or len(flag) > 2 or flag[1:].isdigit()
            hit, page = _find(pages, flag, allow_mentioned=is_long)
            if hit:
                r.flags.append(FlagResult(word, flag, hit[0], page.cite, hit[1], hit[2]))
            else:
                r.flags.append(FlagResult(word, flag, "missing"))
                where = ", ".join(p.cite for p in (pages[:2] if name in PAGED else pages))
                part = f" (from {word})" if flag != word else ""
                r.problems.append(f"flag {flag}{part} is not in {where} on {rel.release}")
    return r


def check_line(rel, line, extra_refs=None, card_command=None):
    """InvResult for every command in the line. extra_refs apply to card_command only."""
    results = []
    for inv in cmdline.parse(line):
        refs = extra_refs if (card_command and inv.command == card_command) else None
        results.append(check_invocation(rel, inv, refs))
    return results
