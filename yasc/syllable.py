"""Syllabification and the syllable tier (spec §5.4, §5.7; design §11).

Phase P7.  This module provides:

- :class:`Syllable` and :class:`SyllableTier`, the immutable syllable tier a
  :class:`yasc.form.Form` carries in ``form.syllables``, with the upkeep of spec §5.4
  (:meth:`SyllableTier.after_replace`);
- :class:`SylView`, the Form-backed segment view that resolves ``Scope(Syllable)``
  features and the role pseudo-features (design §4.1, §13 entry 19);
- :class:`Syllabifier`, which runs MaxOnset or Canon (spec §5.7) with the syllable
  templates compiled to NFAs and matched by the P2 matcher.

The module keeps no global state (design §1). It does not import :mod:`yasc.form` at
module level, so :mod:`yasc.form` can import it.
"""

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .features import ROLE, SYLLABLE, FeatureSystem, FeatureType
from .marks import Mark
from .segment import EMPTY, PlainView, Segment

__all__ = [
    "ONSET", "NUCLEUS", "CODA", "ROLE_FEATURES", "declare_role_features", "view_kinds", "Syllable", "SyllableTier",
    "SylView", "Syllabifier",
]

#: Segment roles inside a syllable (spec §5.4).
ONSET = "onset"
NUCLEUS = "nucleus"
CODA = "coda"

#: The read-only role pseudo-features (spec §5.4), in declaration order.
ROLE_FEATURES = ("SylOnset", "SylNucleus", "SylCoda", "Syllabified")

# View kinds per feature index: plain (read the segment), syllable scope, or a role.
_K_PLAIN, _K_SYL, _K_ONSET, _K_NUCLEUS, _K_CODA, _K_SYLLABIFIED = range(6)
_ROLE_KIND = {"SylOnset": _K_ONSET, "SylNucleus": _K_NUCLEUS, "SylCoda": _K_CODA, "Syllabified": _K_SYLLABIFIED}
_ROLE_OF_KIND = {_K_ONSET: ONSET, _K_NUCLEUS: NUCLEUS, _K_CODA: CODA}


def declare_role_features(fs: FeatureSystem) -> None:
    """Declare the role pseudo-features ``SylOnset``, ``SylNucleus``, ``SylCoda`` and
    ``Syllabified`` as read-only Unary features of scope ``role`` in an open feature system
    (spec §5.4; notes.md §4 C2; design §13 entry 114). Names the user declared are skipped.
    The compiler calls this for every ``Phonology`` before sealing it."""
    for name in ROLE_FEATURES:
        if fs.get(name) is None:
            fs.add_feature(name, FeatureType.unary(), scope=ROLE)


#: Stored marks that separate words (spec §5.2): syllables and the upkeep never join
#: segments across them in the word domain.
_WORD_MARKS = frozenset({Mark.WORD, Mark.PHRASE})


def _runs(positions: Sequence[int]) -> List[Tuple[int, int]]:
    """Maximal runs ``[a, b)`` of consecutive integers in a sorted sequence."""
    out: List[Tuple[int, int]] = []
    for q in positions:
        if out and out[-1][1] == q:
            out[-1] = (out[-1][0], q + 1)
        else:
            out.append((q, q + 1))
    return out


class _Frame:
    """A minimal :class:`~yasc.marks.FormLike` over segments and stored gaps, used to match
    syllable templates (which contain no boundaries or brackets) during the upkeep."""

    __slots__ = ("segs", "gaps", "n")

    def __init__(self, segs: Sequence[Segment], gaps: Optional[Sequence[frozenset]]) -> None:
        self.segs = segs
        self.n = len(segs)
        self.gaps = gaps

    def seg(self, k: int) -> Segment:
        return self.segs[k]

    def view(self, k: int) -> PlainView:
        return PlainView(self.segs[k])

    def gap_marks(self, g: int) -> frozenset:
        marks = frozenset() if self.gaps is None else frozenset(self.gaps[g])
        return marks | {Mark.PHRASE} if g in (0, self.n) else marks

    @property
    def brackets(self) -> Tuple:
        return ()


