"""<command> | la interpret <kind>: fixed rules over output you pipe in. No model.

Nothing is stored and nothing leaves the machine. Every "next" step points to a KB
guide or card, never to a command made up here. Exit code 1 when a rule raised a warning.
"""
import re

from . import out, why

KINDS = ("df", "free", "uptime", "vmstat", "iostat", "ss", "systemctl", "lsblk", "apt", "pods",
         "docker", "log")
POD_RESTARTS_WARN = 5
DF_WARN, DF_CRIT = 85, 95
MEM_WARN, MEM_CRIT = 10, 5          # available memory, % of total
UTIL_WARN, UTIL_CRIT = 80, 95       # iostat %util
WAIT_WARN = 20                      # vmstat wa, %
SKIP_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "efivarfs", "ramfs"}
SAFE = re.compile(r"^[A-Za-z0-9._/:@%+=,-]+$")


class Report:
    def __init__(self, release=""):
        self.release = release
        self.lines = []       # (symbol, text)
        self.next = []        # text lines (la ... commands or KB commands)
        self.warned = False

    def add(self, level, text):
        sym = {"crit": "⛔", "warn": "⚠", "ok": "✅", "info": "·"}[level]
        if level in ("crit", "warn"):
            self.warned = True
        self.lines.append((sym, text))

    def suggest(self, text):
        if text not in self.next:
            self.next.append(text)


def detect(text):
    head = "\n".join(text.splitlines()[:5])
    if re.search(r"^Filesystem\s.*\bI?Use%", head, re.M):
        return "df"
    if re.search(r"^\s+total\s+used\s+free", head, re.M) and "Mem:" in text:
        return "free"
    if re.search(r"^procs -+memory", head, re.M):
        return "vmstat"
    if re.search(r"^Device\s.*%util", text, re.M):
        return "iostat"
    if re.search(r"^(Netid\s+)?State\s+Recv-Q\s+Send-Q", head, re.M):
        return "ss"
    if re.search(r"UNIT\s+LOAD\s+ACTIVE\s+SUB", head):
        return "systemctl"
    if re.search(r"^NAME\s+MAJ:MIN", head, re.M):
        return "lsblk"
    if head.startswith("Listing...") or "[upgradable from:" in text:
        return "apt"
    if re.search(r"load averages?:", head):
        return "uptime"
    if re.search(r"^(NAMESPACE\s+)?NAME\s+READY\s+STATUS\s+RESTARTS", head, re.M):
        return "pods"
    if re.search(r"^CONTAINER ID\s+IMAGE\s+", head, re.M):
        return "docker"
    return "log"


def _kb_example(rep, kb, card, contains):
    c = kb.cards.get(card)
    for ex in (c or {}).get("examples", []):
        if contains in ex["cmd"] and rep.release in ex.get("verified", []):
            return f"{ex['cmd']}   [KB {card}]"
    return None


def _guide(kb, gid, **values):
    if gid not in kb.guides:
        return None
    known = kb.guides[gid].get("placeholders", {})
    vals = " ".join(f"{k}={v}" for k, v in values.items() if k in known and v and SAFE.match(v))
    return f"la guide {gid} {vals}".rstrip()


def _pct(s):
    m = re.match(r"^(\d+)%$", s)
    return int(m.group(1)) if m else None


def rule_df(text, kb, rep, opts):
    lines = [l for l in text.splitlines() if l.strip()]
    hdr = next((l for l in lines if l.startswith("Filesystem")), None)
    if not hdr:
        return False
    cols = hdr.split()
    key = "IUse%" if "IUse%" in cols else "Use%"
    idx = cols.index(key)
    type_idx = cols.index("Type") if "Type" in cols else None
    inodes = key == "IUse%"
    ok = 0
    for l in lines[lines.index(hdr) + 1:]:
        t = l.split()
        if len(t) <= idx:
            continue
        pct = _pct(t[idx])
        mount = " ".join(t[idx + 1:]) or t[0]
        fstype = t[type_idx] if type_idx is not None and len(t) > type_idx else ""
        if pct is None or t[0].startswith("/dev/loop") or fstype in SKIP_FS or t[0] in SKIP_FS \
                or mount.startswith(("/snap/", "/run", "/dev", "/sys", "/proc", "/boot/efi")):
            continue
        what = "inodes used" if inodes else "used"
        if pct >= DF_CRIT:
            rep.add("crit", f"{mount}  {pct}% {what}  (rule: {DF_CRIT}% or more is critical)")
        elif pct >= DF_WARN:
            rep.add("warn", f"{mount}  {pct}% {what}  (rule: {DF_WARN}% or more)")
        else:
            ok += 1
            continue
        rep.suggest(_guide(kb, "disk-full", MOUNT=mount))
        if inodes:
            rep.suggest(_kb_example(rep, kb, "du", "--inodes"))
    if ok:
        rep.add("ok", f"{ok} other filesystem(s) under {DF_WARN}%")
    return True


