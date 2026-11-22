"""Danger levels: read-only, modify, destructive.

KB entries state their level. This module finds commands that are destructive
whatever the entry says (the KB check stops the build if one is labelled
lower), and rates commands that have no label (AI answers).
"""
from . import cmdline

LEVELS = ("read-only", "modify", "destructive")

# Always destructive: they erase or overwrite data.
ALWAYS = {"mkfs", "mke2fs", "mkswap", "wipefs", "shred", "blkdiscard", "sgdisk",
          "pvremove", "vgremove", "lvremove", "mkdosfs"}
# Destructive with one of these flags or sub-words.
WITH = {
    "rm": {"-r", "-R", "--recursive", "-f", "--force", "-rf", "-fr", "-Rf"},
    "find": {"-delete"},
    "rsync": {"--delete", "--delete-after", "--delete-before", "--delete-during"},
    "iptables": {"-F", "--flush", "-X", "--delete-chain"},
    "nft": {"flush"},
    "ufw": {"reset"},
    "parted": {"rm", "mklabel", "mkpart"},
    "mdadm": {"--zero-superblock", "--create", "-C"},
    "cryptsetup": {"luksFormat", "erase"},
    "userdel": {"-r", "--remove"},
    "git": {"clean"},
    "truncate": {"-s", "--size"},
    "lvreduce": {"-L", "--size", "-l", "--extents"},
}
# Read-only commands and sub-words (used when rating an unlabelled command).
READ_ONLY = {"ls", "cat", "less", "head", "tail", "wc", "stat", "file", "du", "df",
             "find", "grep", "sort", "uniq", "cut", "awk", "tr", "diff", "tree", "pwd",
             "id", "who", "w", "last", "lastb", "ps", "top", "htop", "pgrep", "free",
             "uptime", "vmstat", "iostat", "mpstat", "lsof", "ss", "ping", "tracepath",
             "traceroute", "mtr", "dig", "host", "lsblk", "blkid", "findmnt", "pvs", "vgs",
             "lvs", "pvdisplay", "vgdisplay", "lvdisplay", "lscpu", "lspci", "lsusb",
             "lsmod", "uname", "hostname", "getfacl", "lsattr", "journalctl", "dmesg",
             "sha256sum", "md5sum", "realpath", "readlink", "date", "env", "printenv",
             "which", "whereis", "type", "column", "jq", "man", "apropos", "whatis",
             "namei", "getent", "groups", "nproc", "dpkg-query", "apt-cache", "aa-status"}
READ_ONLY_SUB = {
    "systemctl": {"status", "list-units", "list-unit-files", "list-timers", "is-active",
                  "is-enabled", "is-failed", "cat", "show", "list-dependencies"},
    "ip": {"show", "list", "get"},
    "apt": {"list", "show", "search", "policy"},
    "dpkg": {"-l", "-L", "-S", "-s", "--list", "--listfiles", "--search", "--status"},
    "ufw": {"status"},
    "nft": {"list"},
    "timedatectl": {"status", "show"},
    "hostnamectl": {"status"},
    "resolvectl": {"status", "query", "dns"},
    "netplan": {"get", "status"},
    "git": {"log", "status", "diff", "show"},
    "crontab": {"-l"},
    "tar": {"-t", "-tf", "-tvf", "-tzf", "-tzvf", "--list"},
    "smartctl": {"-a", "-H", "-i"},
    "mdadm": {"--detail", "-D", "--examine", "-E"},
}


