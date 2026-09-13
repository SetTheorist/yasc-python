"""Minimal distinguishing feature sets (plan P10; ``orig-notes/scer.txt`` "minimize";
design §8 R20 and §13 entry 167).

``minimize(phones, forced=())`` chooses, greedily, the feature whose addition gives the
partition of the phones with the highest entropy, until every phone with a distinct bundle
is distinguished; it then drops greedy choices that later ones made redundant, and reports
which excluded features are predictable from small subsets of the chosen ones.

The ``ret.py`` prototype (``minimize_phonology``) is rewritten, fixing R20:

- ``features_used = [].extend(forced)`` was ``None``, so it crashed at once: the forced
  features are copied into a new list;
- it stopped when the best entropy equalled the previous one, which also happens when every
  feature is needed (its own TODO) and left ``best_feature = None``: the loop stops when the
  target number of classes is reached or no candidate is left;
- phones with identical bundles can never be told apart: they are reported as ``merged``.

Command line::

    python3 -m yasc.tools.minimize [SCRIPT] [--orthography NAME] [--phones "p t k"]
                                   [--force F ...] [--json] [--no-predict]

``SCRIPT`` defaults to ``lib:ipa.yasc``. Exit status: 0 on success, 1 when the script does not
load, 2 on a usage error (an unknown phone, orthography or feature).
"""

import argparse
import itertools
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from ..compile import LIB_DIR, LIB_PREFIX, compile_file
from ..errors import YascError

__all__ = ["Minimization", "minimize", "candidates", "spec_item", "load", "phones_of", "select_orthography", "main"]


def spec_item(feature, value: Optional[str]) -> str:
    """One constraint of a segment spec for ``feature = value`` (spec §6.2): ``+F``, ``!F``,
    ``2F``, ``[H]F``, or ``_F`` for an unspecified value."""
    if value is None:
        return "_" + feature.name
    return ("[%s]%s" if value.isalpha() else "%s%s") % (value, feature.name)


def candidates(fs) -> List:
    """The features a minimal set may use: segment-scope leaves without a tier (spec §4.1,
    §4.3); Node values are derived from their daughters, so nodes are never candidates."""
    return [f for f in fs.features if not f.is_node and f.is_plain]


def _classes(segs, idxs) -> int:
    return len({tuple(s.values[i] for i in idxs) for s in segs})


def _entropy(segs, idxs) -> float:
    counts: Dict[tuple, int] = {}
    for s in segs:
        key = tuple(s.values[i] for i in idxs)
        counts[key] = counts.get(key, 0) + 1
    n = float(len(segs))
    return -sum(c / n * math.log(c / n) for c in counts.values())


class Minimization:
    """The result of :func:`minimize` (plan P10): ``features`` (chosen names, forced first),
    ``forced``, ``phones`` (names in input order), ``bundles`` (each phone's values on the
    chosen features), ``merged`` (groups of phones with identical input bundles) and
    ``predictable`` (excluded feature -> (determining features, [(conditions, value)]))."""

    def __init__(self, features, forced, phones, bundles, merged, predictable):
        self.features: Tuple[str, ...] = tuple(features)
        self.forced: Tuple[str, ...] = tuple(forced)
        self.phones: Tuple[str, ...] = tuple(phones)
        self.bundles: Dict[str, str] = bundles
        self.merged: Tuple[Tuple[str, ...], ...] = tuple(merged)
        self.predictable = predictable

    def to_dict(self) -> dict:
        """A JSON-ready dictionary, the ``--json`` output (design §13 entry 167)."""
        return {"features": list(self.features), "forced": list(self.forced),
                "bundles": dict(self.bundles), "merged": [list(g) for g in self.merged],
                "predictable": {f: {"from": list(d), "rules": [[c, v] for c, v in rules]}
                                for f, (d, rules) in self.predictable.items()}}

    def format(self) -> str:
        """The human-readable report printed by the command line (design §13 entry 167)."""
        out = ["features (%d): %s" % (len(self.features), " ".join(self.features))]
        width = max((len(p) for p in self.phones), default=1)
        out += ["  %-*s  %s" % (width, p, self.bundles[p]) for p in self.phones]
        if self.merged:
            out.append("not distinguishable (identical bundles): "
                       + "; ".join(" ".join(g) for g in self.merged))
        if self.predictable:
            out.append("predictable from the chosen features:")
            for f, (det, rules) in self.predictable.items():
                shown = [(c, v) for c, v in rules if not v.startswith("_")] or rules   # --json has all
                out.append("  %s  <- %s:  %s" % (f, " ".join(det) or "(constant)",
                                                 "; ".join("%s -> %s" % (c or "{}", v) for c, v in shown)))
        return "\n".join(out) + "\n"