def view_kinds(fs: FeatureSystem) -> Tuple[int, ...]:
    """For each feature index of ``fs``: how a :class:`SylView` reads it (design §4.1)."""
    out = []
    for f in fs.features:
        if f.scope == SYLLABLE:
            out.append(_K_SYL)
        elif f.scope == ROLE:
            out.append(_ROLE_KIND.get(f.name, _K_PLAIN))
        else:
            out.append(_K_PLAIN)
    return tuple(out)


class Syllable:
    """One syllable: segments ``start..end-1``, nucleus ``nuc_start..nuc_end-1`` and the
    syllable-scope feature values ``feats`` (a :class:`Segment` of the feature system in
    which only syllable-scope slots are used) (design §11; spec §5.4).

    Segments before the nucleus are the onset, those after it the coda."""

    __slots__ = ("start", "end", "nuc_start", "nuc_end", "feats", "_hash")

    def __init__(self, start: int, end: int, nuc_start: int, nuc_end: int, feats: Segment) -> None:
        if not (start <= nuc_start < nuc_end <= end):
            raise ValueError("bad syllable span %d..%d with nucleus %d..%d" % (start, end, nuc_start, nuc_end))
        self.start = start
        self.end = end
        self.nuc_start = nuc_start
        self.nuc_end = nuc_end
        self.feats = feats
        self._hash = hash((start, end, nuc_start, nuc_end, feats))

    def key(self) -> Tuple[int, int, int, int, Segment]:
        """The value tuple used for equality (design §4.3: forms compare by value)."""
        return (self.start, self.end, self.nuc_start, self.nuc_end, self.feats)

    def role(self, i: int) -> str:
        """The role of segment ``i`` (which must lie in the syllable) (spec §5.4)."""
        if i < self.nuc_start:
            return ONSET
        if i < self.nuc_end:
            return NUCLEUS
        return CODA

    def with_feats(self, feats: Segment) -> "Syllable":
        """The same span with other syllable-scope values (spec §5.4 "Syllable features")."""
        return Syllable(self.start, self.end, self.nuc_start, self.nuc_end, feats)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Syllable) and self.key() == other.key()

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return "Syllable(%d..%d, nucleus %d..%d, %s)" % (self.start, self.end, self.nuc_start, self.nuc_end,
                                                         self.feats.canonical())


class SylView:
    """A Form-backed :class:`~yasc.segment.SegView` (design §4.1, §13 entry 19): plain
    features come from the segment, ``Scope(Syllable)`` features from its syllable (``None``
    when it has none), and the role pseudo-features are ``'!'`` or ``None`` (spec §5.4)."""

    __slots__ = ("segment", "tier", "index")

    def __init__(self, segment: Segment, tier: "SyllableTier", index: int) -> None:
        self.segment = segment
        self.tier = tier
        self.index = index

    def __getitem__(self, idx: int) -> Optional[str]:
        kind = self.tier.kinds[idx]
        if kind == _K_PLAIN:
            return self.segment.values[idx]
        s = self.tier.seg_to_syl[self.index]
        if s is None:
            return None
        if kind == _K_SYL:
            return self.tier.syls[s].feats.values[idx]
        if kind == _K_SYLLABIFIED:
            return "!"
        return "!" if self.tier.syls[s].role(self.index) == _ROLE_OF_KIND[kind] else None

    def get(self, feature) -> Optional[str]:
        """Value of a feature by name, index or object (spec §5.4); Nodes as on
        :class:`Segment`."""
        f = self.segment.system.resolve(feature)
        if f.is_node:
            return self.segment.get(f)
        return self[f.index]

    def present(self, feature) -> bool:
        """Whether a leaf is specified, or a Node present (spec §4.3 rule 2)."""
        f = self.segment.system.resolve(feature)
        if f.is_node:
            return self.segment.present(f)
        return self[f.index] is not None

    def __repr__(self) -> str:
        return "SylView(%r, %d)" % (self.segment, self.index)


