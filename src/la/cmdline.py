"""Splits a command line into the commands it runs and the flags each one gets.

  "sudo du -xh --max-depth=1 /var | sort -h"
    -> sudo  flags ['']            (wrapper)
       du    flags ['-xh', '--max-depth=1']   args ['/var']
       sort  flags ['-h']

Handles pipes and lists (| && || ;), redirections (> 2>&1), VAR=value prefixes,
wrappers that run another command (sudo, xargs, watch, timeout, ...), find's
-exec ... ;, $( ... ), and <PLACEHOLDER> words. Nothing is run.
"""
import re
import shlex

OPERATORS = {"|", "||", "&&", ";", "&", "|&"}
REDIRECTS = {">", ">>", "<", "<<", ">&", "<&", "&>", "&>>", ">|", "<>"}

# wrapper -> (flags that take a value, positional words before the command)
WRAPPERS = {
    "sudo": ({"-u", "-g", "-C", "-D", "-p", "-r", "-t", "-U", "-T", "-h"}, 0),
    "xargs": ({"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a"}, 0),
    "watch": ({"-n", "-q"}, 0),
    "nice": ({"-n"}, 0),
    "ionice": ({"-c", "-n", "-p"}, 0),
    "nohup": (set(), 0),
    "timeout": ({"-s", "-k"}, 1),
    "env": ({"-u", "-C", "-S"}, 0),
    "time": (set(), 0),
    "stdbuf": ({"-i", "-o", "-e"}, 0),
}
# Commands whose flags are words with one dash (-xdev, -brief): never split into letters.
SINGLE_DASH = {"find", "ip", "bridge", "tc", "openssl"}
# Words that look like flags but are modes (chattr -i, chmod -x): arguments.
MODE_ARGS = {
    "chattr": re.compile(r"^[-+=][aAcCdDeFijmPsStTux]+$"),
    "chmod": re.compile(r"^[-+=][rwxXst]+$"),
}
EXEC_FLAGS = {"-exec", "-execdir", "-ok", "-okdir"}
PLACEHOLDER_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_-]*)>")
SUBST_RE = re.compile(r"\$\(([^()]*)\)")


class Invocation:
    def __init__(self, command, via=None):
        self.command = command
        self.flags = []     # raw flag words as written, e.g. '-xh', '--max-depth=1'
        self.args = []
        self.via = via      # the wrapper that runs it, e.g. 'sudo'
        self.notes = []
        self.words = []     # every word after the command, in order

    @property
    def style(self):
        return "single" if self.command in SINGLE_DASH else "gnu"

    def __repr__(self):
        return f"<{self.command} flags={self.flags} args={self.args}>"


def tokenize(line):
    """shlex words, with <PLACEHOLDER> kept as one word."""
    holders = {}

    def keep(m):
        key = f"LAPH{len(holders)}X"
        holders[key] = m.group(0)
        return key

    text = PLACEHOLDER_RE.sub(keep, line)
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    words = []
    for w in lex:
        for k, v in holders.items():
            w = w.replace(k, v)
        words.append(w)
    return words


def _is_flag(word, cmd=""):
    if len(word) < 2 or word[0] != "-" or word == "--":
        return False
    if cmd == "find" and re.match(r"^-\d", word):
        return False               # find -mtime -7: a value
    mode = MODE_ARGS.get(cmd)
    return not (mode and mode.match(word))


def parse(line):
    """All invocations in a command line, in order."""
    invs = []
    for inner in SUBST_RE.findall(line):
        invs.extend(parse(inner))
    line = SUBST_RE.sub("SUBST", line)
    words = tokenize(line)
    segments, cur = [], []
    skip_next = False
    for w in words:
        if skip_next:
            skip_next = False
            continue
        if w in OPERATORS:
            if cur:
                segments.append(cur)
            cur = []
        elif w in REDIRECTS or (w and w[0] in "<>" and set(w) <= set("<>&|")):
            if cur and cur[-1].isdigit():
                cur.pop()          # the 2 in 2>/dev/null
            skip_next = True       # the redirect target
        else:
            cur.append(w)
    if cur:
        segments.append(cur)
    for seg in segments:
        invs.extend(_segment(seg))
    return invs


def _segment(words, via=None):
    out = []
    i = 0
    while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
        i += 1                     # VAR=value prefix
    if i >= len(words):
        return out
    cmd = words[i]
    inv = Invocation(cmd, via)
    out.append(inv)
    rest = words[i + 1:]
    inv.words = list(rest)
    if cmd in WRAPPERS:
        takes_value, positional = WRAPPERS[cmd]
        j = 0
        while j < len(rest):
            w = rest[j]
            if w == "--":
                j += 1
                break
            if _is_flag(w):
                inv.flags.append(w)
                if w in takes_value:
                    j += 1
                j += 1
                continue
            if cmd == "env" and "=" in w:
                j += 1
                continue
            break
        inv.args = rest[j:j + positional]
        j += positional
        tail = rest[j:]
        if len(tail) == 1 and " " in tail[0]:
            for sub in parse(tail[0]):       # watch 'df -h'
                sub.via = cmd
                out.append(sub)
        elif tail:
            out.extend(_segment(tail, via=cmd))
        return out
    j = 0
    end_of_flags = False
    while j < len(rest):
        w = rest[j]
        if not end_of_flags and w == "--":
            end_of_flags = True
        elif cmd == "find" and w in EXEC_FLAGS:
            inv.flags.append(w)
            k = j + 1
            while k < len(rest) and rest[k] not in (";", "+"):
                k += 1
            sub = [x for x in rest[j + 1:k] if x != "{}"]
            if sub:
                out.extend(_segment(sub, via="find"))
            j = k
        elif not end_of_flags and _is_flag(w, cmd):
            inv.flags.append(w)
        else:
            inv.args.append(w)
        j += 1
    return out
