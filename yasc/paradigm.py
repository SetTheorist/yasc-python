"""Paradigm expansion (spec §9; plan P9; decisions in ``docs/decisions/P9.md``).

A ``Paradigm`` section (:class:`~yasc.ir.ParadigmDef`) is a list of cells
``Label : template``. Expanding a variant produces one variant per cell whose ``/:L±`` and
``/:D±`` restrictions admit the record and dialect, each labelled with its cell name. The
template is a sequence of the stem ``$_``, orthographic strings, boundaries and brackets
``<Label: ... >``, so a cell can build the bracketed structure that cyclic rules (``/:C*``,
spec §8.6) need.

The runtime (:mod:`yasc.runtime`) calls :func:`applicable_cells` and :func:`build_cell`
from ``!paradigm $X`` and from paradigm-tagged records (P9 decision 8).
"""

from typing import Iterable, List, Mapping, Optional, Sequence, Set

from .form import Form
from .marks import Bracket, Mark
from .rules import lexical_ok
from .tiers import concat_tiers
from .variants import names_ok

__all__ = ["cell_ok", "applicable_cells", "build_cell", "concatenate"]


def cell_ok(cell, lexical: Mapping[str, str], dialect: Optional[str]) -> bool:
    """True if the cell's ``/:L±`` restrictions admit the record's lexical features and its
    ``/:D±`` restrictions admit ``dialect`` (spec §9, §8.6)."""
    r = cell.restrictions
    if r.lexical and not lexical_ok(r.lexical, lexical):
        return False
    return not r.dialect or names_ok(r.dialect, dialect)


def applicable_cells(paradigm, lexical: Mapping[str, str], dialect: Optional[str]) -> List:
    """The cells of ``paradigm`` (a :class:`~yasc.ir.ParadigmDef`) that apply, in
    definition order (spec §9)."""
    return [c for c in paradigm.cells if cell_ok(c, lexical, dialect)]


class _Builder:
    """Accumulates segments, gap marks and brackets from left to right."""

    def __init__(self) -> None:
        self.segs: List = []
        self.gaps: List[Set[Mark]] = [set()]
        self.brackets: List[Optional[Bracket]] = []
        self.open: List = []
        self.parts: List = []  # plan P8: (offset, form) pairs, for the autosegmental tiers

    def put(self, form: Form) -> None:
        """Append ``form``: its first gap merges with the current last gap (spec §9)."""
        base = len(self.segs)
        self.parts.append((base, form))
        self.gaps[-1] |= form.gaps[0]
        self.segs.extend(form.segs)
        self.gaps.extend(set(g) for g in form.gaps[1:])
        self.brackets.extend(Bracket(b.label, b.open_gap + base, b.close_gap + base) for b in form.brackets)

    def mark(self, m: Mark) -> None:
        """Store boundary mark ``m`` in the current last gap."""
        self.gaps[-1].add(m)

    def open_bracket(self, label: str) -> None:
        """Open ``<label:`` here; its slot keeps document order for nested brackets."""
        self.open.append((len(self.brackets), label, len(self.segs)))
        self.brackets.append(None)

    def close_bracket(self) -> None:
        """Close the innermost open bracket here."""
        k, label, start = self.open.pop()
        self.brackets[k] = Bracket(label, start, len(self.segs))

    def form(self) -> Form:
        """The finished form (spec §5.1)."""
        while self.open:
            self.close_bracket()
        return Form(tuple(self.segs), tuple(frozenset(g) for g in self.gaps),
                    tuple(b for b in self.brackets if b is not None), None,
                    concat_tiers(self.parts, len(self.segs)))  # plan P8: tones carry into cells


def build_cell(cell, stem: Form) -> Form:
    """The form of one paradigm cell (spec §9): the template items in order, with ``$_``
    replaced by ``stem``. Boundaries go into the gap where they are written, and brackets
    enclose the material between ``<Label:`` and ``>``. The result has no syllable tier; the
    runtime re-syllabifies it if the stem had one (P9 decision 8)."""
    b = _Builder()
    for kind, value in cell.items:
        if kind == "stem":
            b.put(stem)
        elif kind == "ortho":
            if value is not None:
                b.put(value)
        elif kind == "boundary":
            if value is not None:
                b.mark(value)
        elif kind == "open":
            b.open_bracket(value)
        elif kind == "close":
            b.close_bracket()
    return b.form()


def concatenate(forms: Sequence[Form], marks: Iterable[Mark] = ()) -> Form:
    """Concatenate ``forms``, putting ``marks`` into every joint gap (a helper for
    templates and tests; spec §5.1)."""
    b = _Builder()
    marks = tuple(marks)
    for k, f in enumerate(forms):
        if k:
            for m in marks:
                b.mark(m)
        b.put(f)
    return b.form()
