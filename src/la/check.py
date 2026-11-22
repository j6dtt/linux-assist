"""The KB check. Runs during the image build; any problem stops the build.

For every command in every card, guide and error entry, on every release it is tagged for:
  1. it parses;
  2. each command in it is installed in that release's stock packages (or is docker or kubectl);
  3. each flag is in that command's man page (kubectl, docker compose: its --help text);
  4. it is not labelled lower than destructive when it erases data;
  5. a card's examples use the card's command;
  6. see_also names only known commands.
Writes the KB, with what passed, to one JSON file the app reads.

Usage: python -m la.check --kb DIR --data DIR --out FILE
"""
import argparse
import json
import sys

from . import cmdline, danger, kb as kbmod
from .data import RELEASES, Release
from .verify import check_line


def main(argv=None):
    ap = argparse.ArgumentParser(prog="la.check")
    ap.add_argument("--kb", required=True)
    ap.add_argument("--data", required=True, help="folder that holds data/<release>/")
    ap.add_argument("--out", help="where to write the checked KB (JSON)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    kb, problems = kbmod.load(a.kb)
    rels = {r: Release(r, a.data) for r in RELEASES}
    checked = 0
    for where, item, card_cmd, extra in kbmod.runnable_items(kb):
        line = item.get("cmd", "")
        if not line:
            continue
        try:
            invs = cmdline.parse(line)
        except ValueError as e:
            problems.append(f"{where}: can't read the command ({e}): {line}")
            continue
        if not invs:
            problems.append(f"{where}: no command found in: {line}")
            continue
        if card_cmd and card_cmd not in [i.command for i in invs]:
            problems.append(f"{where}: does not use {card_cmd}: {line}")
        for inv in invs:
            reason = danger.destructive(inv)
            if reason and item.get("danger") != "destructive":
                problems.append(f"{where}: labelled {item.get('danger')} but {reason}: {line}")
        passed = []
        for r in item.get("releases", []):
            results = check_line(rels[r], line, extra, card_cmd)
            bad = [p for res in results for p in res.problems]
            for p in bad:
                problems.append(f"{where}: {p}: {line}")
            if not bad:
                passed.append(r)
            checked += 1
        item["verified"] = passed

    # see_also may only name commands this build knows: a KB card or a stock command.
    for name, card in kb["cards"].items():
        for other in card.get("see_also", []):
            if other not in kb["cards"] and not any(other in rels[r].commands for r in RELEASES):
                problems.append(f"card {name}: see_also names {other}, which is neither a KB card "
                                "nor a command in the stock packages")

    if problems:
        print(f"KB check FAILED: {len(problems)} problem(s). The build stops here.")
        for p in problems:
            print(f"  - {p}")
        return 1
    n_ex = sum(len(c["examples"]) for c in kb["cards"].values())
    if not a.quiet:
        print(f"KB check passed: {len(kb['cards'])} command cards ({n_ex} examples), "
              f"{len(kb['guides'])} guides, {len(kb['errors'])} error entries.")
        print(f"  {checked} command checks on Ubuntu {' and '.join(RELEASES)}: every command is "
              "installed (stock packages, Docker's CLI, kubectl) and every flag is in its man "
              "page or --help text.")
    if a.out:
        kb["releases"] = {r: rels[r].meta for r in RELEASES}
        with open(a.out, "w") as f:
            json.dump(kb, f, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
