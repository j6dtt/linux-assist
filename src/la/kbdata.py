"""The checked KB that the build wrote to <LA_DATA>/kb.json, as the app reads it."""
import json
import os
import re

from .data import DATA


def norm(line):
    return re.sub(r"\s+", " ", line.strip())


class KB:
    def __init__(self, path=None):
        with open(path or os.path.join(DATA, "kb.json")) as f:
            raw = json.load(f)
        self.cards = raw["cards"]
        self.guides = raw["guides"]
        self.errors = raw["errors"]
        self.releases = raw["releases"]
        self._examples = {}
        for name, card in self.cards.items():
            for ex in card["examples"]:
                self._examples.setdefault(norm(ex["cmd"]), []).append((name, ex))

    def examples_for(self, card, release):
        """(usable here, not for this release) lists of examples."""
        here, elsewhere = [], []
        for ex in card["examples"]:
            (here if release in ex.get("verified", []) else elsewhere).append(ex)
        return here, elsewhere

    def match_example(self, line, release):
        """(card name, example) when the line is exactly a KB example checked on this release."""
        for name, ex in self._examples.get(norm(line), []):
            if release in ex.get("verified", []):
                return name, ex
        return None

    def guides_using(self, command):
        out = []
        for gid, g in self.guides.items():
            for st in g["steps"]:
                if re.search(r"(^|[\s|;&(])(sudo\s+)?" + re.escape(command) + r"(\s|$)", st["cmd"]):
                    out.append(gid)
                    break
        return out