def minimize(phones: Sequence[Tuple[str, object]], forced: Sequence[str] = (), *,
             predict: bool = True, max_determinants: int = 2) -> Minimization:
    """Choose a small set of features that distinguishes every phone (plan P10; scer.txt).

    ``phones`` is a sequence of ``(name, Segment)``; ``forced`` names features (aliases
    allowed) that are always kept. Ties go to the feature declared first, so the result is
    deterministic. Raises ``ValueError`` for an unusable forced feature."""
    if not phones:
        raise ValueError("no phones to distinguish")
    names = [n for n, _ in phones]
    segs = [s for _, s in phones]
    fs = segs[0].system
    cands = candidates(fs)
    chosen: List[int] = []
    for name in forced:
        feat = fs.get(name)
        if feat is None or feat not in cands:
            raise ValueError("cannot force %r: not a segment-level leaf feature" % name)
        if feat.index not in chosen:
            chosen.append(feat.index)
    n_forced = len(chosen)
    all_idx = [f.index for f in cands]
    target = _classes(segs, all_idx)
    while _classes(segs, chosen) < target:
        best, best_e = None, -1.0
        for f in all_idx:
            if f not in chosen:
                e = _entropy(segs, chosen + [f])
                if e > best_e + 1e-12:
                    best, best_e = f, e
        if best is None:                        # cannot happen while classes < target
            break
        chosen.append(best)
    for f in reversed(chosen[n_forced:]):       # drop greedy picks that later ones made redundant
        rest = [g for g in chosen if g != f]
        if _classes(segs, rest) == target:
            chosen = rest
    feats = [fs.features[i] for i in chosen]
    bundles = {n: "{%s}" % " ".join(spec_item(f, s.values[f.index]) for f in feats if s.values[f.index] is not None)
               for n, s in zip(names, segs)}
    groups: Dict[object, List[str]] = {}
    for n, s in zip(names, segs):
        groups.setdefault(tuple(s.values[i] for i in all_idx), []).append(n)
    merged = [tuple(g) for g in groups.values() if len(g) > 1]
    predictable = _predictable(fs, segs, chosen, all_idx, max_determinants) if predict else {}
    return Minimization([f.name for f in feats], [fs.features[i].name for i in chosen[:n_forced]],
                        names, bundles, merged, predictable)


def _predictable(fs, segs, chosen, all_idx, max_det):
    """Excluded features whose value is a function of at most ``max_det`` chosen ones."""
    out = {}
    for f in all_idx:
        if f in chosen:
            continue
        for k in range(0, max_det + 1):
            found = None
            for det in itertools.combinations(chosen, k):
                table: Dict[tuple, Optional[str]] = {}
                if all(table.setdefault(tuple(s.values[d] for d in det), s.values[f]) == s.values[f] for s in segs):
                    found = (det, table)
                    break
            if found:
                det, table = found
                feat = fs.features[f]
                rules = [(" ".join(spec_item(fs.features[d], v) for d, v in zip(det, key)), spec_item(feat, val))
                         for key, val in table.items()]
                out[feat.name] = (tuple(fs.features[d].name for d in det), rules)
                break
    return out


def load(path: str):
    """Compile a script (``lib:NAME`` names the standard library, design §13 entry 163)."""
    if path.startswith(LIB_PREFIX):
        path = os.path.join(LIB_DIR, path[len(LIB_PREFIX):])
    return compile_file(path)


def phones_of(orth, texts: Optional[Sequence[str]] = None) -> List[Tuple[str, object]]:
    """``(text, Segment)`` pairs: each text parsed with ``orth`` (it must give exactly one
    segment, diacritics allowed), or every declared grapheme when ``texts`` is ``None``
    (spec §5.6 parsing; design §13 entry 167)."""
    if texts is None:
        return [(g, orth.phone_segment(g)) for g in orth.graphemes]
    out = []
    for t in texts:
        try:
            form = orth.parse(t)
        except YascError as e:
            raise ValueError("cannot parse phone %r: %s" % (t, e.message if hasattr(e, "message") else e)) from None
        if form.n != 1:
            raise ValueError("%r is %d segments, not one phone" % (t, form.n))
        out.append((t, form.seg(0)))
    return out


def select_orthography(compiled, name: Optional[str]):
    """The orthography named ``name`` (with or without ``$``), or the active one (spec §10.3)."""
    if not name:
        return compiled.orthography
    orth = compiled.orthographies.get(name.lstrip("$"))
    if orth is None:
        raise ValueError("unknown orthography %r (the script defines: %s)"
                         % (name, ", ".join("$" + n for n in compiled.orthographies) or "none"))
    return orth


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python3 -m yasc.tools.minimize`` (plan P10); returns the exit status."""
    ap = argparse.ArgumentParser(prog="python3 -m yasc.tools.minimize",
                                 description="Find a minimal set of features that distinguishes every phone.")
    ap.add_argument("script", nargs="?", default="lib:ipa.yasc",
                    help="script whose orthography declares the phones (default: lib:ipa.yasc)")
    ap.add_argument("-O", "--orthography", metavar="NAME", help="orthography to use (default: the active one)")
    ap.add_argument("--phones", metavar="LIST", help="whitespace-separated phones (default: every grapheme)")
    ap.add_argument("--force", nargs="+", default=[], metavar="F", help="features that must be kept")
    ap.add_argument("--json", action="store_true", help="print the result as one JSON object")
    ap.add_argument("--no-predict", action="store_true", help="do not look for predictable features")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if not e.code else 2
    try:
        compiled = load(args.script)
    except (YascError, OSError) as e:
        sys.stderr.write("%s\n" % e)
        return 1
    try:
        orth = select_orthography(compiled, args.orthography)
        phones = phones_of(orth, args.phones.split() if args.phones else None)
        res = minimize(phones, args.force, predict=not args.no_predict)
    except ValueError as e:
        sys.stderr.write("error: %s\n" % e)
        return 2
    sys.stdout.write(json.dumps(res.to_dict(), ensure_ascii=False) + "\n" if args.json else res.format())
    return 0


if __name__ == "__main__":
    sys.exit(main())
