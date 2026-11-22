"""Word search over the KB and the man page descriptions. No model: plain scoring.

Each query word scores by where it appears: a card's name or task (3), its summary or an
example's description (2), a man page description (1). The whole query appearing in a
task scores extra.
"""
import math
import re

STOP = {"a", "an", "the", "to", "of", "in", "on", "for", "is", "are", "my", "how", "do", "does",
        "i", "what", "which", "with", "from", "and", "or", "it", "be", "can", "this", "that",
        "me", "you", "want", "need", "should", "would", "there", "at", "by", "as", "if", "why",
        "when", "where", "who", "linux", "ubuntu", "server", "command", "commands", "using", "up",
        "get", "show", "see", "make", "all", "any", "some", "one", "check", "view", "display",
        "find", "out", "into", "new"} - {"find"}
# A few plain-English words and the words the KB uses for the same thing.
SYNONYMS = {
    "delete": ["remove"], "remove": ["delete"], "folder": ["directory"], "directory": ["folder"],
    "dir": ["folder"], "ip": ["address"], "firewall": ["ufw", "nftables"], "log": ["journal"],
    "logs": ["journal"], "service": ["systemd", "unit"], "daemon": ["service"],
    "dns": ["resolution", "resolve"], "account": ["user"], "user": ["account"],
    "storage": ["disk"], "space": ["disk"], "ram": ["memory"], "mem": ["memory"],
    "slow": ["load"], "speed": ["load"], "kill": ["stop"], "stop": ["kill"],
    "package": ["apt"], "install": ["apt"], "upgrade": ["update"], "update": ["upgrade"],
    "partition": ["disk"], "volume": ["lvm"], "lvm": ["volume"], "raid": ["mdadm"],
    "cert": ["certificate"], "certificate": ["tls"], "ssl": ["tls"], "tls": ["certificate"],
    "port": ["socket"], "permission": ["permissions"], "copy": ["sync"], "zip": ["archive"],
    "compress": ["archive"], "backup": ["archive", "copy"], "cron": ["scheduled"],
    "schedule": ["timer", "cron"], "hostname": ["host"], "reboot": ["boot"],
    "clean": ["remove", "delete"], "usage": ["use", "used"], "memory": ["swap"], "big": ["biggest", "large"], "large": ["biggest", "big"],
    "container": ["docker"], "compose": ["docker"], "image": ["docker"], "pod": ["kubectl", "kubernetes"],
    "k8s": ["kubernetes", "kubectl"], "kubernetes": ["kubectl", "k8s"], "cluster": ["kubectl"],
    "namespace": ["kubectl"], "deployment": ["kubectl"], "node": ["kubectl"], "pid": ["process"],
    "app": ["process"],
}
# Phrases rewritten before the words are split.
PHRASES = [(r"\bfree(ing)? up\b", "clean"), (r"\bclean(ing)? up\b", "clean"),
           (r"\bspace left\b", "free space"), (r"\bin use\b", "use")]


def stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(text):
    return [stem(w) for w in re.findall(r"[a-z0-9][a-z0-9._+-]*", text.lower()) if w not in STOP]


def query_terms(text):
    text = text.lower()
    for pat, rep in PHRASES:
        text = re.sub(pat, rep, text)
    # Host names, IPs and versions say nothing about which command is meant.
    base = [w for w in dict.fromkeys(words(text)) if w.count(".") < 2 and not re.fullmatch(r"[\d.:/]+", w)]
    extra = [stem(s) for w in base for s in SYNONYMS.get(w, [])]
    return base, extra


def _score(base, extra, fields, phrase, phrase_fields, idf=None):
    """fields: list of (weight, set of words). Rare words count more (idf), and a hit that
    covers more of the query's words counts more."""
    total, covered = 0.0, 0
    for terms, factor in ((base, 1.0), (extra, 0.5)):
        for t in terms:
            best = max((w for w, ws in fields if t in ws), default=0)
            if best and factor == 1.0:
                covered += 1
            total += best * factor * (idf.get(t, 1.0) if idf else 1.0)
    if phrase and any(phrase in f for f in phrase_fields):
        total += 5
    if base:
        total *= 0.4 + 0.6 * covered / len(base)
    return total


_IDF = {}


def _idf(kb):
    """Weight per word: 1.0 for a word in one card, down to 0.3 for a word in many."""
    if id(kb) in _IDF:
        return _IDF[id(kb)]
    df = {}
    for name, card in kb.cards.items():
        ws = set(words(name + " " + " ".join(card["tasks"]) + " " + card["summary"]))
        for ex in card["examples"]:
            ws |= set(words(ex["does"]))
        for w in ws:
            df[w] = df.get(w, 0) + 1
    n = max(len(kb.cards), 2)
    out = {w: max(0.3, 1 - math.log(c) / math.log(n)) for w, c in df.items()}
    _IDF[id(kb)] = out
    return out


def search(kb, rel, text, limit=8):
    """Ranked hits: dicts with kind (card, guide, error, man), id, score, and the best example."""
    base, extra = query_terms(text)
    if not base:
        return []
    idf = _idf(kb)
    phrase = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    hits = []
    for name, card in kb.cards.items():
        here, _ = kb.examples_for(card, rel.release)
        if not here:
            continue
        tasks = " ".join(card["tasks"]).lower()
        fields = [(3, set(words(name)) | {name}), (3, set(words(tasks))), (2, set(words(card["summary"])))]
        s = _score(base, extra, fields, phrase, [tasks], idf)
        if s <= 0:
            continue
        best, best_s = None, -1.0
        for ex in here:
            es = _score(base, extra, [(2, set(words(ex["does"]))), (2, set(words(ex["cmd"])))], "", [], idf)
            if es > best_s:
                best, best_s = ex, es
        hits.append({"kind": "card", "id": name, "score": s + best_s * 0.25, "example": best})
    for gid, g in kb.guides.items():
        if rel.release not in g.get("releases", []):
            continue
        tasks = " ".join(g["tasks"]).lower()
        fields = [(3, set(words(tasks))), (3, set(words(g["title"]))), (1, set(words(gid)))]
        s = _score(base, extra, fields, phrase, [tasks, g["title"].lower()], idf)
        if s > 0:
            hits.append({"kind": "guide", "id": gid, "score": s + 0.5})
    for e in kb.errors:
        text_l = text.lower()
        exact = max((len(m) for m in e["match"] if m.lower() in text_l), default=0)
        fields = [(2, set(words(e["title"]))), (2, set(words(" ".join(e["match"]))))]
        s = _score(base, extra, fields, "", [], idf) + (6 if exact else 0)
        if s > 0:
            hits.append({"kind": "error", "id": e["id"], "score": s})
    covered = {h["id"] for h in hits if h["kind"] == "card"}
    man_hits = []
    for name, sec, desc in rel.whatis():
        if name in covered or not sec[0] in "18":
            continue
        fields = [(3, {name}), (1, set(words(desc)))]
        s = _score(base, [], fields, "", [], idf)
        if s >= 2:
            man_hits.append({"kind": "man", "id": f"{name}({sec})", "name": name, "score": s * 0.6,
                             "desc": desc, "help": rel.help_pages.get(name, "")})
    man_hits.sort(key=lambda h: -h["score"])
    hits.extend(man_hits[:3])
    hits.sort(key=lambda h: -h["score"])
    if not hits:
        return []
    top = hits[0]["score"]
    return [h for h in hits if h["score"] >= top * 0.3][:limit]
