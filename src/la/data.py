"""The reference data collected at build time, one folder per Ubuntu release.

  <LA_DATA>/data/<release>/meta.json, whatis.tsv, commands.tsv, man/<sec>/<name>.txt.gz
"""
import gzip
import json
import os
import re

DATA = os.environ.get("LA_DATA", "/opt/la")
RELEASES = ("22.04", "24.04")
# Where to look for a command's page when no section is given.
SECTION_ORDER = ("1", "8", "5")

# Shell builtins: no page of their own, documented in bash(1).
BUILTINS = {"cd", "echo", "export", "source", ".", "alias", "unalias", "type", "umask",
            "ulimit", "history", "set", "unset", "exit", "read", "jobs", "fg", "bg",
            "wait", "trap", "pushd", "popd", "dirs", "hash", "command", "exec", "eval",
            "true", "false", "test", "[", "printf", "shopt", "builtin", "disown", "help"}


def parse_ref(ref):
    """'ss(8)' -> ('ss', '8'); 'ss' -> ('ss', None)."""
    m = re.match(r"^(.+)\((\w+)\)$", ref)
    return (m.group(1), m.group(2)) if m else (ref, None)


class Release:
    def __init__(self, release, root=None):
        if release not in RELEASES:
            raise ValueError(f"unknown release {release}; this build has {', '.join(RELEASES)}")
        self.release = release
        self.dir = os.path.join(root or DATA, "data", release)
        with open(os.path.join(self.dir, "meta.json")) as f:
            self.meta = json.load(f)
        self.commands = {}
        with open(os.path.join(self.dir, "commands.tsv")) as f:
            for line in f:
                name, pkg, ver = (line.rstrip("\n").split("\t") + ["", ""])[:3]
                self.commands[name] = (pkg, ver)
        self._sections = sorted(os.listdir(os.path.join(self.dir, "man")))
        self._pages = {}
        # Pages made from --help text: page name -> the command, e.g. "kubectl get --help".
        self.help_pages = self.meta.get("help_pages", {})
        # Other names for a help page: kubectl-top-pods -> kubectl-top-pod.
        self.help_aliases = self.meta.get("help_aliases", {})

    def page_path(self, name, section=None):
        name = self.help_aliases.get(name, name)
        if section:
            secs = [section]
        else:
            secs = [s for want in SECTION_ORDER for s in self._sections if s.startswith(want)]
        for s in secs:
            p = os.path.join(self.dir, "man", s, name + ".txt.gz")
            if os.path.exists(p):
                return s, p
        return None, None

    def man(self, name, section=None):
        """The ManPage for name, or None if this release has no such page."""
        from .manpage import ManPage
        key = (name, section)
        if key not in self._pages:
            sec, path = self.page_path(name, section)
            page = None
            if path:
                real = self.help_aliases.get(name, name)
                with gzip.open(path, "rt", errors="replace") as f:
                    page = ManPage(real, sec, f.read(), self.help_pages.get(real, ""))
            self._pages[key] = page
        return self._pages[key]

    def source_of(self, page, command=""):
        """Where a page's text comes from: 'kubectl 1.34.13 (dl.k8s.io)' for a help page,
        else the command's package and version, e.g. 'iproute2 6.1.0-1ubuntu6'."""
        if page is not None and page.help_cmd:
            for tool, src in self.meta.get("help_sources", {}).items():
                if page.help_cmd.startswith(tool + " ") or page.help_cmd == tool:
                    return src
        pkg, ver = self.commands.get(command or (page.name if page else ""), ("", ""))
        return f"{pkg} {ver}".strip()

    def man_ref(self, ref):
        return self.man(*parse_ref(ref))

    def whatis(self):
        rows = []
        with open(os.path.join(self.dir, "whatis.tsv")) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3:
                    rows.append(tuple(parts))
        return rows
