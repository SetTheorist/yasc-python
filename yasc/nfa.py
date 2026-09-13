"""NFA graph, Thompson construction with ordered edges, reversal and ε-closures (design §5.2).

:func:`compile_pattern` turns a pattern AST (:mod:`yasc.pattern`) into an :class:`NFA`.
States are integers; ``nfa.edges[u]`` lists the out-edges of ``u`` as :class:`Edge`
``(kind, payload, target)`` tuples **in preference order**:

* a Star or Plus loop edge comes before its exit edge (greedy);
* an Optional's "take" edge comes before its "skip" edge (greedy);
* the alternatives of a disjunction keep their textual order.

Edge kinds (spec §6.1, §6.3, §6.4):

======== =============================== ==============================================
kind     payload                         meaning
======== =============================== ==============================================
EPS      ``None``                        free move
SEG      :class:`SegmentSpec` or ``None`` consume one segment (``None`` = any segment)
ASSERT   :class:`BoundaryPred` /         zero-width test of the current gap
         :class:`BracketPred`
CAP_OPEN capture number ``n``            start of capture ``n`` (records the gap)
CAP_CLOSE capture number ``n``           end of capture ``n`` (completes the span)
ALT      ``(id, index)``                 records the disjunction alternative in the Env
BACKREF  capture number ``n``            consume an exact copy of capture ``n``
FLOAT    :class:`~yasc.tiers.FloatPred`  zero-width: a floating autosegment at the gap
                                         (``^X``, spec §6.5); may bind several ways
======== =============================== ==============================================

``S^X`` (spec §6.5) compiles to a SEG edge whose payload is a :class:`~yasc.tiers.LinkSpec`
(design §11 ``LINK``): it is matched like a spec that reads tier features.

:meth:`NFA.reversed` gives the NFA for backward matching (contexts left of the locus):
every edge is reversed, start and accept are swapped, and capture open/close are swapped.
The preference order is kept: loops stay greedy and alternatives stay in textual order.

``nfa.closure[u]`` is the precomputed ε-closure of state ``u``: an ordered list of
``(target, ops)`` where ``target`` is a state with consuming edges (SEG, BACKREF) or the
accept state, and ``ops`` is the tuple of non-consuming actions (ASSERT, CAP_*, ALT) along
the path. The matcher evaluates ``ops`` at run time because assertions depend on the gap.
"""

from collections import namedtuple
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .errors import NotImplementedYet, YascDefinitionError
from .marks import Mark, gap_satisfies
from .pattern import (
    ANY_LABEL,
    OPEN,
    Alt,
    Anything,
    AutoFloat,
    BackRef,
    Boundary,
    BracketAssert,
    Capture,
    Linked,
    Locus,
    Macro,
    Nothing,
    Opt,
    Ortho,
    Pattern,
    Plus,
    Seq,
    Spec,
    Star,
    first_specs,
)

__all__ = [
    "EPS",
    "SEG",
    "ASSERT",
    "CAP_OPEN",
    "CAP_CLOSE",
    "ALT",
    "BACKREF",
    "FLOAT",
    "KIND_NAMES",
    "Edge",
    "BoundaryPred",
    "BracketPred",
    "NFA",
    "compile_pattern",
]

# Edge kinds (small ints: compared on the matcher's hot path).
EPS = 0
SEG = 1
ASSERT = 2
CAP_OPEN = 3
CAP_CLOSE = 4
ALT = 5
BACKREF = 6
FLOAT = 7

KIND_NAMES = {EPS: "EPS", SEG: "SEG", ASSERT: "ASSERT", CAP_OPEN: "CAP_OPEN", CAP_CLOSE: "CAP_CLOSE",
              ALT: "ALT", BACKREF: "BACKREF", FLOAT: "FLOAT"}

_CONSUMING = (SEG, BACKREF)
_SWAP = {CAP_OPEN: CAP_CLOSE, CAP_CLOSE: CAP_OPEN}

#: One out-edge of a state (design §5.2).
Edge = namedtuple("Edge", "kind payload target")
Edge.__doc__ = "One NFA edge ``(kind, payload, target)``; see the module table (design §5.2)."


