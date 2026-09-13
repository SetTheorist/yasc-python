"""Orthographies: graphemes <-> feature bundles (spec §5.6; design §9).

An :class:`Orthography` is built with a Python API (the parser, plan P4, calls the same
methods for the lines of an ``Orthography [[ ... ]]`` block), then sealed. It then:

* **parses** text into a :class:`~yasc.form.Form` with a dynamic-programming tokenizer
  (:meth:`Orthography.parse`), and
* **renders** forms and segments back as text (:meth:`Orthography.render`,
  :meth:`Orthography.render_segment`), using an exact lookup first and otherwise a
  nearest-base search with greedy diacritics, and reporting approximate renderings.

Round trip: for any text that parses, ``parse(render(parse(text))) == parse(text)``, as long
as separators and delimiters are not also written inside graphemes or diacritics.
"""

import re
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .errors import SourceLoc, YascDefinitionError, YascRuntimeError
from .features import Feature, FeatureSystem
from .form import Form, bracket_events
from .marks import Bracket, Mark
from .segment import Absent, Eq, Segment, SegmentSpec
from .tiers import build_tiers, floating_by_gap, segments_with_tiers, tier_features

__all__ = [
    "Orthography",
    "OpaqueSegment",
    "UnparsableError",
    "is_unknown",
    "UNKNOWN",
    "SEPARATOR_KINDS",
    "ON_UNPARSABLE",
    "DEFAULT_K",
]

#: The pseudo-feature name of opaque segments kept by ``OnUnparsable = keep`` (spec §5.6).
UNKNOWN = "Unknown"

#: Accepted values of the ``on_unparsable`` argument (spec §5.6, ``!set OnUnparsable``).
ON_UNPARSABLE = ("error", "skip", "keep")

#: How many nearest bases the renderer tries (spec §5.6 step 3).
DEFAULT_K = 3

#: Separator kinds (spec §5.6) and the gap mark each one writes (``None``: no mark).
SEPARATOR_KINDS: Dict[str, Optional[Mark]] = {
    "phone": None,
    "syllable": Mark.SYLLABLE,
    "morpheme": Mark.MORPHEME,
    "clitic": Mark.CLITIC,
    "word": Mark.WORD,
    "phrase": Mark.PHRASE,
}

_DEFAULT_SEPARATORS: Dict[str, Optional[str]] = {
    "phone": None,
    "syllable": ".",
    "morpheme": "+",
    "clitic": "=",
    "word": "#",
    "phrase": "##",
}

_DEFAULT_DELIMITERS = {"open": "<", "label_end": ":", "close": ">"}

#: ``Orthography`` setting lines of spec §5.6 and what they set.
_SETTING_NAMES = {
    "phoneseparator": ("sep", "phone"),
    "syllableseparator": ("sep", "syllable"),
    "morphemeseparator": ("sep", "morpheme"),
    "cliticseparator": ("sep", "clitic"),
    "wordseparator": ("sep", "word"),
    "phraseseparator": ("sep", "phrase"),
    "bracketopen": ("delim", "open"),
    "bracketclose": ("delim", "close"),
    "bracketlabelend": ("delim", "label_end"),
    "floatingprefix": ("floating", None),
}

#: Output order of several marks in one gap: strongest first (spec §5.2).
_MARK_ORDER = (Mark.PHRASE, Mark.WORD, Mark.CLITIC, Mark.MORPHEME, Mark.SYLLABLE)

_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_ABSENT = object()  # "declared unspecified" (``_F``) in a phone declaration

# Token kinds of the tokenizer (design §9) and their tie-break rank among non-graphemes.
_UNIT, _WS, _MARK, _SYLMARK, _OPEN, _CLOSE, _PHONESEP, _UNKNOWN = range(8)
_FLOAT = 8  # plan P8: FloatingPrefix + a diacritic on an empty base (spec §5.5)
_BIG = 1 << 30


# --------------------------------------------------------------------------------------------
# Opaque segments
# --------------------------------------------------------------------------------------------


class OpaqueSegment(Segment):
    """An unparsable character kept by ``OnUnparsable = keep`` (spec §5.6 step 4).

    It has every real feature unspecified and carries the pseudo-feature ``!Unknown``
    (P3 decision 9): the text is part of its identity, it equals only opaque segments with
    the same text, and it renders verbatim. A rule that modifies it produces an ordinary
    segment. Use :func:`is_unknown` to test for it.
    """

    __slots__ = ("text",)

    def __init__(self, system: FeatureSystem, text: str) -> None:
        super().__init__(system, (None,) * system.size)
        self.text = text
        self._hash = hash((UNKNOWN, text))

    def get(self, feature):
        """``'!'`` for the pseudo-feature ``Unknown``, else as for :class:`Segment` (spec §5.6)."""
        if feature == UNKNOWN:
            return "!"
        return Segment.get(self, feature)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, Segment):
            return NotImplemented
        return isinstance(other, OpaqueSegment) and other.system is self.system and other.text == self.text

    def __ne__(self, other: object) -> bool:
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __hash__(self) -> int:
        return self._hash

    def canonical(self) -> str:
        """``{!Unknown}`` plus the kept text, e.g. ``{!Unknown '%'}`` (spec §5.6)."""
        return "{!%s %r}" % (UNKNOWN, self.text)

    __repr__ = canonical

    def __reduce__(self):
        return (OpaqueSegment, (self.system, self.text))


def is_unknown(seg: Segment) -> bool:
    """True if ``seg`` is an opaque ``!Unknown`` segment (spec §5.6 step 4)."""
    return isinstance(seg, OpaqueSegment)


