"""Forms: segments, gaps with boundary marks, category brackets and tier placeholders
(spec §5.1–§5.3, §8.2.4; design §4.3).

A :class:`Form` is an immutable value. Rules never mutate a form: :meth:`Form.replace`
returns a new one, which keeps boundaries and brackets consistent, and gives the syllable
tier (plan P7) and autosegmental tiers (plan P8) a hook to update themselves.

``Form`` implements :class:`yasc.marks.FormLike`, the interface of the matcher (plan P2).
Record data (lexical features, date, dialect, fields; spec §10.2) is *not* part of a form;
it travels in the rule engine's ``ApplyContext`` (design §10).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .marks import Bracket, Mark
from .segment import PlainView, Segment
from .syllable import SylView
from .tiers import TierView

__all__ = ["Form", "IndexMap", "ATTACH_LEFT", "ATTACH_RIGHT", "validate_brackets"]

#: ``index_map[p]`` is the new index of old segment ``p``, or ``None`` if it was deleted.
IndexMap = Tuple[Optional[int], ...]

#: For a pure insertion at gap ``i`` (``i == j``): the inserted material joins the *left*
#: neighbour, so the marks, bracket ends and syllable marks stored at gap ``i`` end up after
#: the insertion (design §13, P3 decision 4).
ATTACH_LEFT = "left"
#: For a pure insertion: the material joins the *right* neighbour; everything stored at gap
#: ``i`` stays before the insertion.
ATTACH_RIGHT = "right"

_EMPTY: FrozenSet[Mark] = frozenset()
_PHRASE: FrozenSet[Mark] = frozenset({Mark.PHRASE})
_SYLLABLE: FrozenSet[Mark] = frozenset({Mark.SYLLABLE})


def _contains(outer: Bracket, inner: Bracket) -> bool:
    return outer.open_gap <= inner.open_gap and inner.close_gap <= outer.close_gap


def validate_brackets(brackets: Iterable[Bracket], n: int) -> Tuple[Bracket, ...]:
    """Check and normalise a bracket tuple for a form with ``n`` segments (spec §5.3).

    Brackets are returned in *document order* of their opening delimiters: a stable sort on
    ``open_gap``, so brackets opening at the same gap keep their given relative order (the
    first one is the outer one when it can contain the next). Nesting is checked with a
    stack: a bracket that can nest inside the currently open one does nest; otherwise the
    open one must end at or before the new one's start. Raises ``ValueError`` for indices
    outside ``0..n``, ``open_gap > close_gap``, or crossing spans.
    """
    brs = []
    for b in brackets:
        if not isinstance(b, Bracket):
            raise TypeError("brackets must be Bracket objects, got %r" % (b,))
        if not (0 <= b.open_gap <= b.close_gap <= n):
            raise ValueError("bracket %r lies outside gaps 0..%d or is reversed" % (b, n))
        brs.append(b)
    brs.sort(key=lambda b: b.open_gap)
    stack: List[Bracket] = []
    for b in brs:
        while stack and not _contains(stack[-1], b):
            top = stack.pop()
            if top.close_gap > b.open_gap:
                raise ValueError("brackets %r and %r cross (they must be properly nested)" % (top, b))
        stack.append(b)
    return tuple(brs)


def bracket_events(brackets: Sequence[Bracket], n: int) -> List[List[Tuple[str, Bracket]]]:
    """For each gap ``0..n``, the ordered list of ``('open'|'close', bracket)`` events
    (spec §5.3). Uses the same nesting convention as :func:`validate_brackets`, so writing
    the events back as text and re-reading them gives the same bracket tuple."""
    events: List[List[Tuple[str, Bracket]]] = [[] for _ in range(n + 1)]
    stack: List[Bracket] = []
    ptr = 0
    for g in range(n + 1):
        while True:
            nb = brackets[ptr] if ptr < len(brackets) and brackets[ptr].open_gap == g else None
            if stack and stack[-1].close_gap == g and (nb is None or not _contains(stack[-1], nb)):
                events[g].append(("close", stack.pop()))
                continue
            if nb is not None:
                events[g].append(("open", nb))
                stack.append(nb)
                ptr += 1
                continue
            break
    return events


@dataclass(frozen=True)
class Form:
    """An immutable phonological form (spec §5.1; design §4.3).

    * ``segs`` — the segments ``s[0..n-1]``;
    * ``gaps`` — ``n+1`` frozensets of *stored* :class:`~yasc.marks.Mark` (spec §5.2);
      :meth:`gap_marks` adds :attr:`Mark.PHRASE` at gaps ``0`` and ``n``;
    * ``brackets`` — properly nested :class:`~yasc.marks.Bracket` spans in document order
      (spec §5.3), validated and normalised on construction;
    * ``syllables`` — the syllable tier (plan P7); ``None`` until then;
    * ``tiers`` — autosegmental tiers (plan P8); ``()`` until then;
    * ``pending_syllable_marks`` — ``(gap, Segment)`` pairs from orthographic
      ``SyllableMark`` text (spec §5.6), holding the syllable features to set on the syllable
      that starts at ``gap``; plan P7 consumes them.

    Implements :class:`yasc.marks.FormLike`. Equality and hashing are by value.
    """

    segs: Tuple[Segment, ...]
    gaps: Tuple[FrozenSet[Mark], ...]
    brackets: Tuple[Bracket, ...] = ()
    syllables: Any = None
    tiers: Tuple[Any, ...] = ()
    pending_syllable_marks: Tuple[Tuple[int, Segment], ...] = ()
    _eff: Tuple[FrozenSet[Mark], ...] = field(init=False, repr=False, compare=False)
    _tidx: Dict[int, Any] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        segs = tuple(self.segs)
        n = len(segs)
        for s in segs:
            if not isinstance(s, Segment):
                raise TypeError("Form segments must be Segment objects, got %r" % (s,))
        gaps = tuple(frozenset(g) for g in self.gaps)
        if len(gaps) != n + 1:
            raise ValueError("a form with %d segments needs %d gaps, got %d" % (n, n + 1, len(gaps)))
        for g in gaps:
            for m in g:
                if not isinstance(m, Mark):
                    raise TypeError("gap marks must be Mark values, got %r" % (m,))
        pend = tuple((int(g), s) for g, s in self.pending_syllable_marks)
        for g, s in pend:
            if not 0 <= g <= n:
                raise ValueError("syllable mark at gap %d outside 0..%d" % (g, n))
        pend = tuple(sorted(pend, key=lambda p: p[0]))
        object.__setattr__(self, "segs", segs)
        object.__setattr__(self, "gaps", gaps)
        object.__setattr__(self, "brackets", validate_brackets(self.brackets, n))
        tiers = tuple(self.tiers)
        tidx = {}
        for t in tiers:  # plan P8: autosegmental tiers, by tier-feature index
            if getattr(t, "n", n) != n:
                raise ValueError("tier %r covers %d segments, the form has %d" % (t, t.n, n))
            idx = getattr(t, "index", None)
            if idx is not None:
                tidx[idx] = t
        object.__setattr__(self, "tiers", tiers)
        object.__setattr__(self, "_tidx", tidx)
        object.__setattr__(self, "pending_syllable_marks", pend)
        eff = list(gaps)
        eff[0] = eff[0] | _PHRASE
        eff[n] = eff[n] | _PHRASE
        edges = getattr(self.syllables, "edges", None)  # a P7 SyllableTier (P3 hook stubs have none)
        if edges is not None:
            if self.syllables.n != n:
                raise ValueError("syllable tier covers %d segments, the form has %d" % (self.syllables.n, n))
            for g in edges:
                eff[g] = eff[g] | _SYLLABLE
        object.__setattr__(self, "_eff", tuple(eff))

    # -- construction ----------------------------------------------------------------------

    @classmethod
    def from_segments(
        cls,
        segs: Iterable[Segment],
        marks: Optional[Union[Mapping[int, Iterable[Mark]], Sequence[Iterable[Mark]]]] = None,
        brackets: Iterable[Bracket] = (),
        pending_syllable_marks: Iterable[Tuple[int, Segment]] = (),
    ) -> "Form":
        """Build a form (spec §5.1). ``marks`` is either a mapping ``gap -> marks`` or a
        sequence of ``n+1`` mark collections; missing gaps are empty."""
        segs = tuple(segs)
        n = len(segs)
        if marks is None:
            gaps = [_EMPTY] * (n + 1)
        elif isinstance(marks, Mapping):
            gaps = [_EMPTY] * (n + 1)
            for g, ms in marks.items():
                if not 0 <= g <= n:
                    raise ValueError("marks given for gap %d outside 0..%d" % (g, n))
                gaps[g] = frozenset(ms)
        else:
            gaps = [frozenset(ms) for ms in marks]
        return cls(segs, tuple(gaps), tuple(brackets), None, (), tuple(pending_syllable_marks))

    # -- FormLike --------------------------------------------------------------------------

    @property
    def n(self) -> int:
        """The number of segments; gaps are numbered ``0..n`` (spec §5.1)."""
        return len(self.segs)

    def seg(self, i: int) -> Segment:
        """Segment ``i`` (spec §5.1)."""
        return self.segs[i]

    def view(self, i: int):
        """A :class:`~yasc.segment.SegView` of segment ``i`` (design §4.1). With a syllable
        tier it is a :class:`~yasc.syllable.SylView`, which resolves ``Scope(Syllable)``
        features and the role pseudo-features (spec §5.4); otherwise a plain view. With
        autosegmental tiers it is wrapped in a :class:`~yasc.tiers.TierView`, which reads
        tier features through the segment view of their tier (spec §5.5; plan P8)."""
        syl = self.syllables
        base = PlainView(self.segs[i]) if syl is None else SylView(self.segs[i], syl, i)
        if self._tidx:
            return TierView(base, self, i)
        return base

    def tier_by_index(self, idx: int):
        """The :class:`~yasc.tiers.AutoTier` of tier feature index ``idx``, or ``None``
        (spec §5.5; plan P8)."""
        return self._tidx.get(idx)

    def tier(self, name: Optional[str] = None):
        """The autosegmental tier of feature ``name`` (spec §6.5 ``^Tier.X``), or with no
        name the first one (the single tier); ``None`` if the form has no such tier."""
        for t in self._tidx.values():
            if name is None or t.feature.name == name or name in getattr(t.feature, "aliases", ()):
                return t
        return None

    def with_tiers(self, tiers: Iterable[Any]) -> "Form":
        """A copy with new autosegmental tiers (spec §5.5; plan P8)."""
        return self._evolve(tiers=tuple(tiers))

    def gap_marks(self, g: int) -> FrozenSet[Mark]:
        """Effective marks of gap ``g``: the stored marks, plus :attr:`Mark.PHRASE` at gaps
        ``0`` and ``n`` (spec §5.2), plus :attr:`Mark.SYLLABLE` at every gap that starts or
        ends a syllable of the syllable tier (spec §5.4 "the ``.`` assertion")."""
        return self._eff[g]

    def __len__(self) -> int:
        return len(self.segs)

    # -- derived forms ---------------------------------------------------------------------

    def _evolve(self, **changes: Any) -> "Form":
        values = dict(segs=self.segs, gaps=self.gaps, brackets=self.brackets, syllables=self.syllables,
                      tiers=self.tiers, pending_syllable_marks=self.pending_syllable_marks)
        values.update(changes)
        return Form(**values)

    def with_marks(self, g: int, marks: Iterable[Mark]) -> "Form":
        """A copy whose *stored* marks at gap ``g`` are ``marks`` (spec §5.2)."""
        if not 0 <= g <= self.n:
            raise IndexError("gap %d outside 0..%d" % (g, self.n))
        gaps = list(self.gaps)
        gaps[g] = frozenset(marks)
        return self._evolve(gaps=tuple(gaps))

    def with_syllables(self, syllables: Any, pending_syllable_marks: Iterable[Tuple[int, Segment]] = ()) -> "Form":
        """A copy with a new syllable tier (spec §5.4, §5.7) and pending syllable marks
        (by default none: syllabification consumes them; plan P7)."""
        return self._evolve(syllables=syllables, pending_syllable_marks=tuple(pending_syllable_marks))

    def with_brackets(self, brackets: Iterable[Bracket]) -> "Form":
        """A copy with a new bracket set (spec §5.3); validated and normalised."""
        return self._evolve(brackets=tuple(brackets))

    def erase_brackets(self, predicate: Callable[[Bracket], bool]) -> "Form":
        """A copy without the brackets for which ``predicate`` is true (spec §8.6 ``/:C*``
        step 3). The remaining brackets keep their order."""
        return self._evolve(brackets=tuple(b for b in self.brackets if not predicate(b)))

    def segments_equal(self, other: "Form") -> bool:
        """True if both forms have the same segment sequence, whatever their marks,
        brackets and tiers (spec §5.1)."""
        return self.segs == other.segs

    def replace(self, i: int, j: int, new_segments: Sequence[Segment], *, attach: str = ATTACH_LEFT,
                align: Optional[Sequence[Optional[int]]] = None) -> "Form":
        """Replace ``segs[i:j]`` by ``new_segments`` (spec §8.2.4; design §4.3).

        See :meth:`replace_with_map`, which also returns the index map.
        """
        return self.replace_with_map(i, j, new_segments, attach=attach, align=align)[0]

    def replace_with_map(self, i: int, j: int, new_segments: Sequence[Segment], *, attach: str = ATTACH_LEFT,
                         align: Optional[Sequence[Optional[int]]] = None) -> Tuple["Form", IndexMap]:
        """Replace ``segs[i:j]`` by ``new_segments``; return ``(form, index_map)``
        (spec §8.2.4 "Boundaries", design §4.3).

        With ``k = len(new_segments)``:

        * **Marks.** If ``k == 0`` the marks of gaps ``i..j`` are unioned into gap ``i``.
          Otherwise gap ``i`` keeps its marks plus those of the interior gaps ``i+1..j-1``
          (interior marks move to the left edge), gap ``j``'s marks go to the new right edge
          ``i+k``, and the new interior gaps are empty. For a pure insertion (``i == j``),
          ``attach`` decides where gap ``i``'s marks go: :data:`ATTACH_LEFT` (default: the new
          material joins the left neighbour, the marks follow it) or :data:`ATTACH_RIGHT`.
        * **Brackets** are mapped the same way, end by end: gaps before ``i`` are unchanged,
          gaps after ``j`` shift by ``k-(j-i)``, interior gaps go to ``i``, gap ``j`` goes to
          ``i+k``. A bracket is never dropped; one that ends up empty is kept as a zero-width
          span (P3 decision 3). Pending syllable marks move like marks.
        * **Index map.** ``index_map[p]`` for each old segment ``p``: unchanged before ``i``,
          shifted after ``j``, and inside the span positional (``m_q -> r_q`` for
          ``q < k``, deleted otherwise), unless ``align`` gives, for each replaced segment,
          its offset in ``new_segments`` or ``None``.
        * **Tiers.** ``syllables.after_replace(i, j, k, index_map)`` (plan P7) and
          ``tier.after_replace(i, j, k, index_map)`` for each tier (plan P8) are called when
          present and return the updated tier objects.
        """
        n = self.n
        if not 0 <= i <= j <= n:
            raise IndexError("replace span %d..%d outside 0..%d" % (i, j, n))
        if attach not in (ATTACH_LEFT, ATTACH_RIGHT):
            raise ValueError("attach must be %r or %r" % (ATTACH_LEFT, ATTACH_RIGHT))
        new = tuple(new_segments)
        k = len(new)
        delta = k - (j - i)
        segs = self.segs[:i] + new + self.segs[j:]

        # Where an old gap goes. Before i: same; after j: shifted; interior: left edge.
        right_edge = i + k
        insertion = i == j
        if insertion:
            edge_target = right_edge if attach == ATTACH_LEFT else i

        def map_gap(g: int) -> int:
            if g < i:
                return g
            if g > j:
                return g + delta
            if insertion:
                return edge_target
            if g == j:
                return right_edge
            return i  # g == i or interior

        gaps: List[FrozenSet[Mark]] = [_EMPTY] * (len(segs) + 1)
        for g, marks in enumerate(self.gaps):
            if marks:
                t = map_gap(g)
                gaps[t] = gaps[t] | marks

        brackets = tuple(Bracket(b.label, map_gap(b.open_gap), map_gap(b.close_gap)) for b in self.brackets)
        pending = tuple((map_gap(g), s) for g, s in self.pending_syllable_marks)

        index_map = self._index_map(i, j, k, delta, align)

        syllables = self.syllables
        if syllables is not None:
            # The P7 syllable tier also gets the new segments and stored gaps: the upkeep
            # needs word boundaries and the templates need segments (design §12, P7). Other
            # hook objects keep the four-argument P3 signature.
            if getattr(syllables, "takes_frame", False):
                syllables = syllables.after_replace(i, j, k, index_map, segs=segs, gaps=gaps)
            else:
                syllables = syllables.after_replace(i, j, k, index_map)
        tiers = tuple(t.after_replace(i, j, k, index_map) for t in self.tiers)
        form = Form(segs, tuple(gaps), brackets, syllables, tiers, pending)
        return form, index_map

    def _index_map(self, i: int, j: int, k: int, delta: int, align: Optional[Sequence[Optional[int]]]) -> IndexMap:
        n = self.n
        if align is not None:
            align = tuple(align)
            if len(align) != j - i:
                raise ValueError("align needs %d entries (one per replaced segment), got %d" % (j - i, len(align)))
            for a in align:
                if a is not None and not 0 <= a < k:
                    raise ValueError("align offset %r outside 0..%d" % (a, k - 1))
        out: List[Optional[int]] = []
        for p in range(n):
            if p < i:
                out.append(p)
            elif p >= j:
                out.append(p + delta)
            else:
                q = p - i
                if align is not None:
                    out.append(None if align[q] is None else i + align[q])
                else:
                    out.append(i + q if q < k else None)
        return tuple(out)

    # -- printing ------------------------------------------------------------------------

    def __repr__(self) -> str:
        parts: List[str] = []
        events = bracket_events(self.brackets, self.n)
        pend: Dict[int, List[Segment]] = {}
        for g, s in self.pending_syllable_marks:
            pend.setdefault(g, []).append(s)
        for g in range(self.n + 1):
            for kind, b in events[g]:
                parts.append("<%s:" % b.label if kind == "open" else ">")
            for m in sorted(self.gaps[g], key=lambda m: m.value):
                parts.append(m.value)
            for s in pend.get(g, ()):
                parts.append("'%r" % (s,))
            if g < self.n:
                parts.append(repr(self.segs[g]))
        return "Form(%s)" % " ".join(parts)