# --------------------------------------------------------------------------------------------
# Assertion predicates
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryPred:
    """Zero-width test: the gap carries ``mark`` or a mark that implies it (spec §5.2).

    ``holds(form, lo, hi)`` tests the *virtual* gap made of the real gaps ``lo..hi``
    (inclusive; ``lo == hi`` without invisible segments). Its marks are the union of those
    gaps' marks (spec §8.5), so it holds iff any of them satisfies the mark.
    """

    mark: Mark

    def holds(self, form, lo: int, hi: Optional[int] = None) -> bool:
        """True iff the (virtual) gap ``lo..hi`` satisfies the boundary (spec §5.2, §8.5)."""
        if hi is None or hi == lo:
            return gap_satisfies(form.gap_marks(lo), self.mark)
        for g in range(lo, hi + 1):
            if gap_satisfies(form.gap_marks(g), self.mark):
                return True
        return False

    def canonical(self) -> str:
        """The pattern symbol (spec §5.2)."""
        return self.mark.value


@dataclass(frozen=True)
class BracketPred:
    """Zero-width test: an ``label`` bracket opens (``kind='open'``) or closes
    (``kind='close'``) at the gap; label ``*`` matches any category (spec §5.3).

    ``holds(form, lo, hi)`` tests the virtual gap ``lo..hi``, like :class:`BoundaryPred`.
    """

    kind: str
    label: str

    def holds(self, form, lo: int, hi: Optional[int] = None) -> bool:
        """True iff a matching bracket edge lies in gaps ``lo..hi`` (spec §5.3, §8.5)."""
        if hi is None:
            hi = lo
        opening = self.kind == OPEN
        anylabel = self.label == ANY_LABEL
        for b in form.brackets:
            g = b.open_gap if opening else b.close_gap
            if lo <= g <= hi and (anylabel or b.label == self.label):
                return True
        return False

    def canonical(self) -> str:
        """``<:N`` or ``>:N`` (spec §6.1)."""
        return ("<:" if self.kind == OPEN else ">:") + self.label


# --------------------------------------------------------------------------------------------
# NFA
# --------------------------------------------------------------------------------------------


class NFA:
    """A compiled pattern (design §5.2).

    Attributes:

    * ``start``, ``accept`` — state numbers;
    * ``edges[u]`` — out-edges of ``u`` in preference order (:class:`Edge`);
    * ``closure[u]`` — precomputed ε-closure, ``[(target, ops), ...]`` in preference order;
    * ``is_reversed`` — True for the backward NFA returned by :meth:`reversed`;
    * ``first_specs`` — the pre-filter specs of the forward pattern, or ``None``
      (:func:`yasc.pattern.first_specs`); ``None`` on a reversed NFA;
    * ``pattern`` — the source pattern, if known;
    * ``match_cache`` — the per-matcher cache of variable-free spec results, shared with
      the reversed NFA (design §6 "Caching"). :meth:`clear_cache` empties it.

    Build one with :func:`compile_pattern`.
    """

    __slots__ = ("start", "accept", "edges", "_inn", "closure", "is_reversed", "first_specs", "pattern",
                 "match_cache", "_rev", "_cons", "_bref")

    def __init__(self, start: int, accept: int, edges: List[List[Edge]], inn: List[List[Edge]],
                 is_reversed: bool = False, first_specs=None, pattern: Optional[Pattern] = None,
                 match_cache: Optional[Dict] = None) -> None:
        self.start = start
        self.accept = accept
        self.edges = [tuple(es) for es in edges]
        self._inn = [tuple(es) for es in inn]
        self.is_reversed = is_reversed
        self.first_specs = first_specs
        self.pattern = pattern
        self.match_cache = {} if match_cache is None else match_cache
        self._rev: Optional["NFA"] = None
        self._cons = None     # prepared consuming edges (matcher); built lazily
        self._bref = None     # per-state BACKREF edges, for zero-length back-references
        self.closure = [self._compute_closure(u) for u in range(len(self.edges))]

    @property
    def n_states(self) -> int:
        """Number of states."""
        return len(self.edges)

    # -- ε-closure --------------------------------------------------------------------------

    def _compute_closure(self, s: int) -> Tuple[Tuple[int, tuple], ...]:
        """Ordered ε-closure of ``s`` (design §5.2).

        Depth-first in edge order. Each result is ``(target, ops)`` with ``ops`` an immutable
        tuple built per path (R2: nothing is shared between sibling branches). ε-cycles such
        as ``(P*)*`` are cut by never re-entering a state already on the current path, and a
        state is expanded at most once per distinct ``ops`` (so each state is visited once
        per closure when no ops are involved). A path that re-enters the cycle would only
        repeat zero-width actions, so nothing is lost.
        """
        edges = self.edges
        accept = self.accept
        out: List[Tuple[int, tuple]] = []
        emitted = set()
        expanded = set()

        def visit(u: int, ops: tuple, path: frozenset) -> None:
            key = (u, ops)
            if key in expanded:
                return
            expanded.add(key)
            es = edges[u]
            if u == accept or any(e[0] in _CONSUMING for e in es):
                if key not in emitted:
                    emitted.add(key)
                    out.append(key)
            for kind, payload, v in es:
                if kind in _CONSUMING or v in path:
                    continue
                if kind == EPS:
                    nops = ops
                elif kind == ALT:
                    nops = ops + ((ALT, payload),)
                else:
                    nops = ops + ((kind, payload),)
                visit(v, nops, path | {v})

        visit(s, (), frozenset((s,)))
        return tuple(out)

    # -- reversal ---------------------------------------------------------------------------

    def reversed(self) -> "NFA":
        """The NFA for backward matching (design §5.2), built once and cached.

        Every edge is reversed, ``start`` and ``accept`` are swapped and ``CAP_OPEN`` /
        ``CAP_CLOSE`` are swapped, so a capture is still opened first and closed second in
        matching order. ASSERT predicates are unchanged (gaps are symmetric). The reversed
        out-edges of a state are its original in-edges, kept in an order that preserves
        greedy loops and textual alternative order. ``nfa.reversed().reversed() is nfa``.
        """
        if self._rev is None:
            def flip(es):
                return [Edge(_SWAP.get(e.kind, e.kind), e.payload, e.target) for e in es]
            rev = NFA(self.accept, self.start, [flip(es) for es in self._inn], [flip(es) for es in self.edges],
                      not self.is_reversed, None, self.pattern, self.match_cache)
            rev._rev = self
            self._rev = rev
        return self._rev

    def clear_cache(self) -> None:
        """Empty the spec-match cache (shared with the reversed NFA) (design §6)."""
        for table in self.match_cache.values():
            table.clear()

    def dump(self) -> str:
        """A readable listing of states and edges (debugging aid)."""
        lines = ["start=%d accept=%d%s" % (self.start, self.accept, " (reversed)" if self.is_reversed else "")]
        for u, es in enumerate(self.edges):
            for e in es:
                p = e.payload
                if hasattr(p, "canonical"):
                    p = p.canonical()
                lines.append("  %d -%s%s-> %d" % (u, KIND_NAMES[e.kind], "" if p is None else "(%s)" % (p,), e.target))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return "<NFA %d states%s>" % (len(self.edges), " reversed" if self.is_reversed else "")


