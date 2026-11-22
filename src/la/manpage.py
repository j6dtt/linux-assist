"""Reads a rendered man page (plain text) and answers: is this flag in it, and what does it say?

A flag counts as found when the page:
  - defines it: an option line such as "       -t, --tcp" (the description follows), or
  - lists it in the SYNOPSIS, or
  - (long flags only, e.g. --upgradeable) names it anywhere in the text.
Short flags such as -t must be defined or in the SYNOPSIS: a stray "-t" in prose is not enough.
"""
import re

FLAG_RE = re.compile(r"(?:^|(?<=[\s,|\[({/]))([-+]{1,2}[A-Za-z0-9?@][A-Za-z0-9_?@.+-]*)")
# An option line starts with a flag followed by the end, a space, ',', ';', '=', '[' or '|'.
TAG_START_RE = re.compile(r"^[-+]{1,2}[A-Za-z0-9?@][A-Za-z0-9_?@.+-]*(?=$|[\s,;=\[|])")
NEGATE_RE = re.compile(r"^\[?!\]?\s+")
# Words that belong to an option's tag: -x, --long, FILE, [ARG], x=y, a|b, "or".
ARG_WORD_RE = re.compile(r"^([-+\[<{|]|[A-Z0-9_]+[,.]?$|\S*[=\]|]|or$)")
HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,/&()-]*$")


def _clean(flag):
    flag = flag.rstrip(".,;:)")
    return flag.rstrip("-") if len(flag) > 2 else flag


