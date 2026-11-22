"""Masking for la ask: the model never sees host names, IPs, MACs, user names or chosen words.

Each one becomes a stand-in (hostA.example.com, 198.18.0.1, usera, ...) before the question
leaves the container, and the stand-ins in the model's answer become the real values again.
Stand-ins are valid in commands, so the model can still write a usable command.

Everything is found in one pass over the original text, so a masked word inside a host
name (acme in db01.acme.com) can't break the swap back.
"""
import ipaddress
import re

IPV4 = re.compile(r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w]|\.\d)")
IPV6 = re.compile(r"(?<![\w:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
MAC = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![\w:])")
USER_AT = re.compile(r"(?<![\w.+-])([A-Za-z0-9_][A-Za-z0-9_.+-]*)@([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)")
HOME = re.compile(r"(?<=/home/)([a-z_][a-z0-9_.-]*)")
# A DNS name: 3+ labels, or 2 labels ending in a usual domain ending. Never part of a path,
# and never a file name (the last label is not a file extension).
FQDN = re.compile(r"(?<![\w./@-])((?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{1,23})(?![\w/-])")
TLDS = {"com", "net", "org", "io", "local", "lan", "internal", "corp", "intra", "home", "gov",
        "mil", "edu", "us", "uk", "de", "fr", "ca", "au", "jp", "cloud", "dev", "app", "biz",
        "info", "int", "priv", "private", "site"}
FILE_EXT = {"conf", "cfg", "yaml", "yml", "json", "service", "socket", "timer", "target",
            "mount", "path", "slice", "scope", "log", "gz", "xz", "bz2", "zst", "tar", "tgz",
            "zip", "txt", "md", "py", "sh", "pem", "crt", "key", "csr", "list", "sources",
            "deb", "d", "ini", "rules", "network", "netdev", "link", "img", "iso", "so",
            "html", "xml", "sql", "bak", "old", "new", "tmp", "swp", "pid", "lock", "sock",
            "keyring", "gpg", "asc", "pub", "cnf", "toml", "env", "j2", "js", "css", "conf~"}
KEEP_HOSTS = {"localhost", "localhost.localdomain", "ubuntu.com", "archive.ubuntu.com",
              "security.ubuntu.com", "ports.ubuntu.com", "example.com", "example.org",
              "example.net", "github.com", "docker.com", "launchpad.net", "debian.org",
              "ntp.ubuntu.com", "keyserver.ubuntu.com", "esm.ubuntu.com", "snapcraft.io"}
KEEP_USERS = {"root", "ubuntu", "nobody", "www-data", "syslog", "systemd-network",
              "systemd-resolve", "messagebus", "sshd", "daemon", "user", "admin", "git"}
NAMES = {"ip": "IP address", "ip6": "IPv6 address", "mac": "MAC address", "host": "host name",
         "user": "user name", "word": "chosen word"}


def _letters(n):
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _keep_ip(ip):
    """Addresses with no meaning for one host: loopback, any, netmasks, the stand-in range."""
    return ip.startswith(("127.", "255.", "198.18.")) or ip in ("0.0.0.0",)


def _is_host(name):
    labels = name.lower().split(".")
    if name.lower() in KEEP_HOSTS or labels[-1] in FILE_EXT or labels[-1].isdigit():
        return False
    if re.fullmatch(r"v?\d+(\.\d+)+[a-z]*\d*", name.lower()):
        return False            # a version such as 1.2.3 or v2.4.1rc1
    if name.lower().endswith(".example.com"):
        return False
    return len(labels) >= 3 or labels[-1] in TLDS