# --------------------------------------------------------------------------------------------
# Thompson construction
# --------------------------------------------------------------------------------------------


class _Builder:
    """Accumulates states and edges; ``inn`` keeps in-edges in reversed-preference order."""

    __slots__ = ("edges", "inn")

    def __init__(self) -> None:
        self.edges: List[List[Edge]] = []
        self.inn: List[List[Edge]] = []

    def state(self) -> int:
        self.edges.append([])
        self.inn.append([])
        return len(self.edges) - 1

    def add(self, u: int, kind: int, payload, v: int, rev_first: bool = False) -> None:
        """Add ``u -kind-> v``. ``rev_first`` puts the edge first among ``v``'s in-edges, so
        it is preferred when the NFA is reversed (loop back-edges)."""
        self.edges[u].append(Edge(kind, payload, v))
        e = Edge(kind, payload, u)
        if rev_first:
            self.inn[v].insert(0, e)
        else:
            self.inn[v].append(e)

    def build(self, p: Pattern, s: int) -> int:
        """Build ``p`` starting at state ``s``; return its end state (design §5.2)."""
        if isinstance(p, Spec):
            e = self.state()
            self.add(s, SEG, p.spec, e)
            return e
        if isinstance(p, Ortho):
            for spec in p.specs:
                e = self.state()
                self.add(s, SEG, spec, e)
                s = e
            return s
        if isinstance(p, Nothing):
            return s
        if isinstance(p, Boundary):
            e = self.state()
            self.add(s, ASSERT, BoundaryPred(p.mark), e)
            return e
        if isinstance(p, BracketAssert):
            e = self.state()
            self.add(s, ASSERT, BracketPred(p.kind, p.label), e)
            return e
        if isinstance(p, Seq):
            for item in p.items:
                s = self.build(item, s)
            return s
        if isinstance(p, Alt):
            # R13: one fork state with exactly one edge per alternative.
            ends = []
            for k, item in enumerate(p.items):
                a = self.state()
                if p.id is None:
                    self.add(s, EPS, None, a)
                else:
                    self.add(s, ALT, (p.id, k), a)
                ends.append(self.build(item, a))
            e = self.state()
            for b in ends:
                self.add(b, EPS, None, e)
            return e
        if isinstance(p, Opt):
            a = self.state()
            self.add(s, EPS, None, a)                 # take (preferred)
            b = self.build(p.pattern, a)
            e = self.state()
            self.add(b, EPS, None, e)
            self.add(s, EPS, None, e)                 # skip
            return e
        if isinstance(p, Star):
            loop = self.state()
            self.add(s, EPS, None, loop)
            a = self.state()
            self.add(loop, EPS, None, a)              # iterate (preferred: greedy)
            b = self.build(p.pattern, a)
            self.add(b, EPS, None, loop, rev_first=True)
            e = self.state()
            self.add(loop, EPS, None, e)              # exit
            return e
        if isinstance(p, Plus):
            a = self.state()
            self.add(s, EPS, None, a)
            b = self.build(p.pattern, a)
            self.add(b, EPS, None, a, rev_first=True)  # loop (preferred: greedy)
            e = self.state()
            self.add(b, EPS, None, e)                 # exit
            return e
        if isinstance(p, Anything):
            # A single state with a self-loop on any segment (design §10).
            loop = self.state()
            self.add(s, EPS, None, loop)
            self.add(loop, SEG, None, loop)
            e = self.state()
            self.add(loop, EPS, None, e)
            return e
        if isinstance(p, BackRef):
            e = self.state()
            self.add(s, BACKREF, p.n, e)
            return e
        if isinstance(p, Capture):
            a = self.state()
            self.add(s, CAP_OPEN, p.n, a)
            b = self.build(p.pattern, a)
            e = self.state()
            self.add(b, CAP_CLOSE, p.n, e)
            return e
        if isinstance(p, Locus):
            raise YascDefinitionError(
                "the locus ___ cannot be compiled into a pattern", p.loc,
                hint="split a context with yasc.pattern.split_at_locus and compile each side")
        if isinstance(p, Macro):
            raise YascDefinitionError("macro %r was not expanded before compiling the pattern" % p.name, p.loc)
        if isinstance(p, AutoFloat):
            from .tiers import FloatPred
            e = self.state()
            self.add(s, FLOAT, FloatPred(p.tier, p.x, p.name), e)
            return e
        if isinstance(p, Linked):
            return self.build(_linked(p), s)
        raise TypeError("not a pattern node: %r" % (p,))


