"""Terminal output: short lines (at most 80 columns), wrapped text, danger labels."""
import os
import shutil
import sys
import textwrap

LABEL = {"read-only": "READ-ONLY", "modify": "MODIFY ⚠", "destructive": "DESTRUCTIVE ⛔"}


def width():
    try:
        cols = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        cols = 0
    if cols <= 0:
        cols = shutil.get_terminal_size((80, 24)).columns
    return max(40, min(cols, 80))


def say(text="", indent=0, hang=None):
    """Print text wrapped to the terminal, indented; hang = indent of the 2nd+ lines."""
    if not text:
        print()
        return
    hang = indent if hang is None else hang
    for i, part in enumerate(str(text).split("\n")):
        lines = textwrap.wrap(part, width=width(), initial_indent=" " * (indent if i == 0 else hang),
                              subsequent_indent=" " * hang, break_on_hyphens=False,
                              break_long_words=False) or [""]
        for line in lines:
            print(line)


def cmd(text, indent=2):
    """A command line, never wrapped (so it can be copied as is)."""
    print(" " * indent + text)


def heading(text):
    print(text)


def err(text):
    print(text, file=sys.stderr)


def label(level):
    return LABEL.get(level, (level or "unrated").upper())
