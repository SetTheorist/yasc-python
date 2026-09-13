"""Test-only helpers for the P2 tests: a minimal :class:`yasc.marks.FormLike` and a small
phonology (spec §5.1–§5.3). P3's ``yasc.form.Form`` is the real implementation."""

from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from yasc.features import FeatureSystem, FeatureType
from yasc.marks import Bracket, Mark
from yasc.segment import Absent, Eq, Segment, SegmentSpec, Var

_SEPARATORS = {".": Mark.SYLLABLE, "-": Mark.MORPHEME, "=": Mark.CLITIC, "#": Mark.WORD, " ": Mark.WORD}


class FakeForm:
    """Segments, per-gap marks and brackets; implements ``FormLike`` (spec §5.1).

    ``marks`` maps a gap index to an iterable of :class:`Mark`. Gaps ``0`` and ``n`` always
    carry :attr:`Mark.PHRASE` (spec §5.2). ``view(i)`` returns the segment itself.
    """

    __slots__ = ("segs", "_gaps", "_brackets")

    def __init__(self, segs: Sequence[Segment], marks: Optional[Mapping[int, Iterable[Mark]]] = None,
                 brackets: Iterable[Bracket] = ()) -> None:
        self.segs = tuple(segs)
        n = len(self.segs)
        marks = marks or {}
        gaps = []
        for g in range(n + 1):
            m = set(marks.get(g, ()))
            if g == 0 or g == n:
                m.add(Mark.PHRASE)
            gaps.append(frozenset(m))
        self._gaps = tuple(gaps)
        self._brackets = tuple(brackets)

    @property
    def n(self) -> int:
        return len(self.segs)

    def seg(self, i: int) -> Segment:
        return self.segs[i]

    def view(self, i: int):
        return self.segs[i]

    def gap_marks(self, g: int):
        return self._gaps[g]

    @property
    def brackets(self) -> Tuple[Bracket, ...]:
        return self._brackets

    def reversed(self) -> "FakeForm":
        """The mirror image: segment ``k`` -> ``n-1-k``, gap ``g`` -> ``n-g``."""
        n = self.n
        marks = {n - g: [m for m in ms if m is not Mark.PHRASE or g not in (0, n)] for g, ms in enumerate(self._gaps)}
        brs = [Bracket(b.label, n - b.close_gap, n - b.open_gap) for b in self._brackets]
        return FakeForm(tuple(reversed(self.segs)), marks, brs)

    @classmethod
    def parse(cls, text: str, table: Mapping[str, Segment], brackets: Iterable[Bracket] = ()) -> "FakeForm":
        """One character per segment; ``. - = #`` and space become marks on the gap."""
        segs = []
        marks: Dict[int, set] = {}
        for ch in text:
            if ch in _SEPARATORS:
                marks.setdefault(len(segs), set()).add(_SEPARATORS[ch])
            else:
                segs.append(table[ch])
        return cls(segs, marks, brackets)

    def __repr__(self) -> str:
        return "FakeForm(%d segs)" % self.n


def make_system() -> FeatureSystem:
    """A small sealed feature system for the matcher tests (spec §4)."""
    fs = FeatureSystem("p2test")
    fs.add_feature("Syll", FeatureType.binary())
    fs.add_feature("Voice", FeatureType.binary())
    fs.add_feature("Nasal", FeatureType.binary())
    fs.add_feature("Place", FeatureType.node(), children=("Labial", "Coronal", "Dorsal"))
    fs.add_feature("Labial", FeatureType.unary())
    fs.add_feature("Coronal", FeatureType.unary())
    fs.add_feature("Dorsal", FeatureType.unary())
    fs.add_feature("High", FeatureType.binary())
    fs.add_feature("Low", FeatureType.binary())
    fs.add_feature("Len", FeatureType.scalar(0, 3))
    fs.define_op("Len", "<Max>", ("3", "3", "3", "3"))
    return fs.seal()


def make_inventory(fs: FeatureSystem) -> Dict[str, Segment]:
    """Letters -> segments: p t k b d g m n (consonants), i u e a o (vowels)."""
    c = dict(Syll="-")
    inv = {
        "p": dict(c, Voice="-", Nasal="-", Labial="!"),
        "t": dict(c, Voice="-", Nasal="-", Coronal="!"),
        "k": dict(c, Voice="-", Nasal="-", Dorsal="!"),
        "b": dict(c, Voice="+", Nasal="-", Labial="!"),
        "d": dict(c, Voice="+", Nasal="-", Coronal="!"),
        "g": dict(c, Voice="+", Nasal="-", Dorsal="!"),
        "m": dict(c, Voice="+", Nasal="+", Labial="!"),
        "n": dict(c, Voice="+", Nasal="+", Coronal="!"),
        "i": dict(Syll="+", Voice="+", High="+", Low="-", Len=1),
        "u": dict(Syll="+", Voice="+", High="+", Low="-", Len=1, Labial="!"),
        "e": dict(Syll="+", Voice="+", High="-", Low="-", Len=1),
        "a": dict(Syll="+", Voice="+", High="-", Low="+", Len=1),
        "o": dict(Syll="+", Voice="+", High="-", Low="-", Len=3, Labial="!"),
    }
    return {k: fs.segment(v) for k, v in inv.items()}


def spec(fs: FeatureSystem, text: str = "", strict: bool = False) -> SegmentSpec:
    """Tiny spec builder for tests: ``"+Syll -Voice _Nasal 3Len (a)High"``: one-character
    values, ``_F`` and ``(var)F`` only."""
    cons = []
    for tok in text.split():
        if tok.startswith("("):
            name, feat = tok[1:].split(")")
            cons.append(Var(fs.feature(feat), name))
        elif tok[0] == "_":
            cons.append(Absent(fs.feature(tok[1:])))
        else:
            cons.append(Eq(fs.feature(tok[1:]), tok[0]))
    return SegmentSpec(fs, cons, strict=strict)
