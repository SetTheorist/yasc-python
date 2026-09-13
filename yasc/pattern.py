"""Pattern AST (spec §6.1, §6.4, §7; design §5.1).

Patterns are regular expressions over forms. The parser (plan P4) builds these nodes, the
compiler expands :class:`Macro` nodes and wraps the LHS items with :func:`number_lhs`, and
:func:`yasc.nfa.compile_pattern` turns the result into an NFA.

Every node is immutable (a frozen dataclass), carries an optional ``loc``
(:class:`~yasc.errors.SourceLoc`, ignored by equality) and has :meth:`Pattern.canonical`,
which returns parseable text in spec syntax (spec §11.3).

Helpers:

* :func:`split_at_locus` — ``C ___ D`` → ``(C, D)`` for contexts (spec §8.1, §8.2);
* :func:`number_lhs` — wrap the top-level LHS items in ``Capture(1..k)`` (spec §6.4);
* :func:`first_specs` — the specs that can consume the first segment (design §6 pre-filter);
* :func:`walk` — every node of a pattern, pre-order.
"""

from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from .errors import SourceLoc, YascDefinitionError
from .marks import Mark
from .segment import SegmentSpec

__all__ = [
    "Pattern",
    "Spec",
    "Ortho",
    "Macro",
    "Nothing",
    "Boundary",
    "BracketAssert",
    "Seq",
    "Alt",
    "Opt",
    "Star",
    "Plus",
    "Anything",
    "Locus",
    "BackRef",
    "Capture",
    "AutoFloat",
    "Linked",
    "OPEN",
    "CLOSE",
    "ANY_LABEL",
    "walk",
    "split_at_locus",
    "number_lhs",
    "first_specs",
    "nullable",
]

#: ``BracketAssert.kind`` values (spec §5.3).
OPEN = "open"
CLOSE = "close"
#: The label that matches any category (``<:*``, ``>:*``).
ANY_LABEL = "*"


def _loc():
    return field(default=None, compare=False, repr=False)


class Pattern:
    """Base class of every pattern node (spec §6.1; design §5.1)."""

    __slots__ = ()

    def canonical(self) -> str:  # pragma: no cover - overridden
        """Parseable text of the pattern in spec syntax (spec §11.3)."""
        raise NotImplementedError

    def children(self) -> Tuple["Pattern", ...]:
        """The direct sub-patterns, in textual order."""
        return ()

    def __str__(self) -> str:
        return self.canonical()


# --------------------------------------------------------------------------------------------
# Segment-consuming leaves
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Spec(Pattern):
    """``{...}``, ``'{...}`` or a combined ``S1:S2`` — one segment (spec §6.1, §6.2).

    Equality compares the canonical text of the spec (and the feature system), so a parsed
    ``canonical()`` round-trips to an equal node (spec §11.3).
    """

    spec: SegmentSpec
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``{+Syll -Voice}`` or ``'{...}`` (spec §6.2)."""
        return self.spec.canonical()

    def _key(self):
        return (id(self.spec.system), self.spec.canonical())

    def __eq__(self, other):
        if not isinstance(other, Spec):
            return NotImplemented
        return self.spec is other.spec or self._key() == other._key()

    def __hash__(self):
        return hash(self._key())


@dataclass(frozen=True, eq=False)
class Ortho(Pattern):
    """``[abc]`` — the segments the string parses to, each acting as a spec (spec §6.1).

    ``specs`` holds one :class:`SegmentSpec` per parsed segment (built with
    :meth:`SegmentSpec.from_segment`; strict for ``'[abc]``). ``text`` is the source string
    between the brackets; when it is missing, :meth:`canonical` prints the specs instead.
    """

    specs: Tuple[SegmentSpec, ...]
    text: Optional[str] = None
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "specs", tuple(self.specs))
        if not self.specs:
            raise YascDefinitionError("an orthographic string must parse to at least one segment", self.loc)

    def canonical(self) -> str:
        """``[abc]`` (``'[abc]`` when strict), or the specs in sequence without ``text``."""
        if self.text is not None:
            strict = all(s.strict for s in self.specs)
            return ("'" if strict else "") + "[" + self.text + "]"
        return " ".join(s.canonical() for s in self.specs)

    def _key(self):
        return (tuple(id(s.system) for s in self.specs), tuple(s.canonical() for s in self.specs))

    def __eq__(self, other):
        if not isinstance(other, Ortho):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self):
        return hash(self._key())