class Masker:
    def __init__(self, words=(), on=True):
        self.on = on
        self.words = sorted({w.strip() for w in words if w and len(w.strip()) >= 3}, key=len, reverse=True)
        self.real_to_fake = {}
        self.fake_to_real = {}
        self.kinds = {}          # real -> kind
        self._seen_text = ""

    def _fake(self, kind, real):
        if real in self.real_to_fake:
            return self.real_to_fake[real]
        n = sum(1 for k in self.kinds.values() if k == kind)
        while True:
            if kind == "ip":
                fake = f"198.18.{n // 250}.{n % 250 + 1}"
            elif kind == "ip6":
                fake = f"2001:db8::{n + 1:x}"
            elif kind == "mac":
                fake = f"02:00:00:00:{n // 256:02x}:{n % 256:02x}"
            elif kind == "host":
                fake = f"host{_letters(n)}.example.com"
            elif kind == "user":
                fake = f"user{_letters(n).lower()}"
            else:
                fake = f"name{_letters(n)}"
            if fake.lower() not in self._seen_text.lower():
                break
            n += 1               # the text already holds this stand-in: take the next one
        self.kinds[real] = kind
        self.real_to_fake[real] = fake
        self.fake_to_real[fake] = real
        return fake

    def _spans(self, text):
        spans = []

        def add(start, end, kind):
            if end > start:
                spans.append((start, end, kind))
        for w in self.words:
            for m in re.finditer(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text, re.I):
                add(m.start(), m.end(), "word")
        for m in USER_AT.finditer(text):
            if m.group(1) not in KEEP_USERS:
                add(m.start(1), m.end(1), "user")
            host = m.group(2)
            if not IPV4.fullmatch(host) and host.lower() not in KEEP_HOSTS and not host.endswith(".example.com"):
                add(m.start(2), m.end(2), "host")
        for m in MAC.finditer(text):
            add(m.start(), m.end(), "mac")
        for m in IPV6.finditer(text):
            cand = m.group(0)
            if cand.count(":") < 2 or cand in ("::1", "::"):
                continue
            try:
                ipaddress.IPv6Address(cand)
            except ValueError:
                continue
            add(m.start(), m.end(), "ip6")
        for m in IPV4.finditer(text):
            if not _keep_ip(m.group(0)):
                add(m.start(), m.end(), "ip")
        for m in FQDN.finditer(text):
            if _is_host(m.group(1)):
                add(m.start(1), m.end(1), "host")
        for m in HOME.finditer(text):
            if m.group(1) not in KEEP_USERS:
                add(m.start(), m.end(), "user")
        # Longest first wins where spans overlap: db01.acme.com beats acme.
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        kept, end = [], -1
        for s in spans:
            if s[0] >= end:
                kept.append(s)
                end = s[1]
        return kept

    def hide(self, text):
        if not self.on or not text:
            return text
        self._seen_text += "\n" + text
        out, pos = [], 0
        for start, end, kind in self._spans(text):
            out.append(text[pos:start])
            out.append(self._fake(kind, text[start:end]))
            pos = end
        out.append(text[pos:])
        return "".join(out)

    def show(self, text):
        """Put the real values back in text from the model."""
        if not self.on or not text or not self.fake_to_real:
            return text
        lower = {f.lower(): r for f, r in self.fake_to_real.items()}
        v6 = [f for f, r in self.fake_to_real.items() if self.kinds[r] == "ip6"]
        rest = [f for f in self.fake_to_real if f not in v6]
        # IPv6 stand-ins end where no hex or colon follows; the others may be followed by
        # ':' (scp hostA.example.com:/tmp) or '.' (app.nameA.conf), but not by '.5'.
        for fakes, before, after in ((v6, r"(?<![\w:])", r"(?![\w:])"),
                                     (rest, r"(?<![\w-])", r"(?![\w-]|\.\d)")):
            if fakes:
                alt = "|".join(re.escape(f) for f in sorted(fakes, key=len, reverse=True))
                text = re.sub(before + "(" + alt + ")" + after,
                              lambda m: lower[m.group(1).lower()], text, flags=re.I)
        return text

    @property
    def counts(self):
        return self.kinds

    def summary(self):
        totals = {}
        for kind in self.kinds.values():
            totals[kind] = totals.get(kind, 0) + 1
        parts = []
        for k, n in totals.items():
            name = NAMES[k]
            parts.append(f"{n} {name}" + ("" if n == 1 else "es" if name.endswith("s") else "s"))
        return ", ".join(parts)