# docker and kubectl: group words before the verb ("docker container rm", "kubectl rollout
# status"), flags whose value is the next word, and the read-only verbs.
GROUPS = {
    "docker": {"container", "image", "volume", "network", "system", "compose", "context",
               "builder", "node", "service", "stack", "plugin", "secret", "swarm", "trust"},
    "kubectl": {"config", "rollout", "auth", "certificate", "plugin"},
}
VALUE_FLAGS = {
    "docker": {"-H", "--host", "-c", "--context", "--config", "-l", "--log-level", "-f", "--file",
               "-p", "--project-name", "--env-file", "--profile", "--project-directory"},
    "kubectl": {"-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "-s",
                "--server", "--as", "--token"},
}
PAGED_READ_ONLY = {
    "docker": {"ps", "inspect", "logs", "images", "version", "info", "stats", "top", "port",
               "events", "diff", "history", "ls", "df", "config", "show"},
    "kubectl": {"get", "describe", "logs", "top", "explain", "version", "api-resources",
                "api-versions", "cluster-info", "events", "diff", "can-i", "whoami", "view",
                "get-contexts", "current-context", "get-clusters", "get-users", "status",
                "history"},
}


def verb(inv):
    """The docker / kubectl verb: 'docker container rm x' -> 'rm', 'kubectl -n a get pods' -> 'get'."""
    groups = GROUPS.get(inv.command, set())
    takes = VALUE_FLAGS.get(inv.command, set())
    skip = False
    for w in inv.words:
        if skip:
            skip = False
        elif w == "--":
            break
        elif w.startswith("-"):
            skip = w in takes
        elif w not in groups:
            return w
    return ""


def _paged_destructive(inv, words):
    v = verb(inv)
    if inv.command == "docker":
        name = " ".join(["docker"] + [a for a in inv.args[:2] if a in GROUPS["docker"]] + [v])
        if v in ("rmi", "prune") or (v == "rm" and "image" in inv.args[:2]):
            return f"{name} deletes for good (images can't be pulled again on an air-gapped server)"
        if v == "rm" and "volume" in inv.args[:2]:
            return f"{name} deletes the volume's data"
        hit = words & {"-v", "--volumes", "--rmi"}
        if v in ("down", "rm") and hit:
            return f"{name} {sorted(hit)[0]} deletes volumes or images too"
    if inv.command == "kubectl":
        if v == "delete":
            return "kubectl delete removes the objects (pods come back only if a controller recreates them)"
        if v == "drain" and words & {"--delete-emptydir-data", "--delete-local-data"}:
            return "kubectl drain --delete-emptydir-data deletes the pods' emptyDir data"
        if v == "replace" and "--force" in words:
            return "kubectl replace --force deletes and recreates the objects"
    return ""


def destructive(inv):
    """A reason if this invocation is destructive, else ''."""
    c = inv.command
    if c in GROUPS:
        return _paged_destructive(inv, set(inv.flags) | set(inv.args))
    if c in ALWAYS or c.startswith("mkfs."):
        return f"{c} erases data"
    if c == "dd" and any(a.startswith("of=") for a in inv.args):
        return "dd writes over its output (of=)"
    words = set(inv.flags) | set(inv.args)
    short = {"-" + ch for f in inv.flags if f.startswith("-") and not f.startswith("--") for ch in f[1:]}
    if c == "rsync" and (words | short) & {"-n", "--dry-run"}:
        return ""                  # a dry run changes nothing
    hit = WITH.get(c, set()) & words
    if hit:
        return f"{c} {sorted(hit)[0]} removes or overwrites data"
    if c == "rm" and any(f.startswith("-") and set(f[1:]) & {"r", "R", "f"} for f in inv.flags):
        return "rm -r / -f removes files without asking"
    return ""


def rate(line):
    """(level, reason) for an unlabelled command line. Unknown commands rate 'modify'."""
    invs = cmdline.parse(line)
    worst, reason = "read-only", ""
    for inv in invs:
        d = destructive(inv)
        if d:
            return "destructive", d
        if inv.command in ("sudo", "xargs", "watch", "nice", "timeout", "env", "time"):
            continue
        ro = inv.command in READ_ONLY
        sub = READ_ONLY_SUB.get(inv.command)
        if inv.command in PAGED_READ_ONLY:
            ro = verb(inv) in PAGED_READ_ONLY[inv.command]
        elif sub:
            ro = bool(set(inv.flags + inv.args[:1]) & sub)
        if not ro and worst == "read-only":
            worst, reason = "modify", f"{inv.command} may change the system"
    return worst, reason
