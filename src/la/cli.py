"""The la command: lookups only. It never runs commands and never reads the host."""
import json
import os
import shlex
import sys

from . import VERSION
from .data import DATA, RELEASES, Release

USAGE = f"""la {VERSION} — Linux command reference for Ubuntu {' and '.join(RELEASES)}.
Lookups only: la never runs a command and never reads this server.

  la show <command>            KB card, or the man page summary
  la show kubectl get          a docker / kubectl subcommand
  la show <command> --man      the whole man page
  la find "<task>"             which command does this?
  la explain "<command line>"  every part, checked in the man pages
  la why "<error text>"        known causes, and how to check each
  la guide list                step-by-step guides
  la guide <name> [NAME=value] one guide, with your values filled in
  <cmd> | la interpret [kind]  fixed rules over output you pipe in
  la ask "<question>"          AI answer, every command checked
  la version                   what this build holds

Options: --release 22.04|24.04 (default: this server's release)
         ask: -v shows each step;  interpret: --cpus N
Labels: ✅ KB, checked  [man] from the man page  [help] from --help
        🟡 AI-written, checked  🔴 not in the man page"""

COMMANDS = ("show", "find", "explain", "why", "guide", "interpret", "ask", "version", "help")


def cmd_version():
    with open(os.path.join(DATA, "kb.json")) as f:
        kb = json.load(f)
    n_ex = sum(len(c["examples"]) for c in kb["cards"].values())
    print(f"la {VERSION}")
    print(f"KB: {len(kb['cards'])} command cards ({n_ex} examples), "
          f"{len(kb['guides'])} guides, {len(kb['errors'])} error entries")
    for r in RELEASES:
        m = kb["releases"][r]
        print(f"{m['pretty_name']}: {m['man_pages']} man pages, {m['commands']} commands, "
              f"collected {m['built']}")
    m = kb["releases"][RELEASES[-1]]
    if m.get("help_sources"):
        src = m["help_sources"]
        docker = Release(RELEASES[-1]).commands.get("docker", ("", ""))[1].split("-")[0]
        print(f"Docker: docker-ce-cli {docker.split(':')[-1]}, {src.get('docker compose', '')}")
        print(f"Kubernetes: {src.get('kubectl', '')}; "
              f"{len(m.get('help_pages', {}))} pages from --help")
    return 0


def _take_option(args, name, with_value=True):
    """Remove --name VALUE / --name=VALUE (or a bare --name) from args; return the value."""
    for i, a in enumerate(args):
        if a == name:
            if not with_value:
                del args[i]
                return True
            if i + 1 >= len(args):
                raise SystemExit(f"{name} needs a value")
            v = args[i + 1]
            del args[i:i + 2]
            return v
        if with_value and a.startswith(name + "="):
            del args[i]
            return a.split("=", 1)[1]
    return None if with_value else False


def _stdin(optional=False, wait=3.0):
    """Text piped in. optional: give up when nothing arrives within `wait` seconds, so a
    stdin that is open but never written to (cron, ssh without -t) can't hang la ask."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    try:
        if optional:
            import select
            ready, _, _ = select.select([sys.stdin], [], [], wait)
            if not ready:
                return ""
        return sys.stdin.read(4_000_000)
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


def _release(chosen):
    rel = chosen or os.environ.get("LA_RELEASE", "").strip()
    if not rel:
        print("Note: no release given (the la wrapper passes this server's release); "
              f"using {RELEASES[-1]}. Choose one with --release.", file=sys.stderr)
        rel = RELEASES[-1]
    if rel not in RELEASES:
        raise SystemExit(f"Ubuntu {rel} is not in this build (it has {', '.join(RELEASES)}). "
                         f"Choose one with --release {RELEASES[-1]}.")
    return Release(rel)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if args else 2
    if args[0] in ("version", "--version"):
        return cmd_version()
    # Options before the subcommand.
    release = None
    while args and args[0].startswith("--release"):
        release = _take_option(args, "--release")
    if not args:
        print(USAGE)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd not in COMMANDS:
        print(f"Unknown command '{cmd}'. These are: {', '.join(COMMANDS)}.\n")
        print(USAGE)
        return 2
    if cmd == "explain":
        # The command line is one quoted argument; options may follow it.
        if len(rest) > 1 and rest[1].startswith("--release"):
            release = _take_option(rest, "--release") or release
        line = rest[0] if len(rest) == 1 else shlex.join(rest)
    else:
        release = _take_option(rest, "--release") or release
    rel = _release(release)
    from .kbdata import KB
    kb = KB()

    if cmd == "show":
        full = _take_option(rest, "--man", with_value=False)
        if not rest:
            print("usage: la show <command>   e.g. la show ss, la show kubectl get")
            return 2
        if len(rest) > 1 and not rel.man("-".join(rest)):
            print(f"No page for '{' '.join(rest)}'. Subcommand pages: la show kubectl get, "
                  "la show docker container ls.   One command: la show ss")
            return 2
        from . import show
        return show.run(rel, kb, "-".join(rest), full_man=full)
    if cmd == "find":
        if not rest:
            print('usage: la find "<task>"   e.g. la find "free disk space"')
            return 2
        from . import find
        return find.run(rel, kb, " ".join(rest))
    if cmd == "explain":
        if not rest:
            print("usage: la explain \"<command line>\"   e.g. la explain \"ss -tulpn\"")
            return 2
        from . import explain
        return explain.run(rel, kb, line)
    if cmd == "why":
        from . import why
        if rest:
            return why.run(rel, kb, " ".join(rest))
        text = _stdin()
        if not text.strip():
            print('usage: la why "<error text>"   or: journalctl -u <unit> | la why')
            return 2
        return why.run(rel, kb, text, from_stdin=True)
    if cmd == "guide":
        from . import guide
        if not rest or rest[0] == "list":
            return guide.list_all(kb)
        return guide.run(rel, kb, rest[0], rest[1:])
    if cmd == "interpret":
        from . import interpret
        cpus = _take_option(rest, "--cpus")
        opts = {}
        if cpus:
            if not cpus.isdigit() or int(cpus) < 1:
                print("--cpus needs a whole number, e.g. --cpus 8 (run nproc to see it)")
                return 2
            opts["cpus"] = int(cpus)
        return interpret.run(rel, kb, rest[0] if rest else None, _stdin(), opts)
    if cmd == "ask":
        from . import ask
        verbose = _take_option(rest, "-v", with_value=False)
        if not rest:
            print('usage: la ask "<question>"   e.g. la ask "which process uses port 443?"')
            return 2
        return ask.run(rel, kb, " ".join(rest), pasted=_stdin(optional=True), verbose=verbose)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