class UnparsableError(YascRuntimeError):
    """Text that the orthography cannot parse (spec §5.6 step 4, §12).

    ``text`` is the input, ``offset`` and ``end_offset`` the 0-based span of the first
    unparsable run. The error is a run-time error because input records are data; the
    compiler (plan P4) re-raises it as a load-time error for ``[...]`` strings in a script.
    """

    text: str = ""
    offset: int = 0
    end_offset: int = 0


# --------------------------------------------------------------------------------------------
# Internal structures
# --------------------------------------------------------------------------------------------


class _Trie:
    """A character trie mapping strings to payloads (design §9)."""

    __slots__ = ("root",)

    def __init__(self) -> None:
        self.root: Dict[str, Any] = {}

    def add(self, text: str, payload: Any) -> None:
        node = self.root
        for ch in text:
            node = node.setdefault(ch, {})
        node.setdefault("", []).append(payload)

    def matches(self, text: str, pos: int) -> List[Tuple[int, List[Any]]]:
        """Every stored string that starts at ``text[pos]``: ``(end, payloads)``, shortest first."""
        out = []
        node = self.root
        i = pos
        n = len(text)
        while i < n:
            node = node.get(text[i])
            if node is None:
                break
            i += 1
            hit = node.get("")
            if hit:
                out.append((i, hit))
        return out


class _Phone:
    __slots__ = ("text", "order", "values", "raw", "closed")

    def __init__(self, text: str, order: int) -> None:
        self.text = text
        self.order = order
        self.values: Dict[int, Any] = {}
        self.raw: Optional[Segment] = None
        self.closed: Optional[Segment] = None


class _Diacritic:
    __slots__ = ("pre", "post", "kind", "order", "values", "spec")

    def __init__(self, pre: str, post: str, order: int) -> None:
        self.pre = pre
        self.post = post
        self.kind = "circumfix" if pre and post else ("prefix" if pre else "postfix")
        self.order = order
        self.values: Dict[int, Any] = {}
        self.spec: Optional[SegmentSpec] = None

    @property
    def template(self) -> str:
        return self.pre + "#" + self.post


class _Rendered:
    """The memoised rendering of one segment (design §9)."""

    __slots__ = ("text", "approximate", "residual", "value")

    def __init__(self, text: str, approximate: bool, residual: Tuple, value: Segment) -> None:
        self.text = text
        self.approximate = approximate
        self.residual = residual
        self.value = value


# --------------------------------------------------------------------------------------------
# Orthography
# --------------------------------------------------------------------------------------------