@dataclass(frozen=True)
class Macro(Pattern):
    """``Name`` or ``Name:refine`` — a macro reference, expanded by the compiler (spec §7).

    Compiling an unexpanded macro into an NFA is an error.
    """

    name: str
    refine: Optional[Pattern] = None
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Name`` or ``Name:{...}`` (spec §7)."""
        if self.refine is None:
            return self.name
        return self.name + ":" + self.refine.canonical()

    def children(self):
        return () if self.refine is None else (self.refine,)


@dataclass(frozen=True)
class BackRef(Pattern):
    """``$n`` — an exact copy of the span captured by LHS item ``n`` (spec §6.4)."""

    n: int
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        if isinstance(self.n, bool) or not isinstance(self.n, int) or self.n < 0:
            raise YascDefinitionError("invalid back-reference $%r" % (self.n,), self.loc)

    def canonical(self) -> str:
        """``$n`` (spec §6.4)."""
        return "$%d" % self.n


@dataclass(frozen=True)
class Anything(Pattern):
    """``...`` — any run of segments, ``{}*`` (spec §6.1)."""

    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``...`` (spec §6.1)."""
        return "..."


# --------------------------------------------------------------------------------------------
# Zero-width elements
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Nothing(Pattern):
    """``0`` — the empty string (spec §6.1)."""

    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``0`` (spec §6.1)."""
        return "0"


@dataclass(frozen=True)
class Boundary(Pattern):
    """``.`` ``-`` ``=`` ``#`` ``##`` — a gap carrying the mark or a stronger one (spec §5.2)."""

    mark: Mark
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        if not isinstance(self.mark, Mark):
            raise TypeError("Boundary takes a Mark, got %r" % (self.mark,))

    def canonical(self) -> str:
        """The mark's pattern symbol (spec §5.2)."""
        return self.mark.value


@dataclass(frozen=True)
class BracketAssert(Pattern):
    """``<:N`` / ``>:N`` — the opening / closing of an N bracket; label ``*`` = any (spec §5.3)."""

    kind: str
    label: str = ANY_LABEL
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        if self.kind not in (OPEN, CLOSE):
            raise ValueError("BracketAssert kind must be %r or %r, got %r" % (OPEN, CLOSE, self.kind))
        if not self.label:
            raise YascDefinitionError("a bracket assertion needs a label or *", self.loc)

    def canonical(self) -> str:
        """``<:N``, ``>:N``, ``<:*`` (spec §6.1)."""
        return ("<:" if self.kind == OPEN else ">:") + self.label


@dataclass(frozen=True)
class Locus(Pattern):
    """``___`` — the focus position; only in contexts (spec §6.1, §8.1)."""

    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``___`` (spec §6.1)."""
        return "___"


# --------------------------------------------------------------------------------------------
# Composites
# --------------------------------------------------------------------------------------------


def _atomic(p: Pattern) -> bool:
    """True if ``p`` can take a postfix operator without parentheses."""
    if isinstance(p, Capture):
        return _atomic(p.pattern)
    if isinstance(p, Ortho):
        return p.text is not None or len(p.specs) == 1
    if isinstance(p, Macro):
        return p.refine is None
    return isinstance(p, (Spec, Alt, BackRef))


def _item_text(p: Pattern) -> str:
    """Text of ``p`` as one item of a sequence; nested sequences are written flat."""
    if isinstance(p, Capture):
        return _item_text(p.pattern)
    if isinstance(p, Seq):
        return " ".join(_item_text(x) for x in p.items) if p.items else "0"
    return p.canonical()


@dataclass(frozen=True)
class Seq(Pattern):
    """``P P ...`` — a sequence; the empty sequence is ``0`` (spec §6.1)."""

    items: Tuple[Pattern, ...]
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(self.items))

    def canonical(self) -> str:
        """Items separated by spaces; nested sequences are flattened textually (spec §6.1)."""
        return _item_text(self)

    def children(self):
        return self.items


@dataclass(frozen=True)
class Alt(Pattern):
    """``<< P | P | ... >>`` — ordered disjunction; the matched index is recorded in the Env
    under ``id`` (spec §6.1, §8.2.4). ``id`` is assigned by the compiler (an int); with
    ``id=None`` nothing is recorded."""

    id: Optional[int]
    items: Tuple[Pattern, ...]
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(self.items))
        if self.id is not None and (isinstance(self.id, bool) or not isinstance(self.id, int)):
            raise TypeError("Alt ids are ints (Env keys must be mutually sortable), got %r" % (self.id,))

    def canonical(self) -> str:
        """``<< a | b >>`` (spec §6.1)."""
        return "<< " + " | ".join(_item_text(x) for x in self.items) + " >>"

    def children(self):
        return self.items


@dataclass(frozen=True)
class Opt(Pattern):
    """``( P )`` — zero or one, greedy (spec §6.1)."""

    pattern: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``(P)`` (spec §6.1)."""
        return "(" + _item_text(self.pattern) + ")"

    def children(self):
        return (self.pattern,)