class SyllableTier:
    """The syllable tier of a form with ``n`` segments (design §11; spec §5.4).

    ``syls`` are non-overlapping :class:`Syllable` objects in order; ``seg_to_syl[i]`` is
    the index in ``syls`` of segment ``i``'s syllable, or ``None`` (unsyllabified).
    ``syllabifier`` is the :class:`Syllabifier` that built the tier (``None`` for a tier
    built by hand); the upkeep uses its templates to re-attach orphans (spec §5.4 [Δ] S2),
    and ``persistent`` asks the rule engine to recompute the tier after every rule that
    changes the form (spec §5.7 ``Persistent``).

    Tiers are immutable values: equality and hashing use ``n`` and ``syls``, so two forms
    with the same syllables and syllable features are equal (design §4.3)."""

    __slots__ = ("fs", "n", "syls", "seg_to_syl", "syllabifier", "kinds", "edges", "_hash")

    #: ``Form.replace`` passes ``segs`` and ``gaps`` to :meth:`after_replace` (design §12, P7).
    takes_frame = True

    def __init__(self, fs: FeatureSystem, n: int, syls: Iterable[Syllable], syllabifier: Any = None,
                 kinds: Optional[Tuple[int, ...]] = None) -> None:
        syls = tuple(sorted(syls, key=lambda s: s.start))
        owner: List[Optional[int]] = [None] * n
        prev_end = 0
        for k, s in enumerate(syls):
            if s.start < prev_end or s.end > n:
                raise ValueError("syllables overlap or lie outside 0..%d: %r" % (n, syls))
            prev_end = s.end
            for p in range(s.start, s.end):
                owner[p] = k
        self.fs = fs
        self.n = n
        self.syls = syls
        self.seg_to_syl: Tuple[Optional[int], ...] = tuple(owner)
        self.syllabifier = syllabifier
        self.kinds = kinds if kinds is not None else view_kinds(fs)
        edges = set()
        for s in syls:
            edges.add(s.start)
            edges.add(s.end)
        self.edges: frozenset = frozenset(edges)
        self._hash = hash((n, syls))

    @property
    def persistent(self) -> bool:
        """True if the syllabification that built the tier is ``Persistent`` (spec §5.7)."""
        return bool(self.syllabifier is not None and self.syllabifier.persistent)

    def evolve(self, n: int, syls: Iterable[Syllable]) -> "SyllableTier":
        """A tier of the same system and syllabifier over new syllables."""
        return SyllableTier(self.fs, n, syls, self.syllabifier, self.kinds)

    # -- queries ---------------------------------------------------------------------------

    def syllable_of(self, i: int) -> Optional[Syllable]:
        """The syllable of segment ``i``, or ``None`` if it is unsyllabified (spec §5.4)."""
        k = self.seg_to_syl[i]
        return None if k is None else self.syls[k]

    def role(self, i: int) -> Optional[str]:
        """``'onset'``, ``'nucleus'``, ``'coda'`` or ``None`` for segment ``i`` (design §11)."""
        k = self.seg_to_syl[i]
        return None if k is None else self.syls[k].role(i)

    def spans(self) -> Tuple[Tuple[int, int], ...]:
        """``(start, end)`` of every syllable, in order."""
        return tuple((s.start, s.end) for s in self.syls)

    # -- syllable features (spec §5.4) -----------------------------------------------------

    def write(self, i: int, spec, env, weak: bool = False) -> Optional["SyllableTier"]:
        """Apply the syllable-scope output ``spec`` to the syllable of segment ``i``
        (spec §5.4 "Writing a syllable feature onto a segment writes it to that segment's
        syllable"). Returns the new tier (``self`` if nothing changed), or ``None`` if the
        segment is unsyllabified: then the write is a no-op the caller may warn about."""
        k = self.seg_to_syl[i]
        if k is None:
            return None
        syl = self.syls[k]
        new = spec.weak_apply(syl.feats, env) if weak else spec.apply(syl.feats, env)
        if new == syl.feats:
            return self
        syls = list(self.syls)
        syls[k] = syl.with_feats(new)
        return self.evolve(self.n, syls)

    # -- upkeep after rules (spec §5.4) ----------------------------------------------------

    def after_replace(self, i: int, j: int, k: int, index_map: Sequence[Optional[int]], *,
                      segs: Optional[Sequence[Segment]] = None, gaps: Optional[Sequence[frozenset]] = None
                      ) -> "SyllableTier":
        """The tier after ``Form.replace(i, j, new)`` with ``k = len(new)`` (spec §5.4
        "Upkeep after rules"; design §4.3 step 4, §13 entries 115–117).

        * Positions mapped by ``index_map`` keep their syllable and role, so feature changes
          keep the structure. Inside ``[i, j)`` a replaced segment the map deletes is paired
          with a new segment the map does not reach, in order: a segment replaced by a copy
          (``$n``, metathesis) keeps its place in the structure.
        * A deleted segment leaves its syllable. A syllable left without a nucleus is
          dissolved; its remaining segments (orphans) are re-attached with the templates of
          :attr:`syllabifier` (S2): the longest run at the right end that forms, with the next
          syllable's onset, an ``Onset`` joins that syllable; of the rest, the longest run at
          the left end that extends the previous syllable's coda to a ``Coda`` joins it; the
          others are unsyllabified. Neighbours must be in the same word.
        * An inserted segment joins the syllable of its left neighbour in the same word, or
          else of its right neighbour, taking that neighbour's role.

        ``segs`` and ``gaps`` are the new form's segments and stored marks (``Form.replace``
        passes them); without ``gaps`` every neighbour counts as in the same word, and
        without ``segs`` orphans stay unsyllabified.
        """
        n_old = self.n
        n_new = n_old - (j - i) + k
        if j - i == k and all(index_map[p] == p for p in range(i, j)):
            return self  # pure feature change: the structure is kept (spec §5.4)
        new_of = list(index_map)
        if j > i and k:
            reached = {q for q in index_map[i:j] if q is not None}
            fresh = [q for q in range(i, i + k) if q not in reached]
            gone = [p for p in range(i, j) if index_map[p] is None]
            for p, q in zip(gone, fresh):
                new_of[p] = q
        member: List[Optional[Tuple[int, str]]] = [None] * n_new
        for p in range(n_old):
            q = new_of[p]
            s = self.seg_to_syl[p]
            if q is not None and s is not None:
                member[q] = (s, self.syls[s].role(p))
        image = {q for q in new_of if q is not None}
        inserted = [q for q in range(i, i + k) if q not in image]
        # Dissolve syllables that lost their nucleus.
        alive = {m[0] for m in member if m is not None and m[1] == NUCLEUS}
        orphans = []
        for q, m in enumerate(member):
            if m is not None and m[0] not in alive:
                member[q] = None
                orphans.append(q)

        def same_word(g: int) -> bool:
            return gaps is None or not (gaps[g] & _WORD_MARKS)

        # Inserted segments take the syllable and role of a neighbour.
        for a, b in _runs(inserted):
            m = None
            if a > 0 and same_word(a) and member[a - 1] is not None:
                m = member[a - 1]
            elif b < n_new and same_word(b) and member[b] is not None:
                m = member[b]
            for q in range(a, b):
                member[q] = m
        if orphans and segs is not None and self.syllabifier is not None:
            frame = _Frame(segs, gaps)
            for a, b in _runs(orphans):
                self.syllabifier.reattach(frame, member, a, b, same_word)
        return self.evolve(n_new, self._rebuild(member))

    def _rebuild(self, member: Sequence[Optional[Tuple[int, str]]]) -> List[Syllable]:
        """Syllables from per-position ``(old syllable, role)`` entries. Each syllable keeps
        its feature values; positions outside the contiguous block around its nucleus are
        dropped (unsyllabified), which the upkeep rules never produce but keeps the tier
        valid."""
        pos: Dict[int, List[int]] = {}
        nuc: Dict[int, List[int]] = {}
        for q, m in enumerate(member):
            if m is not None:
                pos.setdefault(m[0], []).append(q)
                if m[1] == NUCLEUS:
                    nuc.setdefault(m[0], []).append(q)
        out = []
        for s, ps in pos.items():
            ns = nuc.get(s)
            if not ns:
                continue
            lo, hi = ns[0], ns[-1] + 1
            if hi - lo != len(ns):
                hi = lo + 1  # a nucleus split by an insertion elsewhere: keep its first run
                while hi in ns:
                    hi += 1
            start, end = lo, hi
            members = set(ps)
            while start - 1 in members:
                start -= 1
            while end in members:
                end += 1
            out.append(Syllable(start, end, lo, hi, self.syls[s].feats))
        return out

    # -- value semantics -------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SyllableTier) and self.n == other.n and self.syls == other.syls

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:  # noqa: D105
        return "SyllableTier(n=%d, %s)" % (self.n, ", ".join(
            "%d..%d/%d..%d%s" % (s.start, s.end, s.nuc_start, s.nuc_end,
                                 (" " + s.feats.canonical()) if any(v is not None for v in s.feats.values) else "")
            for s in self.syls))


