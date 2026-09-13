"""Boundary marks, category brackets and the minimal form interface (spec §5.1–§5.3).

This module is the shared contract between the matcher (plan P2) and forms/orthography
(plan P3): the matcher only ever talks to a form through :class:`FormLike`, and
``yasc.form.Form`` implements it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Protocol, Tuple

from .segment import Segment, SegView

__all__ = [
    "Mark",
    "SATISFIED_BY",
    "gap_satisfies",
    "mark_from_symbol",
    "Bracket",
    "FormLike",
]


class Mark(enum.Enum):
    """A boundary mark stored in a gap; the value is its pattern symbol (spec §5.2)."""

    SYLLABLE = "."
    MORPHEME = "-"
    CLITIC = "="
    WORD = "#"
    PHRASE = "##"

    def __repr__(self) -> str:
        return f"Mark({self.value!r})"


#: For each mark requested by a pattern, the set of gap marks that satisfy it (spec §5.2):
#: a pattern symbol matches a gap containing that mark or any mark that implies it.
#: Morpheme and clitic boundaries do *not* imply a syllable boundary.
SATISFIED_BY: Dict[Mark, FrozenSet[Mark]] = {
    Mark.SYLLABLE: frozenset({Mark.SYLLABLE, Mark.WORD, Mark.PHRASE}),
    Mark.MORPHEME: frozenset({Mark.MORPHEME, Mark.CLITIC, Mark.WORD, Mark.PHRASE}),
    Mark.CLITIC: frozenset({Mark.CLITIC, Mark.WORD, Mark.PHRASE}),
    Mark.WORD: frozenset({Mark.WORD, Mark.PHRASE}),
    Mark.PHRASE: frozenset({Mark.PHRASE}),
}

_BY_SYMBOL: Dict[str, Mark] = {m.value: m for m in Mark}


def gap_satisfies(marks: FrozenSet[Mark], wanted: Mark) -> bool:
    """True iff a gap carrying ``marks`` satisfies the pattern assertion ``wanted`` (spec §5.2)."""
    return not marks.isdisjoint(SATISFIED_BY[wanted])


def mark_from_symbol(symbol: str) -> Optional[Mark]:
    """The mark written as ``symbol`` in patterns (``. - = # ##``), or ``None`` (spec §5.2)."""
    return _BY_SYMBOL.get(symbol)


@dataclass(frozen=True)
class Bracket:
    """A labelled category span over gap indices, ``open_gap <= close_gap`` (spec §5.3).

    Brackets in a form are properly nested; the segments inside are
    ``segs[open_gap:close_gap]``.
    """

    label: str
    open_gap: int
    close_gap: int


class FormLike(Protocol):
    """The read-only view of a form used by the matcher (spec §5.1; design §4.3, §6).

    * ``n`` — number of segments; gaps are numbered ``0..n``.
    * ``seg(i)`` — the segment at index ``i`` (``0 <= i < n``).
    * ``view(i)`` — a :class:`~yasc.segment.SegView` for segment ``i``; syllable-scope and tier
      features resolve through it (plan P7/P8). Until then it may simply return
      ``PlainView(seg(i))`` or the segment itself.
    * ``gap_marks(g)`` — the *effective* marks of gap ``g``: stored marks, plus
      :attr:`Mark.PHRASE` at gaps ``0`` and ``n``, plus derived syllable edges once a syllable
      tier exists (plan P7).
    * ``brackets`` — all category brackets, outermost first in document order.
    """

    @property
    def n(self) -> int: ...

    def seg(self, i: int) -> Segment: ...

    def view(self, i: int) -> SegView: ...

    def gap_marks(self, g: int) -> FrozenSet[Mark]: ...

    @property
    def brackets(self) -> Tuple[Bracket, ...]: ...