@dataclass(frozen=True)
class Star(Pattern):
    """``P*`` / ``( P )*`` — zero or more, greedy (spec §6.1)."""

    pattern: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``P*`` for atoms, else ``(P)*`` (spec §6.1)."""
        p = self.pattern
        return (_item_text(p) if _atomic(p) else "(" + _item_text(p) + ")") + "*"

    def children(self):
        return (self.pattern,)


@dataclass(frozen=True)
class Plus(Pattern):
    """``P+`` / ``( P )+`` — one or more, greedy (spec §6.1)."""

    pattern: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``P+`` for atoms, else ``(P)+`` (spec §6.1)."""
        p = self.pattern
        return (_item_text(p) if _atomic(p) else "(" + _item_text(p) + ")") + "+"

    def children(self):
        return (self.pattern,)


@dataclass(frozen=True)
class Capture(Pattern):
    """Capture group ``n`` around ``pattern``: records the matched span ``(i, j)`` in the
    Env (spec §6.4). Inserted by :func:`number_lhs`; it has no surface syntax, so
    :meth:`canonical` prints the inner pattern."""

    n: int
    pattern: Pattern
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        if isinstance(self.n, bool) or not isinstance(self.n, int) or self.n < 0:
            raise ValueError("capture numbers are ints >= 0, got %r" % (self.n,))

    def canonical(self) -> str:
        """The inner pattern's text (captures are implicit, spec §6.4)."""
        return self.pattern.canonical()

    def children(self):
        return (self.pattern,)


# --------------------------------------------------------------------------------------------
# Autosegmental placeholders (plan P8)
# --------------------------------------------------------------------------------------------


def _tier_elem(tier: Optional[str], x: str, exact: bool, name: Optional[str]) -> str:
    return "^" + (tier + "." if tier else "") + x + ("'" if exact else "") + ("=" + name if name else "")


@dataclass(frozen=True)
class AutoFloat(Pattern):
    """``^[Tier.]X[=name]`` — a floating autosegment at this gap (spec §6.5). Placeholder:
    compiling it raises ``NotImplementedYet(phase="P8")``."""

    tier: Optional[str]
    x: str
    name: Optional[str] = None
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``^[H]``, ``^Tone.(a)=h`` (spec §6.5)."""
        return _tier_elem(self.tier, self.x, False, self.name)


@dataclass(frozen=True)
class Linked(Pattern):
    """``S^X``, ``S^X'`` (exactly one link), ``S^X=name`` — a segment linked to an
    autosegment (spec §6.5). Placeholder: compiling it raises ``NotImplementedYet(phase="P8")``."""

    spec: Pattern
    tier: Optional[str]
    x: str
    exact: bool = False
    name: Optional[str] = None
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``V^[H]``, ``V^*=h``, ``V^[H]'`` (spec §6.5)."""
        return self.spec.canonical() + _tier_elem(self.tier, self.x, self.exact, self.name)

    def children(self):
        return (self.spec,)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def walk(pattern: Pattern) -> Iterator[Pattern]:
    """Every node of ``pattern`` in pre-order (design §5.1)."""
    stack = [pattern]
    while stack:
        p = stack.pop()
        yield p
        stack.extend(reversed(p.children()))


def _items(pattern: Pattern) -> Tuple[Pattern, ...]:
    return pattern.items if isinstance(pattern, Seq) else (pattern,)


def _rebuild(items: List[Pattern], loc: Optional[SourceLoc]) -> Pattern:
    if not items:
        return Nothing(loc)
    if len(items) == 1:
        return items[0]
    return Seq(tuple(items), loc)


def split_at_locus(pattern: Pattern) -> Tuple[Pattern, Pattern]:
    """Split a context ``C ___ D`` into ``(C, D)`` (spec §8.1, §8.2).

    The locus must appear exactly once, as a top-level item. A missing side is
    :class:`Nothing`; a side with one item is that item; otherwise a :class:`Seq`. ``C`` is
    returned in textual (forward) order: :func:`yasc.nfa.compile_pattern` and
    :meth:`NFA.reversed` make it match backward.
    """
    loci = [p for p in walk(pattern) if isinstance(p, Locus)]
    loc = getattr(pattern, "loc", None)
    if len(loci) != 1:
        where = loci[1].loc if len(loci) > 1 else loc
        raise YascDefinitionError(
            "a context must contain the locus ___ exactly once (found %d)" % len(loci), where,
            hint="write the focus position as ___, e.g. / V ___ #")
    items = _items(pattern)
    idx = [k for k, p in enumerate(items) if isinstance(p, Locus)]
    if not idx:
        raise YascDefinitionError("the locus ___ must be a top-level item of the context, not nested inside "
                                  "a group, disjunction or repetition", loci[0].loc)
    k = idx[0]
    return _rebuild(list(items[:k]), loc), _rebuild(list(items[k + 1:]), loc)