UNIT = {"": 1, "B": 1, "K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40, "P": 2**50}


def _size(s):
    m = re.match(r"^([\d.,]+)([BKMGTP]?)i?$", s)
    if not m:
        return None
    return float(m.group(1).replace(",", ".")) * UNIT[m.group(2)]


def rule_free(text, kb, rep, opts):
    hdr = next((l for l in text.splitlines() if re.match(r"^\s+total\s", l)), None)
    mem = next((l for l in text.splitlines() if l.startswith("Mem:")), None)
    swap = next((l for l in text.splitlines() if l.startswith("Swap:")), None)
    if not hdr or not mem:
        return False
    cols = hdr.split()
    vals = mem.split()[1:]
    if "available" not in cols or len(vals) < len(cols):
        rep.add("info", "No 'available' column: run free -h (procps) and pipe that in.")
        return True
    total, avail = _size(vals[cols.index("total")]), _size(vals[cols.index("available")])
    if not total:
        return False
    pct = round(100 * avail / total)
    if pct <= MEM_CRIT:
        rep.add("crit", f"memory available: {pct}% of total  (rule: {MEM_CRIT}% or less is critical)")
    elif pct <= MEM_WARN:
        rep.add("warn", f"memory available: {pct}% of total  (rule: {MEM_WARN}% or less)")
    else:
        rep.add("ok", f"memory available: {pct}% of total")
    if swap:
        sv = swap.split()[1:]
        stotal, sused = _size(sv[0]), _size(sv[1]) if len(sv) > 1 else None
        if not stotal:
            rep.add("info", "no swap configured")
        elif sused:
            spct = round(100 * sused / stotal)
            level = "warn" if pct <= MEM_WARN else "info"
            rep.add(level, f"swap used: {spct}%" + ("  (low memory and swap in use: the server is likely swapping)" if level == "warn" else ""))
    if rep.warned:
        rep.suggest(_kb_example(rep, kb, "ps", "--sort=-%mem"))
        rep.suggest(_kb_example(rep, kb, "vmstat", "vmstat 1 5"))
        rep.suggest('la why "Out of memory"')
    return True


def rule_uptime(text, kb, rep, opts):
    m = re.search(r"load averages?:\s*(\d+[.,]\d+),?\s+(\d+[.,]\d+),?\s+(\d+[.,]\d+)", text)
    if not m:
        return False
    loads = [float(x.replace(",", ".")) for x in m.groups()]
    rep.add("info", f"load average: {loads[0]} (1 min), {loads[1]} (5 min), {loads[2]} (15 min)")
    cpus = opts.get("cpus")
    if cpus:
        if loads[0] > cpus:
            rep.add("warn", f"1-minute load {loads[0]} is above the {cpus} CPUs: work is waiting  (rule: load > CPUs)")
            rep.suggest(_kb_example(rep, kb, "vmstat", "vmstat 1 5"))
            rep.suggest(_kb_example(rep, kb, "top", "top -b"))
        else:
            rep.add("ok", f"load is within the {cpus} CPUs")
    else:
        rep.add("info", "Compare with the CPU count (run nproc), or add --cpus N to apply the rule.")
    return True


def _table(lines, hdr_idx):
    cols = lines[hdr_idx].split()
    rows = []
    for l in lines[hdr_idx + 1:]:
        t = l.split()
        if len(t) == len(cols) and all(re.match(r"^-?[\d.,]+$", x) for x in t):
            rows.append(dict(zip(cols, (float(x.replace(",", ".")) for x in t))))
        elif rows:
            break
    return rows


def rule_vmstat(text, kb, rep, opts):
    lines = text.splitlines()
    hdr = next((i for i, l in enumerate(lines) if re.match(r"^\s*r\s+b\s", l)), None)
    if hdr is None:
        return False
    rows = _table(lines, hdr)
    if not rows:
        return False
    if len(rows) > 1:
        rows = rows[1:]       # the first line is the average since boot
        rep.add("info", f"{len(rows)} sample(s), first line (since boot) left out")
    si = max(r.get("si", 0) for r in rows)
    so = max(r.get("so", 0) for r in rows)
    wa = sum(r.get("wa", 0) for r in rows) / len(rows)
    idle = sum(r.get("id", 0) for r in rows) / len(rows)
    if si or so:
        rep.add("warn", f"swapping: si up to {si:g}, so up to {so:g}  (rule: above 0)")
        rep.suggest(_kb_example(rep, kb, "free", "free -h"))
        rep.suggest(_kb_example(rep, kb, "ps", "--sort=-%mem"))
    else:
        rep.add("ok", "no swapping (si and so are 0)")
    if wa >= WAIT_WARN:
        rep.add("warn", f"CPU waiting for disk: wa {wa:.0f}% on average  (rule: {WAIT_WARN}% or more)")
        rep.suggest(_kb_example(rep, kb, "iostat", "iostat"))
    else:
        rep.add("ok", f"disk wait wa {wa:.0f}%")
    rep.add("info", f"CPU idle {idle:.0f}% on average")
    cpus = opts.get("cpus")
    r = max(x.get("r", 0) for x in rows)
    if cpus and r > cpus:
        rep.add("warn", f"up to {r:g} processes waiting for CPU, with {cpus} CPUs  (rule: r > CPUs)")
    return True


def rule_iostat(text, kb, rep, opts):
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r"^Device\s.*%util", l)]
    if not starts:
        return False
    if len(starts) > 1:
        rep.add("info", f"{len(starts)} reports; the last one is used (the first is since boot)")
    i = starts[-1]
    cols = lines[i].split()
    u = cols.index("%util")
    any_dev = False
    for l in lines[i + 1:]:
        t = l.split()
        if not t:
            break
        if len(t) != len(cols) or t[0].startswith("loop"):
            continue
        any_dev = True
        util = float(t[u].replace(",", "."))
        if util >= UTIL_CRIT:
            rep.add("crit", f"{t[0]} {util:.0f}% busy  (rule: {UTIL_CRIT}% or more: the disk is the bottleneck)")
        elif util >= UTIL_WARN:
            rep.add("warn", f"{t[0]} {util:.0f}% busy  (rule: {UTIL_WARN}% or more)")
        else:
            rep.add("ok", f"{t[0]} {util:.0f}% busy")
    if rep.warned:
        rep.suggest(_kb_example(rep, kb, "pidstat", "pidstat -d 1"))
    return any_dev


