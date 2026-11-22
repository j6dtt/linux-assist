"""Man-style pages from --help text, for the tools that ship no man pages:
kubectl and docker compose, every subcommand. Runs in the Ubuntu build stages
after collect.py.

Adds, under OUT (the release folder collect.py wrote):
  man/1/<name>.txt.gz  e.g. kubectl-get, kubectl-rollout-status, docker-compose-ps
  whatis.tsv           one line per page
  commands.tsv         kubectl's source and version
  meta.json            "help_pages": page name -> the command whose --help it holds;
                       "help_sources": tool -> version

Usage: python3 helpman.py OUT KUBECTL_VERSION COMPOSE_VERSION
"""
import gzip
import json
import os
import re
import subprocess
import sys
import textwrap

WIDTH = 80
ENV = dict(os.environ, HOME="/tmp", KUBECONFIG="/tmp/no-kubeconfig", COLUMNS="200",
           LANG="C.UTF-8", NO_COLOR="1", DOCKER_CLI_HINTS="false")
# A section header: "Options:", "Examples:", "Basic Commands (Beginner):", "Usage:  docker ..."
SECTION_RE = re.compile(r"^([A-Z][A-Za-z ()/-]*):(?:\s+(\S.*))?\s*$")
# kubectl:  "    -n, --namespace='':"  then tab-indented description lines
# (never two spaces inside: "--format string     Format ...:" is docker's style)
KUBE_OPT_RE = re.compile(r"^ {2,}(-{1,2}[A-Za-z0-9](?:[^ ]| (?! ))*?):\s*$")
# docker:   "  -a, --all       Show all ..."   "      --ansi string   Control ..."
DOCKER_OPT_RE = re.compile(
    r"^ {2,}((?:-[A-Za-z0-9], )?--[A-Za-z0-9][\w-]*(?: [A-Za-z]\w*)?)(?: {2,}(\S.*))?$")
CMD_RE = re.compile(r"^ {2,}([a-z][\w-]*)\*?(?: {2,}(\S.*))?$")
MAX_DEPTH = 4


def log(rel, text):
    print(f"[helpman {rel}] {text}", flush=True)


def run_help(argv):
    r = subprocess.run(argv, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=60)
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def _section(name):
    low = name.lower()
    if low == "usage":
        return "usage"
    if low in ("examples", "example"):
        return "examples"
    if low in ("options", "flags", "global flags", "global options"):
        return "options"
    if low == "aliases":
        return "aliases"
    if re.search(r"\bcommands\b", low):
        return "commands"
    return None


def parse(text):
    info = {"desc": [], "usage": [], "options": [], "commands": [], "examples": [], "aliases": []}
    section = "desc"
    lines = text.expandtabs(8).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        m = SECTION_RE.match(line) if line and not line[0].isspace() else None
        sec = _section(m.group(1)) if m else None
        if sec:
            section = sec
            if sec == "usage" and m.group(2):
                info["usage"].append(m.group(2))
            i += 1
            continue
        if section in ("usage", "aliases"):
            if not line.strip():
                section = "desc"           # docker: the description follows the usage
            elif section == "usage" and not line.lstrip().startswith("Use \""):
                info["usage"].append(line.strip())
            elif section == "aliases":     # "  pod, pods, po" or "  docker compose ps, docker ps"
                info["aliases"] += [a.split()[-1] for a in line.split(",") if a.strip()]
            i += 1
            continue
        if section == "examples":
            if line.startswith("Use \""):
                section = "desc"
            else:
                info["examples"].append(line)
            i += 1
            continue
        km = KUBE_OPT_RE.match(line)
        if km:
            tag, desc = km.group(1), []
            j = i + 1
            while j < len(lines) and lines[j].startswith(("\t", " " * 8)) \
                    and not KUBE_OPT_RE.match(lines[j].expandtabs(8)):
                desc.append(lines[j].strip())
                j += 1
            info["options"].append((tag, " ".join(d for d in desc if d)))
            i = j
            continue
        if section == "options":
            dm = DOCKER_OPT_RE.match(line)
            if dm:
                tag, desc = dm.group(1), [dm.group(2) or ""]
                col = len(line) - len((dm.group(2) or "").lstrip()) if dm.group(2) else 0
                j = i + 1
                while j < len(lines) and lines[j].strip() and not lines[j].lstrip().startswith("-") \
                        and len(lines[j]) - len(lines[j].lstrip()) >= max(col, 8):
                    desc.append(lines[j].strip())
                    j += 1
                info["options"].append((tag, " ".join(d for d in desc if d)))
                i = j
                continue
        if section == "commands":
            cm = CMD_RE.match(line)
            if cm:
                info["commands"].append((cm.group(1), cm.group(2) or ""))
                i += 1
                continue
            if not line.strip():
                i += 1
                continue
            section = "desc"
        if section == "desc" and not line.startswith("Use \""):
            info["desc"].append(line.strip())
        i += 1
    while info["desc"] and not info["desc"][0]:
        info["desc"].pop(0)
    while info["desc"] and not info["desc"][-1]:
        info["desc"].pop()
    while info["examples"] and not info["examples"][-1].strip():
        info["examples"].pop()
    while info["examples"] and not info["examples"][0].strip():
        info["examples"].pop(0)
    return info


def summary(info):
    for line in info["desc"]:
        if line:
            s = re.match(r"^(.+?[.!?])(\s|$)", line)
            return (s.group(1) if s else line).rstrip(".")
    return ""


