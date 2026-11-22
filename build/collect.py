"""Collects one Ubuntu release's reference data. Runs in its build stage.

Writes to OUT:
  man/<section>/<name>.txt.gz  every man page in sections 1, 5 and 8, as plain text
  whatis.tsv                   name, section, one-line description
  commands.tsv                 command name, package, package version
  meta.json                    release, codename, build date, counts, skipped packages

Usage: python3 collect.py RELEASE OUT SKIPPED_FILE
"""
import concurrent.futures
import datetime
import gzip
import json
import os
import re
import subprocess
import sys

SECTIONS = ("1", "5", "8")
MAN_ROOT = "/usr/share/man"
BIN_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
ENV = dict(os.environ, MANWIDTH="80", LANG="C.UTF-8", MAN_KEEP_FORMATTING="")


def log(rel, text):
    print(f"[collect {rel}] {text}", flush=True)


def page_files():
    """(section, name, path relative to MAN_ROOT) for each page in the wanted sections."""
    for sec in SECTIONS:
        d = os.path.join(MAN_ROOT, "man" + sec)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            base = f[:-3] if f.endswith(".gz") else f
            name, _, suffix = base.rpartition(".")
            if name and suffix.startswith(sec):
                yield suffix, name, os.path.join("man" + sec, f)


def render(item, out):
    sec, name, rel_path = item
    # -l reads the file itself; from MAN_ROOT, ".so" redirect pages resolve too.
    r = subprocess.run(["man", "-P", "cat", "-l", rel_path], cwd=MAN_ROOT, env=ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    text = subprocess.run(["col", "-bx"], input=r.stdout, stdout=subprocess.PIPE).stdout
    dest = os.path.join(out, "man", sec, name + ".txt.gz")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with gzip.open(dest, "wb", compresslevel=9) as f:
        f.write(text)
    return sec, name


def whatis(out):
    """One line per page: 'name (sec) - description'. Only the wanted sections."""
    r = subprocess.run(["man", "-k", "-s", ",".join(SECTIONS), "."], env=ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    rows = []
    for line in r.stdout.splitlines():
        m = re.match(r"^(\S+) \((\S+)\)\s+- (.*)$", line)
        if m:
            rows.append(m.groups())
    with open(os.path.join(out, "whatis.tsv"), "w") as f:
        for name, sec, desc in sorted(set(rows)):
            f.write(f"{name}\t{sec}\t{desc}\n")
    return len(rows)


def owners(paths):
    """path -> package, from dpkg's database. Paths dpkg doesn't know are left out."""
    found = {}
    r = subprocess.run(["dpkg", "-S"] + paths, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("diversion "):
            continue
        pkg, _, path = line.partition(": ")
        found[path.strip()] = pkg.split(",")[0].split(":")[0].strip()
    return found


def commands(out):
    """Every command on PATH, with the package and version it comes from."""
    paths = {}
    for d in BIN_DIRS:
        if os.path.isdir(d) and not os.path.islink(d):
            for c in os.listdir(d):
                p = os.path.join(d, c)
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    paths.setdefault(c, []).append(p)
    # dpkg may know a file under /bin or /usr/bin (merged /usr), or only its real
    # target (alternatives such as awk -> mawk). Ask for all of them.
    ask = set()
    for c, ps in paths.items():
        for p in ps:
            ask.add(p)
            ask.add(p.replace("/usr/", "/", 1) if p.startswith("/usr/") else "/usr" + p)
            ask.add(os.path.realpath(p))
    ask = sorted(ask)
    pkg_of = {}
    for i in range(0, len(ask), 500):
        pkg_of.update(owners(ask[i:i + 500]))
    versions = dict(line.split("\t") for line in subprocess.run(
        ["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"],
        stdout=subprocess.PIPE, text=True).stdout.splitlines())
    rows = []
    for c in sorted(paths):
        pkg = ""
        for p in paths[c]:
            for q in (p, p.replace("/usr/", "/", 1), "/usr" + p, os.path.realpath(p)):
                if q in pkg_of:
                    pkg = pkg_of[q]
                    break
            if pkg:
                break
        rows.append((c, pkg, versions.get(pkg, "")))
    with open(os.path.join(out, "commands.tsv"), "w") as f:
        for row in rows:
            f.write("\t".join(row) + "\n")
    return len(rows)


def main():
    rel, out, skipped_file = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    items = list(page_files())
    log(rel, f"Rendering {len(items)} man pages to plain text")
    done = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for res in pool.map(lambda it: render(it, out), items):
            if res:
                done.append(res)
    failed = len(items) - len(done)
    log(rel, f"Rendered {len(done)} pages ({failed} could not be rendered)")
    n_whatis = whatis(out)
    n_cmds = commands(out)
    log(rel, f"Indexed {n_whatis} page descriptions and {n_cmds} commands")
    osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', open("/etc/os-release").read(), re.M))
    meta = {
        "release": rel,
        "codename": osr.get("VERSION_CODENAME", ""),
        "pretty_name": osr.get("PRETTY_NAME", ""),
        "built": datetime.date.today().isoformat(),
        "man_pages": len(done),
        "man_pages_not_rendered": failed,
        "commands": n_cmds,
        "packages_skipped": open(skipped_file).read().split(),
    }
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)


if __name__ == "__main__":
    main()