# ------------------------------------------------------------------------------------------
# Syllabification (spec §5.7)
# ------------------------------------------------------------------------------------------


def _yes(value: Any) -> bool:
    return str(value) == "yes"


class Syllabifier:
    """A compiled ``Syllabification`` section (spec §5.7; design §11).

    Built from a :class:`~yasc.ir.SyllabificationDef`. Settings and their defaults:
    ``Algorithm MaxOnset``, ``OnsetRequired no``, ``NucleusPreference first``,
    ``Persistent no``, ``Domain word``, ``AllowUnsyllabified no``. The ``Onset``,
    ``Nucleus`` and ``Coda`` templates are the NFAs the compiler built with the P2 pattern
    compiler; a missing or empty template matches only the empty string.

    :meth:`syllabify` returns a form with a fresh :class:`SyllableTier`. Existing stored
    syllable, word (``Domain word``) and phrase marks are hard boundaries.
    """

    def __init__(self, definition: Any) -> None:
        d = definition
        st = dict(d.settings)
        self.definition = d
        self.name = d.name
        self.fs: FeatureSystem = d.phonology
        self.algorithm = str(st.get("Algorithm", "MaxOnset"))
        self.onset_required = _yes(st.get("OnsetRequired", "no"))
        self.nucleus_preference = str(st.get("NucleusPreference", "first"))
        self.persistent = _yes(st.get("Persistent", "no"))
        self.domain = str(st.get("Domain", "word"))
        self.allow_unsyllabified = _yes(st.get("AllowUnsyllabified", "no"))
        tpl = d.templates
        self.onset = tpl.get("Onset", (None, None))[1]
        self.nucleus = tpl.get("Nucleus", (None, None))[1]
        self.coda = tpl.get("Coda", (None, None))[1]
        canons = tuple(st.get("Canons", ()) or ())
        self.letters: Dict[str, Tuple[Any, ...]] = dict(getattr(d, "letters", {}) or {})
        # (shape, score, nucleus offset start, nucleus offset end): the first canon scores
        # highest (spec §5.7 "the order gives the score"); the nucleus is the span from the
        # first to the last V of the shape.
        self.canons: Tuple[Tuple[str, int, int, int], ...] = tuple(
            (c, len(canons) - r, c.index("V"), c.rindex("V") + 1) for r, c in enumerate(canons) if "V" in c)
        self.kinds = view_kinds(self.fs)

    def __repr__(self) -> str:
        return "<Syllabifier %s%s>" % ((self.name + ": ") if self.name else "", self.algorithm)

    # -- template matching (P2 matcher) -----------------------------------------------------

    @staticmethod
    def _ends(nfa: Any, f: Any, g: int) -> List[int]:
        """End gaps of the matches of ``nfa`` anchored at gap ``g`` going right."""
        if nfa is None:
            return [g]
        from .matcher import match_anchored
        return sorted({e for e, _env in match_anchored(nfa, f, g, EMPTY, direction=1)})

    @staticmethod
    def _starts(nfa: Any, f: Any, g: int) -> List[int]:
        """Start gaps of the matches of ``nfa`` that end exactly at gap ``g``."""
        if nfa is None:
            return [g]
        from .matcher import match_anchored
        return sorted({s for s, _env in match_anchored(nfa, f, g, EMPTY, direction=-1)})

    # -- MaxOnset (spec §5.7) --------------------------------------------------------------

    def _nuclei(self, f: Any, a: int, b: int) -> List[Tuple[int, int]]:
        """Step 1: greedy left-to-right ``Nucleus`` matches (each the longest one), then
        ``NucleusPreference`` on adjacent nuclei (spec §5.7 [Δ] S3)."""
        nuclei: List[Tuple[int, int]] = []
        p = a
        while p < b:
            ends = [e for e in self._ends(self.nucleus, f, p) if p < e <= b]
            if ends:
                nuclei.append((p, ends[-1]))
                p = ends[-1]
            else:
                p += 1
        k = 0
        while k + 1 < len(nuclei):
            (s1, e1), (s2, e2) = nuclei[k], nuclei[k + 1]
            if e1 == s2:
                if self.nucleus_preference == "last":
                    lo = nuclei[k - 1][1] if k else a
                    if any(lo <= s <= s1 for s in self._starts(self.onset, f, s2)):
                        del nuclei[k]  # the first nucleus becomes part of the next onset
                        continue
                elif any(e2 <= e <= b for e in self._ends(self.coda, f, e1)):
                    del nuclei[k + 1]  # the second nucleus becomes part of this coda
                    continue
            k += 1
        return nuclei

    def _max_onset(self, f: Any, a: int, b: int, word_start: bool) -> List[Tuple[int, int, int, int]]:
        """MaxOnset over the chunk ``[a, b)``: ``(start, end, nuc_start, nuc_end)`` per
        syllable (spec §5.7). Onsets are the longest suffix of the material before a nucleus
        that matches ``Onset``; the longest prefix of the remainder that matches ``Coda`` is
        the previous coda; what is left is unsyllabified. With ``OnsetRequired`` a nucleus
        with no onset forms no syllable, except word-initially."""
        nuclei = self._nuclei(f, a, b)
        onsets: List[int] = []
        k = 0
        while k < len(nuclei):
            s, _e = nuclei[k]
            lo = nuclei[k - 1][1] if k else a
            starts = [x for x in self._starts(self.onset, f, s) if lo <= x <= s]
            o = starts[0] if starts else s
            if self.onset_required and o == s and not (word_start and s == a):
                del nuclei[k]
                continue
            onsets.append(o)
            k += 1
        out = []
        for k, (s, e) in enumerate(nuclei):
            limit = onsets[k + 1] if k + 1 < len(nuclei) else b
            ends = [x for x in self._ends(self.coda, f, e) if e <= x <= limit]
            out.append((onsets[k], ends[-1] if ends else e, s, e))
        return out

    # -- Canon (spec §5.7) -----------------------------------------------------------------

    def _letter_ok(self, letter: str, f: Any, k: int, memo: Dict[Tuple[str, int], bool]) -> bool:
        key = (letter, k)
        r = memo.get(key)
        if r is None:
            view = f.view(k)
            r = memo[key] = any(spec.matches(view) for spec in self.letters.get(letter, ()))
        return r

    def canon_parse(self, f: Any, a: int = 0, b: Optional[int] = None) -> Tuple[int, int, List[Tuple[int, int, int, int]]]:
        """The best parse of ``[a, b)`` into canons (spec §5.7 ``Algorithm Canon``):
        ``(score, unsyllabified, syllables)``. Dynamic programming from the right; the parse
        with the fewest unsyllabified segments wins, then the highest total score (canon
        ``r`` of ``m`` scores ``m - r``); ties go to the higher-ranked canon at the leftmost
        position, and to a syllable over a skipped segment (design §13 entry 119)."""
        b = f.n if b is None else b
        memo: Dict[Tuple[str, int], bool] = {}
        best: List[Any] = [None] * (b - a + 1)
        best[b - a] = (0, 0, None)
        for p in range(b - 1, a - 1, -1):
            cand = None
            for shape, score, ns, ne in self.canons:
                q = p + len(shape)
                if q > b or not all(self._letter_ok(ch, f, p + t, memo) for t, ch in enumerate(shape)):
                    continue
                u, neg, _ = best[q - a]
                if cand is None or (u, neg - score) < cand[:2]:
                    cand = (u, neg - score, (p, q, p + ns, p + ne))
            u, neg, _ = best[p + 1 - a]
            if cand is None or (u + 1, neg) < cand[:2]:
                cand = (u + 1, neg, None)
            best[p - a] = cand
        sylls = []
        p = a
        while p < b:
            choice = best[p - a][2]
            if choice is None:
                p += 1
            else:
                sylls.append(choice)
                p = choice[1]
        return -best[0][1], best[0][0], sylls

    def canon_score(self, f: Any, spans: Sequence[Tuple[int, int]]) -> Optional[int]:
        """The total canon score of a given parse ``[(start, end), ...]``, or ``None`` if a
        span is no canon (spec §5.7; used to compare parses such as the notes' abamordi)."""
        memo: Dict[Tuple[str, int], bool] = {}
        total = 0
        for s, e in spans:
            for shape, score, _ns, _ne in self.canons:
                if len(shape) == e - s and all(self._letter_ok(ch, f, s + t, memo) for t, ch in enumerate(shape)):
                    total += score
                    break
            else:
                return None
        return total

    def _canon(self, f: Any, a: int, b: int) -> List[Tuple[int, int, int, int]]:
        _score, unsyll, sylls = self.canon_parse(f, a, b)
        if unsyll and not self.allow_unsyllabified:
            return []  # an unparsable segment costs −∞: the chunk stays unsyllabified
        return sylls

    # -- the whole form --------------------------------------------------------------------

    def syllabify(self, form: Any) -> Any:
        """``form`` with a fresh syllable tier (spec §5.7, §10.3 ``!syllabify``).

        Stored syllable and phrase marks, and word marks in ``Domain word``, are hard
        boundaries: each chunk between them is syllabified on its own. A new syllable
        inherits the syllable-scope values of the old syllable that held the first segment
        of its nucleus, else they are unspecified (spec §5.4 [Δ] S1, S4). Pending syllable
        marks (``SyllableMark``, spec §5.6) set their values on the first syllable that
        starts at or after their gap, within the same chunk, and are consumed."""
        n = form.n
        gaps = form.gaps
        hard = {Mark.SYLLABLE, Mark.PHRASE} | ({Mark.WORD} if self.domain != "phrase" else set())
        cuts = [0] + [g for g in range(1, n) if not hard.isdisjoint(gaps[g])] + [n]
        spans: List[Tuple[int, int, int, int]] = []
        for a, b in zip(cuts, cuts[1:]):
            if a == b:
                continue
            if self.algorithm == "Canon":
                spans.extend(self._canon(form, a, b))
            else:
                word_start = a == 0 or not _WORD_MARKS.isdisjoint(gaps[a])
                spans.extend(self._max_onset(form, a, b, word_start))
        old = form.syllables
        empty = self.fs.empty
        syls = []
        for s, e, ns, ne in spans:
            feats = empty
            if old is not None and old.n == n and old.seg_to_syl[ns] is not None:
                feats = old.syls[old.seg_to_syl[ns]].feats
            syls.append(Syllable(s, e, ns, ne, feats))
        for g, mark in form.pending_syllable_marks:
            nxt = min([c for c in cuts if c > g] or [n])
            for idx, sy in enumerate(syls):
                if g <= sy.start < max(nxt, g + 1):
                    syls[idx] = sy.with_feats(sy.feats.merge(mark))
                    break
        return form.with_syllables(SyllableTier(self.fs, n, syls, self, self.kinds))

    # -- orphan re-attachment for the upkeep (spec §5.4 [Δ] S2) ------------------------------

    def reattach(self, f: Any, member: List[Optional[Tuple[int, str]]], a: int, b: int, same_word) -> None:
        """Re-attach the orphans ``[a, b)`` of a dissolved syllable, in place in ``member``
        (see :meth:`SyllableTier.after_replace`): first as onset of the next syllable, then
        as coda of the previous one, as far as the templates allow."""
        n = f.n
        onset_from = b
        if b < n and same_word(b) and member[b] is not None:
            sb = member[b][0]
            nb = b
            while nb < n and member[nb] is not None and member[nb][0] == sb and member[nb][1] != NUCLEUS:
                nb += 1
            if nb < n and member[nb] == (sb, NUCLEUS):
                starts = [x for x in self._starts(self.onset, f, nb) if a <= x <= b]
                if starts:
                    onset_from = starts[0]
                    for q in range(onset_from, b):
                        member[q] = (sb, ONSET)
        if a > 0 and onset_from > a and same_word(a) and member[a - 1] is not None:
            sa = member[a - 1][0]
            na = a - 1
            while na >= 0 and member[na] is not None and member[na][0] == sa and member[na][1] != NUCLEUS:
                na -= 1
            if na >= 0 and member[na] == (sa, NUCLEUS):
                ends = [x for x in self._ends(self.coda, f, na + 1) if a <= x <= onset_from]
                if ends:
                    for q in range(a, ends[-1]):
                        member[q] = (sa, CODA)