class Orthography:
    """An orthography over one feature system (spec §5.6; design §9).

    Build it with :meth:`add_phones`, :meth:`add_diacritic`, :meth:`ignore`,
    :meth:`set_separator`, :meth:`set_delimiter` (or :meth:`set_setting` for any setting
    line), :meth:`add_syllable_mark`; then :meth:`seal` it (``parse`` and ``render`` seal
    automatically). The feature system must be sealed first.
    """

    def __init__(self, fs: FeatureSystem, name: Optional[str] = None, *, k: int = DEFAULT_K) -> None:
        self.fs = fs
        self.name = name
        self.k = k
        self._phones: Dict[str, _Phone] = {}
        self._phone_list: List[_Phone] = []
        self._diacritics: Dict[str, _Diacritic] = {}
        self._diac_list: List[_Diacritic] = []
        self._ignored: set = set()
        self.separators: Dict[str, Optional[str]] = dict(_DEFAULT_SEPARATORS)
        self.delimiters: Dict[str, str] = dict(_DEFAULT_DELIMITERS)
        self._sylmarks: List[Tuple[str, Segment]] = []
        self.floating_prefix: str = "^"
        self._sealed = False
        self._render_memo: Dict[Segment, _Rendered] = {}
        self._unit_memo: Dict[Tuple, Segment] = {}

    # -- declarations ----------------------------------------------------------------------

    def _check_open(self, what: str, loc: Optional[SourceLoc]) -> None:
        if self._sealed:
            raise YascDefinitionError("cannot %s: the orthography is sealed" % what, loc)

    def _feature_values(self, features: Union[Mapping, SegmentSpec], what: str,
                        loc: Optional[SourceLoc]) -> List[Tuple[Feature, Optional[str]]]:
        """``(feature, value)`` pairs from a dict or an Eq/Absent-only spec; ``None`` = ``_F``."""
        out: List[Tuple[Feature, Optional[str]]] = []
        if isinstance(features, SegmentSpec):
            if features.system is not self.fs:
                raise YascDefinitionError("%s: the spec belongs to another feature system" % what, loc)
            for c in features.constraints:
                if type(c) is Eq:
                    out.append((c.feature, c.value))
                elif type(c) is Absent:
                    out.append((c.feature, None))
                else:
                    raise YascDefinitionError(
                        "%s: only plain values (vF) and _F are allowed in an orthography, not %s"
                        % (what, c.canonical()), c.loc or loc)
            return out
        if not isinstance(features, Mapping):
            raise TypeError("features must be a dict or a SegmentSpec, got %r" % (features,))
        for key, value in features.items():
            feat = self.fs.resolve(key, loc)
            if value is None:
                out.append((feat, None))
            elif feat.is_node:
                raise YascDefinitionError(
                    "%s: Node feature %r has no value of its own; specify its daughters" % (what, feat.name), loc)
            else:
                out.append((feat, feat.coerce(value, loc)))
        return out

    def _add_values(self, store: Dict[int, Any], pairs, what: str, loc: Optional[SourceLoc]) -> None:
        for feat, value in pairs:
            if feat.is_node:
                if value is not None:
                    raise YascDefinitionError("%s: Node feature %r cannot take a value" % (what, feat.name), loc)
                targets = [(d, _ABSENT) for d in feat.descendants]
            else:
                targets = [(feat.index, _ABSENT if value is None else value)]
            for idx, v in targets:
                old = store.get(idx)
                if old is not None and old != v:
                    name = self.fs.features[idx].name
                    shown = lambda x: "_" + name if x is _ABSENT else "%s%s" % (x, name)  # noqa: E731
                    raise YascDefinitionError(
                        "%s: conflicting values %s and %s" % (what, shown(old), shown(v)), loc,
                        hint="declarations add up (spec §5.6), so every value given for a grapheme must agree")
                store[idx] = v

    def add_phones(self, graphemes: Union[str, Iterable[str]], features: Union[Mapping, SegmentSpec],
                   loc: Optional[SourceLoc] = None) -> None:
        """``[g1 g2 ...] {F}``: each grapheme has (at least) the features ``F`` (spec §5.6).

        Declarations add up; a conflicting value raises :class:`YascDefinitionError`.
        ``graphemes`` is a list, or a string that is split on whitespace. ``features`` is a
        ``{feature: value}`` dict (``None`` means ``_F``) or a :class:`SegmentSpec` with only
        ``Eq``/``Absent`` constraints.
        """
        self._check_open("add graphemes", loc)
        if isinstance(graphemes, str):
            graphemes = graphemes.split()
        graphemes = list(graphemes)
        if not graphemes:
            raise YascDefinitionError("an orthography line needs at least one grapheme", loc)
        for g in graphemes:
            if not isinstance(g, str) or not g or any(ch.isspace() for ch in g):
                raise YascDefinitionError("invalid grapheme %r: graphemes are non-empty and contain no whitespace"
                                          % (g,), loc)
        for g in graphemes:
            pairs = self._feature_values(features, "grapheme [%s]" % g, loc)
            phone = self._phones.get(g)
            if phone is None:
                phone = _Phone(g, len(self._phone_list))
                self._phones[g] = phone
                self._phone_list.append(phone)
            self._add_values(phone.values, pairs, "grapheme [%s]" % g, loc)

    def add_diacritic(self, features: Union[Mapping, SegmentSpec], template: str,
                      loc: Optional[SourceLoc] = None) -> None:
        """``{F} ==> [pre#post]``: a diacritic adding ``F`` (spec §5.6).

        The template contains exactly one ``#``; ``pre``, ``post`` or both (a circumfix) are
        non-empty. Declaring the same template again adds features, like graphemes.
        """
        self._check_open("add a diacritic", loc)
        if not isinstance(template, str) or template.count("#") != 1:
            raise YascDefinitionError("diacritic template %r must contain exactly one '#'" % (template,), loc,
                                      hint="write the base position as #, e.g. [#_h], [~#] or [(#)]")
        pre, post = template.split("#")
        if not pre and not post:
            raise YascDefinitionError("diacritic template '#' adds no text", loc)
        if any(ch.isspace() for ch in template):
            raise YascDefinitionError("diacritic template %r contains whitespace" % (template,), loc)
        what = "diacritic [%s]" % template
        pairs = self._feature_values(features, what, loc)
        d = self._diacritics.get(template)
        if d is None:
            d = _Diacritic(pre, post, len(self._diac_list))
            self._diacritics[template] = d
            self._diac_list.append(d)
        self._add_values(d.values, pairs, what, loc)

    def ignore(self, features: Union[Iterable, Mapping, SegmentSpec], loc: Optional[SourceLoc] = None) -> None:
        """``*{F1 F2}``: features ignored when rendering (spec §5.6). Accepts names, indices,
        :class:`Feature` objects, a dict (its keys) or a spec (its features). Ignoring a Node
        ignores all its descendants."""
        self._check_open("change the ignore set", loc)
        if isinstance(features, SegmentSpec):
            feats = [c.feature for c in features.constraints]
        elif isinstance(features, (str, Feature)):
            feats = [self.fs.resolve(features, loc)]
        else:
            feats = [self.fs.resolve(f, loc) for f in features]
        for f in feats:
            if f.is_node:
                self._ignored.update(f.descendants)
            else:
                self._ignored.add(f.index)

    def set_separator(self, kind: str, text: Optional[str], loc: Optional[SourceLoc] = None) -> None:
        """``XSeparator == [text]`` for ``kind`` in phone, syllable, morpheme, clitic, word,
        phrase (spec §5.6). ``None`` or ``""`` disables the separator. Whitespace always
        separates words, whatever the word separator is."""
        self._check_open("change a separator", loc)
        key = kind.lower()
        if key.endswith("separator"):
            key = key[:-len("separator")]
        if key not in SEPARATOR_KINDS:
            raise YascDefinitionError("unknown separator kind %r" % (kind,), loc,
                                      hint="use one of: " + ", ".join(SEPARATOR_KINDS))
        self.separators[key] = self._check_text(text, "separator", loc, allow_none=True)

    def set_delimiter(self, kind: str, text: str, loc: Optional[SourceLoc] = None) -> None:
        """``BracketOpen``/``BracketLabelEnd``/``BracketClose == [text]`` (spec §5.6);
        ``kind`` is ``open``, ``label_end`` or ``close``."""
        self._check_open("change a bracket delimiter", loc)
        if kind not in self.delimiters:
            raise YascDefinitionError("unknown bracket delimiter %r (use open, label_end or close)" % (kind,), loc)
        self.delimiters[kind] = self._check_text(text, "bracket delimiter", loc)

    def set_setting(self, name: str, text: Optional[str], loc: Optional[SourceLoc] = None) -> None:
        """Any ``Name == [text]`` setting line of spec §5.6 (``PhoneSeparator``,
        ``BracketOpen``, ``FloatingPrefix``...), for the compiler (plan P4). ``Escape`` is not
        supported yet (P3 decision 14)."""
        entry = _SETTING_NAMES.get(name.lower())
        if entry is None:
            if name.lower() == "escape":
                from .errors import NotImplementedYet
                raise NotImplementedYet("the orthography setting Escape is not implemented", loc)
            raise YascDefinitionError("unknown orthography setting %r" % (name,), loc,
                                      hint="settings: " + ", ".join(sorted(_SETTING_NAMES)))
        what, key = entry
        if what == "sep":
            self.set_separator(key, text, loc)
        elif what == "delim":
            self.set_delimiter(key, text, loc)  # type: ignore[arg-type]
        else:
            self._check_open("change FloatingPrefix", loc)
            self.floating_prefix = self._check_text(text, "FloatingPrefix", loc)

    def add_syllable_mark(self, features: Union[Mapping, SegmentSpec], text: str,
                          loc: Optional[SourceLoc] = None) -> None:
        """``SyllableMark {F} == [text]``: text written before a syllable that sets the
        syllable features ``F`` and implies a syllable boundary (spec §5.6). Parsed marks are
        stored as ``(gap, Segment)`` pairs in ``Form.pending_syllable_marks`` for plan P7."""
        self._check_open("add a syllable mark", loc)
        text = self._check_text(text, "syllable mark", loc)
        pairs = self._feature_values(features, "syllable mark [%s]" % text, loc)
        seg = self.fs.segment({f.index: v for f, v in pairs if not f.is_node})
        self._sylmarks.append((text, seg))

    @staticmethod
    def _check_text(text: Optional[str], what: str, loc: Optional[SourceLoc], allow_none: bool = False):
        if text is None or text == "":
            if allow_none:
                return None
            raise YascDefinitionError("%s text must not be empty" % what, loc)
        if not isinstance(text, str) or any(ch.isspace() for ch in text):
            raise YascDefinitionError("%s %r must be a non-empty string without whitespace" % (what, text), loc)
        return text

    # -- sealing ---------------------------------------------------------------------------

    @property
    def sealed(self) -> bool:
        """True once :meth:`seal` has run (spec §5.6)."""
        return self._sealed

    def seal(self) -> "Orthography":
        """Freeze the orthography and build its tries and lookup tables (design §9).
        Sealing twice is a no-op."""
        if self._sealed:
            return self
        fs = self.fs
        if not fs.sealed:
            raise YascDefinitionError("the feature system must be sealed before the orthography")
        width = fs.size
        for p in self._phone_list:
            vals = [None] * width
            for idx, v in p.values.items():
                vals[idx] = None if v is _ABSENT else v
            p.raw = Segment.make(fs, tuple(vals))
            p.closed = fs.close_new(p.raw)
        feats = fs.features
        for d in self._diac_list:
            cons = [Absent(feats[idx]) if v is _ABSENT else Eq(feats[idx], v) for idx, v in d.values.items()]
            d.spec = SegmentSpec(fs, cons, output=True)
        self._graph_trie = _Trie()
        for p in self._phone_list:
            self._graph_trie.add(p.text, p.order)
        self._prefix_trie = _Trie()
        self._postfix_trie = _Trie()
        self._copen_trie = _Trie()
        for d in self._diac_list:
            if d.kind == "prefix":
                self._prefix_trie.add(d.pre, d.order)
            elif d.kind == "postfix":
                self._postfix_trie.add(d.post, d.order)
            else:
                self._copen_trie.add(d.pre, d.order)
        self._sep_trie = _Trie()
        for kind, text in self.separators.items():
            if text:
                mark = SEPARATOR_KINDS[kind]
                self._sep_trie.add(text, (_PHONESEP, None) if mark is None else (_MARK, mark))
        for n, (text, _seg) in enumerate(self._sylmarks):
            self._sep_trie.add(text, (_SYLMARK, n))
        self._compare = tuple(i for i in fs.leaf_indices if i not in self._ignored)
        self._tier_slots = frozenset(f.index for f in tier_features(fs))  # plan P8
        self._exact: Dict[Tuple, int] = {}
        for p in self._phone_list:
            self._exact.setdefault(self._project(p.closed), p.order)
        self._sealed = True
        return self

    # -- queries ---------------------------------------------------------------------------

    @property
    def graphemes(self) -> Tuple[str, ...]:
        """All graphemes in declaration order (spec §5.6)."""
        return tuple(p.text for p in self._phone_list)

    @property
    def diacritic_templates(self) -> Tuple[str, ...]:
        """All diacritic templates (``pre#post``) in declaration order (spec §5.6)."""
        return tuple(d.template for d in self._diac_list)

    @property
    def ignored(self) -> FrozenSet[int]:
        """Indices of the features ignored when rendering (spec §5.6)."""
        return frozenset(self._ignored)

    def phone_bundle(self, grapheme: str) -> Segment:
        """The declared features of a grapheme, without implications (spec §5.6, §8.2.4
        ``[x]`` items)."""
        self.seal()
        return self._phones[grapheme].raw  # type: ignore[return-value]

    def phone_segment(self, grapheme: str) -> Segment:
        """The segment a grapheme parses to: its features with implications applied (spec §5.6)."""
        self.seal()
        return self._phones[grapheme].closed  # type: ignore[return-value]

    # -- parsing ---------------------------------------------------------------------------

    def _unit_value(self, phone: int, merge: Tuple[int, ...], close: bool = True) -> Segment:
        """A base grapheme with diacritics merged innermost first, then implications (spec §5.6 step 3)."""
        key = (phone, merge, close)
        seg = self._unit_memo.get(key)
        if seg is None:
            seg = self._phone_list[phone].raw
            for d in merge:
                seg = self._diac_list[d].spec.apply(seg)  # type: ignore[union-attr]
            if close:
                seg = self.fs.close_new(seg)
            self._unit_memo[key] = seg
        return seg

    def _units_at(self, text: str, k: int) -> List[Tuple[int, Tuple, Tuple]]:
        """Every grapheme unit starting at ``k``: ``(end, key, (phone, merge_order))``.

        A unit is ``prefix* (circumfix-open)? grapheme (postfix | circumfix-close)*`` with at
        most one circumfix, whose close must appear (spec §5.6 step 2). Merge order,
        innermost first: postfixes inside the circumfix, the circumfix, postfixes after it,
        then prefixes from the nearest outwards (P3 decision 7).
        """
        out: List[Tuple[int, Tuple, Tuple]] = []
        phones = self._phone_list
        diacs = self._diac_list

        def finish(pos, prefs, circ, g, posts, closed_at):
            if circ is None:
                merge = posts + tuple(reversed(prefs))
            else:
                merge = posts[:closed_at] + (circ,) + posts[closed_at:] + tuple(reversed(prefs))
            p = phones[g]
            out.append((pos, (-len(p.text), p.order, len(merge), merge), (g, merge)))

        def post(pos, prefs, circ, g, posts, closed_at):
            if circ is None or closed_at is not None:
                finish(pos, prefs, circ, g, posts, closed_at)
            for end, ids in self._postfix_trie.matches(text, pos):
                post(end, prefs, circ, g, posts + (ids[0],), closed_at)
            if circ is not None and closed_at is None:
                close = diacs[circ].post
                if text.startswith(close, pos):
                    post(pos + len(close), prefs, circ, g, posts, len(posts))

        def base(pos, prefs, circ=None):
            for end, ids in self._graph_trie.matches(text, pos):
                post(end, prefs, circ, ids[0], (), None)

        def pre(pos, prefs):
            for end, ids in self._prefix_trie.matches(text, pos):
                pre(end, prefs + (ids[0],))
            for end, ids in self._copen_trie.matches(text, pos):
                for c in ids:
                    base(end, prefs, c)
            base(pos, prefs)

        pre(k, ())
        return out

    def _transitions(self, text: str, k: int) -> List[Tuple[int, int, Tuple, Tuple]]:
        """All tokens starting at ``k``: ``(end, unparsed_chars, key, token)`` (design §9)."""
        out: List[Tuple[int, int, Tuple, Tuple]] = []
        ch = text[k]
        if ch.isspace():
            end = k + 1
            while end < len(text) and text[end].isspace():
                end += 1
            return [(end, 0, (0, _BIG + _WS, 0, ()), (_WS, None, k, end))]
        for end, key, payload in self._units_at(text, k):
            out.append((end, 0, key, (_UNIT, payload, k, end)))
        for end, items in self._sep_trie.matches(text, k):
            for kind, payload in items:
                out.append((end, 0, (0, _BIG + kind, 0, ()), (kind, payload, k, end)))
        d_open, d_end, d_close = self.delimiters["open"], self.delimiters["label_end"], self.delimiters["close"]
        if text.startswith(d_open, k):
            m = _LABEL.match(text, k + len(d_open))
            if m and text.startswith(d_end, m.end()):
                end = m.end() + len(d_end)
                out.append((end, 0, (0, _BIG + _OPEN, 0, ()), (_OPEN, m.group(), k, end)))
        if text.startswith(d_close, k):
            end = k + len(d_close)
            out.append((end, 0, (0, _BIG + _CLOSE, 0, ()), (_CLOSE, None, k, end)))
        fp = self.floating_prefix
        if fp and text.startswith(fp, k):
            # Plan P8: a floating autosegment, written as the prefix plus one diacritic on
            # an empty base (spec §5.5 "Orthography").
            p0 = k + len(fp)
            for d in self._diac_list:
                body = d.pre + d.post
                if text.startswith(body, p0):
                    end = p0 + len(body)
                    out.append((end, 0, (0, _BIG + _FLOAT, 0, ()), (_FLOAT, d.order, k, end)))
        out.append((k + 1, 1, (0, _BIG + _UNKNOWN, 0, ()), (_UNKNOWN, ch, k, k + 1)))
        return out

    def tokenize(self, text: str) -> List[Tuple]:
        """The best tokenization of ``text`` (spec §5.6 step 2; design §9).

        Dynamic programming over character positions. Every token counts once: a grapheme
        with all its diacritics, a separator, a bracket delimiter, a run of whitespace, or an
        unparsable character. Candidates are scored ``(unparsed chars, tokens, per-token
        keys)``, lower is better; a grapheme unit's key is ``(-len(grapheme), declaration
        order, ...)`` and every other token's key sorts after all units, so graphemes and
        diacritics win over separators and delimiters that match the same text. Returns
        tokens ``(kind, payload, start, end)``.
        """
        self.seal()
        n = len(text)
        best: List[Optional[Tuple]] = [None] * (n + 1)
        best[0] = ((0, 0, ()), None, None)
        for k in range(n):
            here = best[k]
            if here is None:
                continue
            (u, t, seq) = here[0]
            for end, du, key, token in self._transitions(text, k):
                cand = (u + du, t + 1, seq + (key,))
                cur = best[end]
                if cur is None or cand < cur[0]:
                    best[end] = (cand, k, token)
        tokens = []
        k = n
        while k > 0:
            _score, prev, token = best[k]  # type: ignore[misc]
            tokens.append(token)
            k = prev
        tokens.reverse()
        return tokens

    def parse(self, text: str, *, on_unparsable: str = "error", loc: Optional[SourceLoc] = None,
              source_line: Optional[str] = None, close: bool = True) -> Form:
        """Parse text into a :class:`Form` (spec §5.6 "Parsing text into a form"; design §9).

        * Graphemes and diacritics become segments: the base bundle, diacritics merged from
          innermost to outermost, then implications (``close=False`` skips them).
        * Separators become gap marks (whitespace is a word boundary; leading and trailing
          whitespace is ignored); a syllable mark adds :attr:`Mark.SYLLABLE` and a pending
          syllable-feature entry; the phone separator only separates.
        * ``<L:`` ... ``>`` become brackets (with the configured delimiters).
        * Unparsable characters: ``on_unparsable='error'`` raises :class:`UnparsableError`
          with the column and a caret, ``'skip'`` drops them, ``'keep'`` turns each into an
          :class:`OpaqueSegment`.

        ``loc``, when given, is the location of ``text[0]`` in a source file and
        ``source_line`` the text of that line; error locations are then reported there.
        """
        if on_unparsable not in ON_UNPARSABLE:
            raise ValueError("on_unparsable must be one of %s, got %r" % (ON_UNPARSABLE, on_unparsable))
        tokens = self.tokenize(text)
        if on_unparsable == "error":
            for n, tok in enumerate(tokens):
                if tok[0] == _UNKNOWN:
                    end = tok[3]
                    for later in tokens[n + 1:]:
                        if later[0] != _UNKNOWN:
                            break
                        end = later[3]
                    bad = text[tok[2]:end]
                    raise self._error("cannot parse %r in %r: no grapheme, diacritic or separator matches"
                                      % (bad, text), text, tok[2], end, loc, source_line,
                                      "declare the grapheme, or use !set OnUnparsable = skip|keep")
        segs: List[Segment] = []
        gaps: List[set] = [set()]
        brackets: List[Optional[Bracket]] = []
        stack: List[Tuple[str, int, int, int]] = []
        pending: List[Tuple[int, Segment]] = []
        floats: List[Tuple[int, int, str]] = []
        last = len(tokens) - 1
        for n, (kind, payload, start, end) in enumerate(tokens):
            if kind == _UNIT:
                segs.append(self._unit_value(payload[0], payload[1], close))
                gaps.append(set())
            elif kind == _WS:
                if 0 < n < last:
                    gaps[-1].add(Mark.WORD)
            elif kind == _MARK:
                gaps[-1].add(payload)
            elif kind == _SYLMARK:
                # A mark at the form edge or after a word/phrase mark needs no stored
                # syllable boundary (the edge implies one, spec §5.2); storing it would be
                # written back as a spurious "." (design §13 entry 126).
                if segs and not (gaps[-1] & {Mark.WORD, Mark.PHRASE}):
                    gaps[-1].add(Mark.SYLLABLE)
                pending.append((len(segs), self._sylmarks[payload][1]))
            elif kind == _OPEN:
                stack.append((payload, len(segs), len(brackets), start))
                brackets.append(None)
            elif kind == _CLOSE:
                if not stack:
                    raise self._error("bracket close %r without a matching open bracket" % text[start:end],
                                      text, start, end, loc, source_line,
                                      "brackets are written %sLabel%s ... %s"
                                      % (self.delimiters["open"], self.delimiters["label_end"],
                                         self.delimiters["close"]))
                label, open_gap, idx, _s = stack.pop()
                brackets[idx] = Bracket(label, open_gap, len(segs))
            elif kind == _FLOAT:
                floats.extend(self._floating_values(len(segs), payload, text, start, end, loc, source_line))
            elif kind == _UNKNOWN:
                if on_unparsable == "keep":
                    segs.append(self._opaque(payload))
                    gaps.append(set())
            # _PHONESEP: nothing to record
        if stack:
            label, _g, _i, start = stack[-1]
            raise self._error("bracket %r is never closed" % label, text, start,
                              start + len(self.delimiters["open"]) + len(label), loc, source_line,
                              "close it with %s" % self.delimiters["close"])
        form = Form(tuple(segs), tuple(frozenset(g) for g in gaps), tuple(brackets), None, (), tuple(pending))
        if floats or self._tier_slots:
            form = build_tiers(form, floats, self.fs)  # plan P8: autosegments and links (spec §5.5)
        return form

    def _floating_values(self, gap: int, d: int, text: str, start: int, end: int, loc: Optional[SourceLoc],
                         source_line: Optional[str]) -> List[Tuple[int, int, str]]:
        """``(gap, tier feature index, value)`` for a floating-autosegment token: the tier
        values of diacritic ``d`` (spec §5.5)."""
        vals = [(idx, v) for idx, v in self._diac_list[d].values.items() if idx in self._tier_slots and v is not _ABSENT]
        if not vals:
            raise self._error("%r after the floating prefix sets no tier feature" % text[start:end], text, start, end,
                              loc, source_line, "a floating autosegment is written %s plus a tone diacritic, "
                              "e.g. %s' (spec §5.5)" % (self.floating_prefix, self.floating_prefix))
        return [(gap, idx, v) for idx, v in vals]

    def _floating_text(self, entries: Sequence[Tuple[int, str]]) -> str:
        """The text of floating autosegments: ``FloatingPrefix`` plus the diacritic that sets
        exactly that tier value, on an empty base (spec §5.5)."""
        parts = []
        for idx, value in entries:
            best = None
            for d in self._diac_list:
                if d.values.get(idx) == value and (best is None or len(d.values) < len(best.values)):
                    best = d
            parts.append(self.floating_prefix + (best.pre + best.post if best is not None else "[%s]" % value))
        return "".join(parts)

    def _opaque(self, text: str) -> OpaqueSegment:
        key = ("opaque", text)
        seg = self._unit_memo.get(key)
        if seg is None:
            seg = OpaqueSegment(self.fs, text)
            self._unit_memo[key] = seg
        return seg  # type: ignore[return-value]

    @staticmethod
    def _error(message: str, text: str, start: int, end: int, loc: Optional[SourceLoc],
               source_line: Optional[str], hint: str) -> UnparsableError:
        if loc is None:
            err = UnparsableError.at_offset(message, text, start, file="<input>", end_offset=end, hint=hint)
        else:
            err = UnparsableError(message, loc=SourceLoc(loc.file, loc.line, loc.col + start, loc.col + end),
                                  hint=hint, source_line=source_line)
        err.text, err.offset, err.end_offset = text, start, end
        return err  # type: ignore[return-value]

    # -- rendering -------------------------------------------------------------------------

    def _project(self, seg: Segment) -> Tuple:
        vals = seg.values
        return tuple(vals[i] for i in self._compare)

    def _distance(self, a: Segment, b: Segment) -> int:
        av, bv = a.values, b.values
        return sum(1 for i in self._compare if av[i] != bv[i])

    def _arrange(self, added: Sequence[int]) -> Tuple[List[int], Optional[int], List[int], List[int]]:
        """Split diacritics (in the order the search added them) into text positions:
        ``(prefixes nearest-first, circumfix, postfixes inside it, postfixes after it)``."""
        prefs, inner, outer = [], [], []
        circ = None
        for d in added:
            kind = self._diac_list[d].kind
            if kind == "prefix":
                prefs.append(d)
            elif kind == "circumfix":
                circ = d
            elif circ is None:
                inner.append(d)
            else:
                outer.append(d)
        return prefs, circ, inner, outer

    def _merge_order(self, added: Sequence[int]) -> Tuple[int, ...]:
        prefs, circ, inner, outer = self._arrange(added)
        return tuple(inner) + ((circ,) if circ is not None else ()) + tuple(outer) + tuple(prefs)

    def _unit_text(self, phone: int, added: Sequence[int]) -> str:
        prefs, circ, inner, outer = self._arrange(added)
        dl = self._diac_list
        parts = [dl[d].pre for d in reversed(prefs)]
        if circ is not None:
            parts.append(dl[circ].pre)
        parts.append(self._phone_list[phone].text)
        parts.extend(dl[d].post for d in inner)
        if circ is not None:
            parts.append(dl[circ].post)
        parts.extend(dl[d].post for d in outer)
        return "".join(parts)

    def _values_of(self, text: str) -> Optional[List[Segment]]:
        """The segments ``text`` parses to if it holds only grapheme units, phone separators
        and unknown characters (kept), else ``None``."""
        out = []
        for kind, payload, _s, _e in self.tokenize(text):
            if kind == _UNIT:
                out.append(self._unit_value(payload[0], payload[1]))
            elif kind == _UNKNOWN:
                out.append(self._opaque(payload))
            elif kind != _PHONESEP:
                return None
        return out

    def _render_info(self, seg: Segment) -> _Rendered:
        info = self._render_memo.get(seg)
        if info is not None:
            return info
        self.seal()
        if isinstance(seg, OpaqueSegment):
            info = _Rendered(seg.text, False, (), seg)
        else:
            hit = self._exact.get(self._project(seg))
            if hit is not None:
                p = self._phone_list[hit]
                info = _Rendered(p.text, False, (), p.closed)  # type: ignore[arg-type]
            else:
                info = self._search(seg)
        self._render_memo[seg] = info
        return info

    def _search(self, seg: Segment) -> _Rendered:
        """Nearest bases plus greedy diacritics (spec §5.6 rendering step 3; design §9)."""
        phones = self._phone_list
        if not phones:
            raise YascRuntimeError("orthography %s has no graphemes to render %r" % (self.name or "", seg))
        dists = [self._distance(seg, p.closed) for p in phones]  # type: ignore[arg-type]
        nearest = sorted(range(len(phones)), key=lambda p: (dists[p], p))[: max(1, self.k)]
        cands = []
        for p in nearest:
            added: List[int] = []
            cur_d, cur_v = dists[p], phones[p].closed
            while cur_d > 0:
                best = None
                has_circ = any(self._diac_list[d].kind == "circumfix" for d in added)
                for d in range(len(self._diac_list)):
                    if d in added or (has_circ and self._diac_list[d].kind == "circumfix"):
                        continue
                    # Try the new diacritic as the outermost, then as the innermost one.
                    trials = [added + [d]] + ([[d] + added] if added else [])
                    for trial in trials:
                        v = self._unit_value(p, self._merge_order(trial))
                        dd = self._distance(seg, v)
                        if dd < cur_d and (best is None or dd < best[0]):
                            best = (dd, trial, v)
                if best is None:
                    break
                added = best[1]
                cur_d, cur_v = best[0], best[2]
            added = self._tidy(p, added, cur_v)
            cands.append((cur_d, len(added), p, tuple(added), cur_v))
        cands.sort(key=lambda c: c[:4])
        chosen = None
        for c in cands:
            text = self._unit_text(c[2], c[3])
            if self._values_of(text) == [c[4]]:
                chosen = (c, text)
                break
        if chosen is None:
            c = cands[0]
            chosen = (c, self._unit_text(c[2], c[3]))
        (dist, _nd, _p, _added, value), text = chosen
        feats = self.fs.features
        residual = tuple((feats[i].name, seg.values[i], value.values[i])
                         for i in self._compare if seg.values[i] != value.values[i])
        return _Rendered(text, dist > 0, residual, value)

    def _tidy(self, phone: int, added: List[int], value: Segment) -> List[int]:
        """Prefer declaration order for the diacritics when it gives the same segment
        (design §9 step 5)."""
        prefs, circ, inner, outer = self._arrange(added)
        tidy = sorted(inner + outer) + ([circ] if circ is not None else []) + sorted(prefs)
        if tidy != added and self._unit_value(phone, self._merge_order(tidy)) == value:
            return tidy
        return added

    def render_segment(self, seg: Segment) -> Tuple[str, bool, Tuple[Tuple[str, Optional[str], Optional[str]], ...]]:
        """Render one segment (spec §5.6 "Rendering a form as text"; design §9).

        Returns ``(text, approximate, residual)``. ``approximate`` is true when no grapheme
        plus diacritics matches the segment exactly (ignored features aside); ``residual``
        lists ``(feature, segment value, rendered value)`` for the features that differ.
        Results are memoised per segment.
        """
        info = self._render_info(seg)
        return info.text, info.approximate, info.residual

    def render(self, form: Form) -> str:
        """Render a form as text (spec §5.6; design §9). See :meth:`render_ex`."""
        return self.render_ex(form)[0]

    def render_ex(self, form: Form) -> Tuple[str, List[Tuple[int, Tuple]]]:
        """Render a form; return ``(text, approximations)`` where ``approximations`` lists
        ``(segment index, residual)`` for every approximate segment (spec §5.6 step 4).

        Gap marks become separators (strongest first; a word boundary is a space inside the
        form and the word separator at its edges), brackets are written back, and syllable
        marks are written in place of the syllable separator. Adjacent segments whose texts
        would re-tokenize differently get the phone separator between them, if one is
        declared (P3 decision 12).
        """
        self.seal()
        n = form.n
        events = bracket_events(form.brackets, n)
        pend: Dict[int, List[str]] = {}
        for g, seg in form.pending_syllable_marks:
            pend.setdefault(g, []).append(self._sylmark_text(seg))
        # Plan P7: a syllable whose features match a SyllableMark gets the mark written
        # before its first segment (spec §5.6); a syllabified form has no pending marks.
        syl = form.syllables
        if syl is not None and self._sylmarks:
            for s in syl.syls:
                if any(v is not None for v in s.feats.values):
                    text = self._sylmark_text(s.feats)
                    if text:
                        pend.setdefault(s.start, []).append(text)
        out: List[str] = []
        approx: List[Tuple[int, Tuple]] = []
        chunk: List[_Rendered] = []
        # Plan P8: tier features render through the segment view (diacritics on the TBU), and
        # floating autosegments before the separators of their gap (spec §5.5).
        segs = segments_with_tiers(form) if form.tiers else form.segs
        floats = floating_by_gap(form) if form.tiers else {}
        for g in range(n + 1):
            gap_text = self._gap_text(g, n, form.gaps[g], events[g], pend.get(g, ()))
            if floats.get(g):
                gap_text = self._floating_text(floats[g]) + gap_text
            if gap_text:
                out.append(self._join_chunk(chunk))
                chunk = []
                out.append(gap_text)
            if g < n:
                info = self._render_info(segs[g])
                if info.approximate:
                    approx.append((g, info.residual))
                chunk.append(info)
        out.append(self._join_chunk(chunk))
        return "".join(out), approx

    def _sylmark_text(self, seg: Segment) -> str:
        for text, s in self._sylmarks:
            if s == seg:
                return text
        # A syllable bundle with no declared mark: the first mark whose features it has.
        for text, s in self._sylmarks:
            if all(v is None or seg.values[i] == v for i, v in enumerate(s.values)):
                return text
        return ""

    def _gap_text(self, g: int, n: int, marks: FrozenSet[Mark], events, sylmarks: Sequence[str]) -> str:
        mark_parts = []
        for m in _MARK_ORDER:
            if m not in marks or (m is Mark.SYLLABLE and sylmarks):
                continue
            if m is Mark.WORD and 0 < g < n:
                mark_parts.append(" ")
                continue
            for kind, mark in SEPARATOR_KINDS.items():
                if mark is m:
                    text = self.separators.get(kind)
                    if not text and m is Mark.WORD:
                        text = " "
                    if text:
                        mark_parts.append(text)
        parts: List[str] = []
        placed = False
        d = self.delimiters
        for kind, b in events:
            if kind == "open" and not placed:
                parts.extend(mark_parts)
                placed = True
            parts.append(d["open"] + b.label + d["label_end"] if kind == "open" else d["close"])
        if not placed:
            parts.extend(mark_parts)
        parts.extend(sylmarks)
        return "".join(parts)

    def _join_chunk(self, chunk: List[_Rendered]) -> str:
        if not chunk:
            return ""
        if len(chunk) == 1:
            return chunk[0].text
        text = "".join(c.text for c in chunk)
        values = [c.value for c in chunk]
        sep = self.separators.get("phone")
        if not sep or self._values_of(text) == values:
            return text
        out = chunk[0].text
        for m in range(1, len(chunk)):
            trial = out + chunk[m].text
            if self._values_of(trial) == values[: m + 1]:
                out = trial
            else:
                out = out + sep + chunk[m].text
        return out

    def __repr__(self) -> str:
        return "<Orthography %s%d graphemes, %d diacritics%s>" % (
            (self.name + ": ") if self.name else "", len(self._phone_list), len(self._diac_list),
            ", sealed" if self._sealed else "")