def wrap(text, indent, hang=None):
    return textwrap.wrap(text, WIDTH, initial_indent=" " * indent,
                         subsequent_indent=" " * (indent if hang is None else hang),
                         break_long_words=False, break_on_hyphens=False) or [""]


def render(name, mid, info, source, tool, see_also):
    """mid: the command the text came from, e.g. 'kubectl get --help'."""
    title = f"{name.upper()}(1)"
    gap = max(2, WIDTH - 2 * len(title) - len(mid))
    out = [title + " " * (gap // 2) + mid + " " * (gap - gap // 2) + title, ""]
    out += ["NAME", f"       {name} - {summary(info) or mid}", ""]
    if info["usage"]:
        out.append("SYNOPSIS")
        out += wrap(" ".join(info["usage"]), 7, 11)
        out.append("")
    out.append("DESCRIPTION")
    para = []
    for line in info["desc"] + [""]:
        if line:
            para.append(line)
        elif para:
            out += wrap(" ".join(para), 7)
            out.append("")
            para = []
    out += wrap(f'This page was made at build time from "{mid}" ({source}). '
                f"{tool} has no man pages.", 7)
    out.append("")
    if info["options"]:
        out.append("OPTIONS")
        for tag, desc in info["options"]:
            out.append("       " + tag)
            if desc:
                out += wrap(desc, 14)
            out.append("")
    if info["commands"]:
        out.append("COMMANDS")
        for cname, cdesc in info["commands"]:
            out.append("       " + cname)
            if cdesc:
                out += wrap(cdesc, 14)
            out.append("")
    if info["examples"]:
        out.append("EXAMPLES")
        lead = min((len(e) - len(e.lstrip()) for e in info["examples"] if e.strip()), default=0)
        out += [("       " + e[lead:]).rstrip() for e in info["examples"]]
        out.append("")
    if see_also:
        out += ["SEE ALSO", "       " + see_also, ""]
    return "\n".join(out) + "\n"


def walk(base, words, depth, found, skip=()):
    """found[page name] = (command words, parsed help) for base + words and its subcommands."""
    text = run_help(base + words + ["--help"])
    if text is None:
        return
    info = parse(text)
    name = "-".join(base + words)
    found[name] = (base + words, info)
    if depth >= MAX_DEPTH:
        return
    for cname, _ in info["commands"]:
        if cname in ("help",) + tuple(skip) or "-".join([name, cname]) in found:
            continue
        walk(base, words + [cname], depth + 1, found)


def write_page(out, name, text):
    dest = os.path.join(out, "man", "1", name + ".txt.gz")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with gzip.open(dest, "wt", compresslevel=9) as f:
        f.write(text)


def main():
    out, kube_ver, compose_ver = sys.argv[1:4]
    with open(os.path.join(out, "meta.json")) as f:
        meta = json.load(f)
    rel = meta["release"]
    found = {}
    walk(["kubectl"], [], 0, found, skip=("options",))
    opts = run_help(["kubectl", "options"])
    if opts:
        found["kubectl-options"] = (["kubectl", "options"], parse(opts))
    n_kube = len(found)
    walk(["docker", "compose"], [], 0, found)
    n_compose = len(found) - n_kube
    if n_kube < 40 or n_compose < 15:
        log(rel, f"Only {n_kube} kubectl and {n_compose} docker compose pages were made: "
                 "their --help text was not read. Stopping.")
        sys.exit(1)
    help_pages, aliases, rows = {}, {}, []
    for name, (words, info) in sorted(found.items()):
        if words[0] == "kubectl":
            source, tool = f"kubectl {kube_ver}", "kubectl"
            see = "kubectl-options(1): the options every kubectl command takes" \
                if name != "kubectl-options" else ""
        else:
            source, tool = f"Docker Compose {compose_ver}", "docker compose"
            see = "docker-compose(1): the options every docker compose command takes" \
                if name != "docker-compose" else ""
        mid = " ".join(words) + " --help"
        if name == "kubectl-options":
            mid = "kubectl options"
            info["desc"] = ["The options every kubectl command takes."]
        write_page(out, name, render(name, mid, info, source, tool, see))
        help_pages[name] = mid
        rows.append((name, "1", summary(info) or mid))
        parent = name.rsplit("-", len(words[-1].split("-")))[0] if len(words) > 1 else ""
        for a in info["aliases"]:
            if parent and a != words[-1] and re.fullmatch(r"[a-z][\w-]*", a):
                aliases.setdefault(f"{parent}-{a}", name)
    with open(os.path.join(out, "whatis.tsv"), "a") as f:
        for row in rows:
            f.write("\t".join(row) + "\n")
    cpath = os.path.join(out, "commands.tsv")
    with open(cpath) as f:
        cmds = [line.rstrip("\n").split("\t") for line in f]
    with open(cpath, "w") as f:
        for row in cmds:
            if row[0] == "kubectl":
                row = ["kubectl", "kubectl", kube_ver]
            f.write("\t".join(row) + "\n")
    meta["help_pages"] = help_pages
    meta["help_aliases"] = {a: n for a, n in aliases.items() if a not in found}
    meta["help_sources"] = {"kubectl": f"kubectl {kube_ver} (dl.k8s.io)",
                            "docker compose": f"docker-compose-plugin {compose_ver}"}
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    log(rel, f"Made {n_kube} kubectl and {n_compose} docker compose pages from --help")


if __name__ == "__main__":
    main()