def _linked(p: Linked) -> Pattern:
    """``S^X`` as a Spec node whose spec is a :class:`~yasc.tiers.LinkSpec`; a disjunction
    or a one-segment ``[x]`` inside is distributed over its alternatives (spec §6.5)."""
    from .tiers import LinkSpec
    inner = p.spec
    if isinstance(inner, Capture):
        return Capture(inner.n, _linked(Linked(inner.pattern, p.tier, p.x, p.exact, p.name, p.loc)), inner.loc)
    if isinstance(inner, Spec):
        return Spec(LinkSpec(inner.spec, p.tier, p.x, p.exact, p.name), p.loc)
    if isinstance(inner, Ortho) and len(inner.specs) == 1:
        return Spec(LinkSpec(inner.specs[0], p.tier, p.x, p.exact, p.name), p.loc)
    if isinstance(inner, Alt):
        return Alt(inner.id, tuple(_linked(Linked(x, p.tier, p.x, p.exact, p.name, p.loc)) for x in inner.items),
                   inner.loc)
    raise YascDefinitionError("%s: the segment before ^ must match exactly one segment" % p.canonical(), p.loc,
                              hint="write a spec, a one-segment [x] or a macro for one (spec §6.5)")


def compile_pattern(pattern: Pattern) -> NFA:
    """Compile a pattern into an :class:`NFA` by Thompson construction (design §5.2).

    The pattern must be macro-expanded and must not contain a :class:`~yasc.pattern.Locus`
    (use :func:`~yasc.pattern.split_at_locus` for contexts). Errors:
    :class:`~yasc.errors.YascDefinitionError` for a Macro or a Locus,
    :class:`~yasc.errors.YascDefinitionError` for ``S^X`` whose ``S`` is not one segment.

    The result's ``first_specs`` is :func:`yasc.pattern.first_specs` of the pattern.
    """
    b = _Builder()
    start = b.state()
    end = b.build(pattern, start)
    accept = b.state()
    b.add(end, EPS, None, accept)
    return NFA(start, accept, b.edges, b.inn, False, first_specs(pattern), pattern)