_UNNUMBERED = (Boundary, BracketAssert, Nothing)


def number_lhs(pattern: Pattern, whole: bool = True) -> Pattern:
    """Wrap the top-level LHS items in ``Capture(1..k)`` left to right (spec §6.4).

    Items are every top-level element except the zero-width ones (:class:`Boundary`,
    :class:`BracketAssert`, :class:`Nothing`), which match no segments. With ``whole``, the
    result is also wrapped in ``Capture(0, ...)`` so that ``$0`` (the whole LHS match)
    works in contexts.

    Checks: a :class:`Locus` is illegal in an LHS; ``$0`` is illegal inside the LHS; ``$n``
    must refer to an item that exists and lies strictly before the item containing it (the
    LHS is matched left to right, so a reference to the same or a later item could never
    match).
    """
    for p in walk(pattern):
        if isinstance(p, Locus):
            raise YascDefinitionError("the locus ___ is only allowed in contexts, not in a rule's LHS", p.loc)
    items = _items(pattern)
    out: List[Pattern] = []
    k = 0
    for item in items:
        if isinstance(item, _UNNUMBERED):
            out.append(item)
            continue
        k += 1
        for p in walk(item):
            if isinstance(p, BackRef):
                if p.n == 0:
                    raise YascDefinitionError("$0 (the whole LHS match) cannot be used inside the LHS", p.loc)
                if p.n >= k:
                    raise YascDefinitionError(
                        "$%d in LHS item %d must refer to an earlier item" % (p.n, k), p.loc,
                        hint="$n numbers the top-level LHS items from left to right (spec §6.4)")
        out.append(Capture(k, item, getattr(item, "loc", None)))
    loc = getattr(pattern, "loc", None)
    body = Seq(tuple(out), loc) if (isinstance(pattern, Seq) or len(out) != 1) else out[0]
    return Capture(0, body, loc) if whole else body


# first_specs / nullable ------------------------------------------------------------------------


def _first(p: Pattern):
    """Return ``(specs, nullable)``; ``specs`` is a list of specs or ``None`` (unknown)."""
    if isinstance(p, Spec):
        return [p.spec], False
    if isinstance(p, Ortho):
        return [p.specs[0]], False
    if isinstance(p, (Nothing, Boundary, BracketAssert, Locus)):
        return [], True
    if isinstance(p, Capture):
        return _first(p.pattern)
    if isinstance(p, Seq):
        acc: List[SegmentSpec] = []
        for item in p.items:
            specs, null = _first(item)
            if specs is None:
                return None, _nullable(p)
            acc.extend(specs)
            if not null:
                return acc, False
        return acc, True
    if isinstance(p, Alt):
        acc = []
        null = False
        unknown = False
        for item in p.items:
            specs, n = _first(item)
            if specs is None:
                unknown = True
            else:
                acc.extend(specs)
            null = null or n
        return (None if unknown else acc), null
    if isinstance(p, (Opt, Star)):
        specs, _ = _first(p.pattern)
        return specs, True
    if isinstance(p, Plus):
        return _first(p.pattern)
    # Anything, BackRef, Macro, AutoFloat, Linked: cannot pre-filter.
    return None, _nullable(p)


def nullable(pattern: Pattern) -> bool:
    """True if ``pattern`` can match the empty span, ignoring whether assertions hold
    (spec §6.1; used by the design §6 pre-filter)."""
    return _nullable(pattern)


def _nullable(p: Pattern) -> bool:
    if isinstance(p, (Spec, Ortho, Linked)):
        return False
    if isinstance(p, (Nothing, Boundary, BracketAssert, Locus, Anything, Opt, Star, AutoFloat)):
        return True
    if isinstance(p, BackRef):
        return True  # the captured span may be empty
    if isinstance(p, (Capture, Plus)):
        return _nullable(p.pattern)
    if isinstance(p, Seq):
        return all(_nullable(x) for x in p.items)
    if isinstance(p, Alt):
        return any(_nullable(x) for x in p.items)
    return True  # Macro: unknown until expanded


def first_specs(pattern: Pattern) -> Optional[Tuple[SegmentSpec, ...]]:
    """The specs that can consume the first segment of a match (design §6 pre-filter).

    Returns a tuple in textual order without duplicates (by identity), or ``None`` when the
    pattern can match the empty span or can start with an element that is not a spec
    (``...``, ``$n``, an unexpanded macro, P8 elements). Zero-width assertions before the
    first segment are transparent: they only add conditions, so the pre-filter stays sound.
    """
    specs, null = _first(pattern)
    if specs is None or null:
        return None
    out: List[SegmentSpec] = []
    seen = set()
    for s in specs:
        if id(s) not in seen:
            seen.add(id(s))
            out.append(s)
    return tuple(out)