def rule_ss(text, kb, rep, opts):
    lines = text.splitlines()
    if not lines:
        return False
    hdr = lines[0].split()
    has_netid = hdr and hdr[0] == "Netid"
    groups = {"all": [], "local": [], "one": []}
    for l in lines[1:]:
        t = l.split()
        if len(t) < 5:
            continue
        netid = t[0] if has_netid else "tcp"
        state = t[1] if has_netid else t[0]
        local = t[4] if has_netid else t[3]
        if state not in ("LISTEN", "UNCONN"):
            continue
        host, _, port = local.rpartition(":")
        host = host.split("%")[0]
        m = re.search(r'users:\(\("([^"]+)"', l)
        who = f" {m.group(1)}" if m else ""
        item = f"{port}/{netid}{who}"
        if host in ("0.0.0.0", "*", "[::]", "::"):
            groups["all"].append(item)
        elif host.startswith("127.") or host in ("[::1]", "::1"):
            groups["local"].append(item)
        else:
            groups["one"].append(f"{item} on {host}")
    if not any(groups.values()):
        return False
    def ports(items):
        return ", ".join(sorted(set(items), key=lambda x: (int(re.match(r"\d*", x).group() or 0), x)))
    if groups["all"]:
        rep.add("info", "open on all interfaces: " + ports(groups["all"]))
    if groups["one"]:
        rep.add("info", "on one address: " + ports(groups["one"]))
    if groups["local"]:
        rep.add("info", "this server only (loopback): " + ports(groups["local"]))
    if "users:((" not in text:
        rep.add("info", "No process names: run it with sudo and -p (sudo ss -tulpn).")
    if groups["all"]:
        rep.suggest(_kb_example(rep, kb, "ufw", "status verbose"))
    return True