class ManPage:
    def __init__(self, name, section, text, help_cmd=""):
        self.name = name
        self.section = section
        self.text = text
        # Made at build time from --help text (kubectl, docker compose): the command
        # it came from, e.g. "kubectl get --help". Empty for a real man page.
        self.help_cmd = help_cmd
        self.lines = text.splitlines()
        self.options = {}      # flag -> (tag line, description)
        self.synopsis = set()
        self.headings = {}     # heading -> first line index
        self._rank = {}
        self._parse()

    @property
    def ref(self):
        """A stable id, e.g. 'ss(8)' or 'kubectl-get(1)'."""
        return f"{self.name}({self.section})"

    @property
    def cite(self):
        """What to show a person: 'ss(8)', or 'kubectl get --help' for a help page."""
        return self.help_cmd or self.ref

    @property
    def cite_long(self):
        """'man ss(8)', or 'kubectl get --help'."""
        return self.help_cmd or f"man {self.ref}"

    def takes_value(self, flag):
        """Does this option take a value? From its option line:
        '--namespace=\'\'', '--attach=[]', '--file stringArray': yes;
        '--detach[=false]', '--all-namespaces=false', '--rm true|false', '--all': no.
        None when the page doesn't define the flag."""
        hit = self.options.get(flag)
        if not hit:
            return None
        spec = ""
        for m in re.finditer(r"([-+]{1,2}[A-Za-z0-9?@][A-Za-z0-9_?@.+-]*)([^,]*)", hit[0]):
            spec = spec or m.group(2).strip()
        if not spec or spec.startswith("[="):
            return False
        if re.fullmatch(r"=?\s*(true|false)(\|(true|false))?", spec):   # "--rm true|false" too
            return False
        return spec.startswith("=") or bool(re.match(r"[A-Za-z<]", spec))

    def _parse(self):
        lines = self.lines
        current = ""
        for i, line in enumerate(lines):
            if line and not line[0].isspace() and HEADING_RE.match(line.strip()):
                current = line.strip()
                self.headings.setdefault(current, i)
                continue
            stripped = line.lstrip()
            if not stripped:
                continue
            indent = len(line) - len(stripped)
            if current == "SYNOPSIS":
                for f in FLAG_RE.findall(line):
                    self.synopsis.add(_clean(f))
                # "[-abc]" lists the short flags -a, -b and -c
                for group in re.findall(r"\[-([A-Za-z0-9]{2,})\]", line):
                    for ch in group:
                        self.synopsis.add("-" + ch)
                continue
            # "[!] --dport port" (iptables): the "[!]" says the test can be negated.
            if not TAG_START_RE.match(NEGATE_RE.sub("", stripped)):
                continue           # prose that happens to start with a dash
            prev = lines[i - 1] if i else ""
            prev_s = prev.lstrip()
            if prev_s and len(prev) - len(prev_s) == indent \
                    and not NEGATE_RE.sub("", prev_s).startswith(("-", "+")):
                continue           # the next line of a paragraph, e.g. "-name and -regex) are ..."
            # The description column is the indent of the next text line. A tag
            # short enough to share its line ends where that column starts.
            desc_col = 0
            for nxt in lines[i + 1:i + 4]:
                if nxt.strip():
                    desc_col = len(nxt) - len(nxt.lstrip())
                    break
            if desc_col > indent and len(line) > desc_col and line[desc_col - 1] == " " \
                    and line[desc_col] != " " and len(line[desc_col:].split()) >= 3:
                tag, inline = line[indent:desc_col].strip(), line[desc_col:].strip()
            else:
                parts = re.split(r"\s{2,}", stripped, maxsplit=1)
                tag = parts[0]
                inline = parts[1] if len(parts) > 1 else ""
                words = tag.split(" ")
                if not inline and len(words) >= 4 and desc_col <= indent:
                    # "--zstd Filter the archive through zstd(1)." on one line:
                    # the tag is the flag and its argument words, the rest describes it.
                    n = 1
                    while n < len(words) and ARG_WORD_RE.match(words[n]):
                        n += 1
                    tag, inline = " ".join(words[:n]), " ".join(words[n:])
            flags = [_clean(f) for f in FLAG_RE.findall(tag)]
            if not flags:
                continue
            desc = [inline] if inline else []
            for nxt in lines[i + 1:]:
                s = nxt.lstrip()
                if not s:
                    if desc:
                        break
                    continue
                if len(nxt) - len(s) <= indent:
                    break
                desc.append(s)
            description = join_lines(desc)
            # Keep the best line per flag: one where it comes first ("-size n") beats a
            # list that names it ("-gid, -inum, -size"); one with a description beats none.
            for pos, f in enumerate(flags):
                rank = (pos == 0, bool(description))
                if f not in self._rank or rank > self._rank[f]:
                    self.options[f] = (tag, description)
                    self._rank[f] = rank

    def describe(self):
        """The one-line NAME description, e.g. 'ss - another utility to investigate sockets'."""
        start = self.headings.get("NAME")
        if start is None:
            return ""
        out = []
        for line in self.lines[start + 1:]:
            if line and not line[0].isspace():
                break
            if line.strip():
                out.append(line.strip())
        return " ".join(out)

    def mentioned(self, flag):
        return re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", self.text) is not None

    def find(self, flag):
        """('defined', tag, description) | ('synopsis', '', '') | ('mentioned', '', '') | None."""
        if flag in self.options:
            tag, desc = self.options[flag]
            return ("defined", tag, desc)
        if flag in self.synopsis:
            return ("synopsis", "", "")
        is_long = flag.startswith("--") or len(flag) > 2
        if is_long and self.mentioned(flag):
            return ("mentioned", "", "")
        return None

    def term(self, word):
        """(line, description) for a line that starts with word, e.g. a subcommand
        ("restart PATTERN...") or a word in a list ("install, reinstall, remove"). Or None."""
        pat = re.compile(r"^(\s+)((?:[\w-]+,\s+)*" + re.escape(word) + r")(?![\w-])(.*)$")
        for i, line in enumerate(self.lines):
            m = pat.match(line)
            if not m:
                continue
            indent = len(m.group(1))
            desc = []
            rest = m.group(3).strip()
            for nxt in self.lines[i + 1:]:
                s = nxt.lstrip()
                if not s:
                    if desc:
                        break
                    continue
                if len(nxt) - len(s) <= indent:
                    break
                desc.append(s)
            if desc or rest:
                return line.strip(), join_lines(desc) if desc else rest
        return None

    def options_list(self):
        """(tag, first sentence) for each option line, in page order, each tag once."""
        seen, out = set(), []
        for flag, (tag, desc) in self.options.items():
            if tag in seen:
                continue
            seen.add(tag)
            out.append((tag, first_sentence(desc)))
        return out

    def synopsis_text(self, max_lines=6):
        start = self.headings.get("SYNOPSIS")
        if start is None:
            return []
        out = []
        for line in self.lines[start + 1:]:
            if line and not line[0].isspace():
                break
            if line.strip():
                out.append(line.strip())
            if len(out) >= max_lines:
                break
        return out


def join_lines(lines):
    """Lines of a paragraph as one string; a word split at a line end ("re‐" "moved") is
    joined again."""
    text = ""
    for line in lines:
        line = line.strip()
        if text.endswith("\u2010"):
            text = text[:-1] + line
        else:
            text = (text + " " + line) if text else line
    return re.sub(r"[ \t]+", " ", text).strip()


def first_sentence(text, limit=160):
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"^(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"