def rule_systemctl(text, kb, rep, opts):
    lines = text.splitlines()
    hdr = next((i for i, l in enumerate(lines) if re.search(r"UNIT\s+LOAD\s+ACTIVE\s+SUB", l)), None)
    if hdr is None:
        return False
    failed, total = [], 0
    for l in lines[hdr + 1:]:
        t = l.replace("●", " ").replace("*", " ", 1).split()
        if len(t) < 4 or not re.match(r"^[\w@.:\\-]+\.\w+$", t[0]):
            continue
        total += 1
        if t[2] == "failed" or t[1] == "not-found":
            failed.append((t[0], t[1], t[2]))
    if failed:
        for unit, load, active in failed:
            rep.add("warn", f"{unit}: {active}" + (" (unit file not found)" if load == "not-found" else ""))
            rep.suggest(_guide(kb, "service-wont-start", UNIT=unit))
    else:
        rep.add("ok", f"no failed units among the {total} listed" if total else "no failed units listed")
    return True


def rule_lsblk(text, kb, rep, opts):
    lines = text.splitlines()
    if not lines or not lines[0].startswith("NAME"):
        return False
    cols = lines[0].split()
    if "TYPE" not in cols:
        return False
    ti = cols.index("TYPE")
    rows = lines[1:]
    found = 0
    for i, l in enumerate(rows):
        t = l.split()
        if len(t) <= ti or t[ti] != "disk" or t[0].startswith("loop"):
            continue
        has_child = i + 1 < len(rows) and re.match(r"^\s*[├└│|`]", rows[i + 1])
        mounted = len(t) > ti + 1
        if not has_child and not mounted:
            found += 1
            size = t[cols.index("SIZE")] if "SIZE" in cols else ""
            rep.add("info", f"{t[0]} ({size}): a disk with no partitions and no mount point (new or unused?)")
            rep.suggest(_guide(kb, "new-disk-mount", DISK=t[0]))
            rep.suggest(_guide(kb, "lvm-extend", NEW_DISK=t[0]))
    if not found:
        rep.add("ok", "every disk has partitions or is in use")
    return True


def rule_apt(text, kb, rep, opts):
    ups = [l for l in text.splitlines() if "[upgradable from:" in l]
    if not ups and "Listing..." not in text:
        return False
    sec = [l for l in ups if "-security" in l.split(" ")[0]]
    if not ups:
        rep.add("ok", "no upgradeable packages")
        return True
    rep.add("warn" if sec else "info", f"{len(ups)} upgradeable package(s), {len(sec)} from a -security source")
    for l in sec[:10]:
        rep.add("info", "security: " + l.split("/")[0])
    rep.suggest(_kb_example(rep, kb, "apt", "apt upgrade"))
    return True


def rule_log(text, kb, rep, opts):
    counts = {}
    for line in text.splitlines():
        for e, m, l in why.match(kb, line):
            c = counts.setdefault(e["id"], [e, m, l, 0])
            c[3] += 1
    if not counts:
        rep.add("ok", "no known error types found in the input")
        return True
    for e, m, l, n in sorted(counts.values(), key=lambda c: -c[3])[:5]:
        rep.add("warn", f"{e['title']}: {n} line(s)")
        rep.lines.append(("", f"    e.g. {l[:150]}"))
        rep.suggest(f'la why "{m}"')
    return True


def _columns(header, line):
    """Cut a table row at the header's column starts: values may hold spaces
    ("12 (2m ago)", "Up 3 days (unhealthy)")."""
    starts = [m.start() for m in re.finditer(r"(?<!\S)\S+(?: \S+)*", header)]
    names = [header[a:b].strip() for a, b in zip(starts, starts[1:] + [None])]
    vals = [line[a:b].strip() if a < len(line) else "" for a, b in zip(starts, starts[1:] + [None])]
    return dict(zip(names, vals))


def rule_pods(text, kb, rep, opts):
    lines = text.splitlines()
    hdr = next((i for i, l in enumerate(lines)
                if re.match(r"^(NAMESPACE\s+)?NAME\s+READY\s+STATUS\s+RESTARTS", l)), None)
    if hdr is None:
        return False
    total, issues = 0, 0
    for l in lines[hdr + 1:]:
        if not l.strip():
            continue
        c = _columns(lines[hdr], l)
        name, status, ready = c.get("NAME", ""), c.get("STATUS", ""), c.get("READY", "")
        if not name or not status:
            continue
        total += 1
        ns = c.get("NAMESPACE", "")
        who = f"{ns}/{name}" if ns else name
        restarts = int(re.match(r"\d+", c.get("RESTARTS", "0") or "0").group(0)) \
            if re.match(r"\d+", c.get("RESTARTS", "")) else 0
        m = re.match(r"^(\d+)/(\d+)$", ready)
        problem = ""
        if status not in ("Running", "Completed", "Succeeded"):
            problem = status
        elif status == "Running" and m and int(m.group(1)) < int(m.group(2)):
            problem = f"running but not ready ({ready})"
        elif restarts >= POD_RESTARTS_WARN:
            problem = f"restarted {restarts} times"
        if not problem:
            continue
        issues += 1
        if issues <= 15:
            rep.add("warn", f"{who}: {problem}" + (f", {restarts} restart" + ("s" if restarts != 1 else "")
                                                   if restarts and "restarted" not in problem else ""))
        rep.suggest(_guide(kb, "pod-not-running", NAMESPACE=ns, POD=name) if issues <= 3 else None)
        if any(status in mt for e in kb.errors for mt in e["match"]) and status:
            rep.suggest(f'la why "{status}"')
    if not total:
        return False
    if issues > 15:
        rep.add("info", f"... and {issues - 15} more")
    if not issues:
        rep.add("ok", f"{total} pod(s): all Running and ready (or finished), none restarting often")
    else:
        rep.add("info", f"{issues} of {total} pod(s) need a look")
    return True


def rule_docker(text, kb, rep, opts):
    lines = text.splitlines()
    hdr = next((i for i, l in enumerate(lines) if re.match(r"^CONTAINER ID\s+IMAGE\s+", l)), None)
    if hdr is None:
        return False
    total, issues = 0, 0
    for l in lines[hdr + 1:]:
        if not l.strip():
            continue
        c = _columns(lines[hdr], l)
        name, status = c.get("NAMES", ""), c.get("STATUS", "")
        if not status:
            continue
        total += 1
        level, what = None, ""
        code = re.match(r"^Exited \((\d+)\)", status)
        if status.startswith("Restarting"):
            level, what = "warn", "keeps restarting"
        elif "(unhealthy)" in status:
            level, what = "warn", "unhealthy (its health check fails)"
        elif status.startswith("Dead"):
            level, what = "warn", "dead (Docker could not stop or remove it)"
        elif code:
            n = int(code.group(1))
            if n == 0:
                level, what = "info", "exited 0 (finished normally)"
            elif n == 143:
                level, what = "info", "exited 143 (stopped by a signal, e.g. docker stop)"
            elif n == 137:
                level, what = "warn", "exited 137 (killed: out of memory, or docker stop timed out)"
            else:
                level, what = "warn", f"exited {n} (the app failed: see its log)"
        elif status.startswith("Created"):
            level, what = "info", "created but never started"
        if not level:
            continue
        if level == "warn":
            issues += 1
            rep.suggest(_guide(kb, "container-keeps-restarting", CONTAINER=name) if issues <= 3 else None)
        rep.add(level, f"{name or c.get('CONTAINER ID', '?')}: {what}")
    if not total:
        return False
    if not issues:
        rep.add("ok", f"{total} container(s): none restarting, unhealthy or failed")
    return True


RULES = {"df": rule_df, "free": rule_free, "uptime": rule_uptime, "vmstat": rule_vmstat,
         "iostat": rule_iostat, "ss": rule_ss, "systemctl": rule_systemctl,
         "lsblk": rule_lsblk, "apt": rule_apt, "pods": rule_pods, "docker": rule_docker,
         "log": rule_log}
ALIASES = {"journalctl": "log", "dmesg": "log", "syslog": "log", "kubectl": "pods"}


def run(rel, kb, kind, text, opts):
    if not text.strip():
        out.say("Nothing to read. Pipe a command's output in, e.g.:")
        out.cmd("df -h | la interpret df")
        out.say("Kinds: " + ", ".join(KINDS) + " (journalctl and dmesg output: log)")
        return 2
    kind = ALIASES.get(kind, kind) if kind else detect(text)
    if kind not in RULES:
        out.say(f"No rules for '{kind}'. Kinds: " + ", ".join(KINDS))
        return 2
    rep = Report(rel.release)
    out.say(f"la interpret {kind} · fixed rules, no model, nothing stored")
    try:
        understood = RULES[kind](text, kb, rep, opts)
    except (ValueError, IndexError, KeyError, ZeroDivisionError):
        understood = False
    if not understood:
        out.say(f"This does not look like {kind} output, so no rules were applied.")
        return 2
    print()
    for sym, line in rep.lines:
        out.say(f"{sym} {line}".rstrip() if sym else line, hang=4)
    nxt = [n for n in rep.next if n]
    if nxt:
        print()
        out.heading("NEXT")
        for n in nxt:
            out.cmd(n)
    return 1 if rep.warned else 0
