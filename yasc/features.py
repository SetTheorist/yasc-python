"""Feature types, features, the feature system and implications (spec §4; design §3).

The objects here are built with plain Python constructors; the parser (plan P4) produces
the same calls from ``Phonology [[ ... ]]`` blocks.

Value representation (design §13, decision 10):

* every feature value is a string: ``'+'``/``'-'`` (Binary), ``'!'`` (Unary), decimal
  strings such as ``'2'`` (Scalar) and bare names such as ``'H'`` for bracketed enumerated
  values (``[H]`` in the syntax). Scalar constructors also accept ``int``;
* ``None`` means *unspecified* on a segment and *undefined* (``_``) as an operation result;
* Node features have no values; a node is *present* (``!N``) iff some descendant leaf is
  specified (spec §4.3).

Typical use::

    fs = FeatureSystem()
    fs.add_feature("Syll", FeatureType.binary())
    fs.add_feature("Place", FeatureType.node(), children=("Labial", "Coronal"))
    fs.add_feature("Labial", FeatureType.unary())
    fs.add_feature("Coronal", FeatureType.unary())
    fs.seal()
"""

import difflib
import re
from dataclasses import dataclass
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from weakref import WeakValueDictionary

from .errors import SourceLoc, YascDefinitionError, YascError, YascLoadError, YascRuntimeError

__all__ = [
    "UNARY",
    "BINARY",
    "SCALAR",
    "ENUM",
    "NODE",
    "KINDS",
    "SEGMENT",
    "SYLLABLE",
    "ROLE",
    "SCOPES",
    "PRESENT",
    "UNDEFINED",
    "UNSPEC",
    "BundleValue",
    "format_value",
    "FeatureType",
    "TierDecl",
    "Feature",
    "Implication",
    "FeatureSystem",
]

# --------------------------------------------------------------------------------------------
# Constants and small value objects
# --------------------------------------------------------------------------------------------

#: Feature-type kinds (spec §4.1).
UNARY = "unary"
BINARY = "binary"
SCALAR = "scalar"
ENUM = "enum"
NODE = "node"
KINDS = (UNARY, BINARY, SCALAR, ENUM, NODE)

#: Feature scopes (spec §5.4): stored on the segment, or on its syllable. ``role`` marks the
#: read-only role pseudo-features ``SylOnset`` ... ``Syllabified`` (plan P7; design §13
#: entry 114): their value is derived from the syllable tier and never stored.
SEGMENT = "segment"
SYLLABLE = "syllable"
ROLE = "role"
SCOPES = (SEGMENT, SYLLABLE, ROLE)

#: The single value of a Unary feature, and the presence value of a Node (spec §4.1, §4.3).
PRESENT = "!"

#: The token written for an undefined operation result (spec §4.1).
UNDEFINED = "_"

DEFAULT_SCALAR_MIN = 0
DEFAULT_SCALAR_MAX = 9

_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
# Spec §4.1 (a): no letters, whitespace or []{}()#. ``_`` is also excluded because it is the
# unspecified/undefined marker (design §13, decision 10).
_VALUE_TOKEN = re.compile(r"[^\sA-Za-z\[\]{}()#_]+\Z")
_ENUM_NAME = re.compile(r"[A-Za-z]+\Z")
# Spec §4.1: an op is any string without letters, or ``<Name>`` (``<M->`` is allowed).
_OP_NAME = re.compile(r"(?:[^\sA-Za-z\[\]{}()#]+|<[^\s<>()\[\]{}#]+>)\Z")


class _Unspec:
    """The sentinel bound by a weak variable ``(?a)F`` when F is unspecified (spec §6.2)."""

    __slots__ = ()
    _instance = None

    def __new__(cls) -> "_Unspec":
        if cls._instance is None:
            cls._instance = object.__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSPEC"

    def __reduce__(self):  # pragma: no cover - pickling support only
        return (_Unspec, ())


#: "Unspecified" as a variable value (spec §6.2, row ``(?a)F``).
UNSPEC = _Unspec()


class BundleValue(tuple):
    """The value bound by a variable on a Node feature: ``(p)Place`` (spec §4.3 rule 4).

    A frozen tuple of ``(leaf_index, value)`` pairs over *all* descendant leaves of the node,
    in index order, including unspecified ones (``value is None``). Applying it as an output
    replaces the whole subtree.
    """

    __slots__ = ()

    def __new__(cls, items: Iterable[Tuple[int, Optional[str]]]) -> "BundleValue":
        return tuple.__new__(cls, items)

    @property
    def indices(self) -> Tuple[int, ...]:
        """The descendant leaf indices covered by this bundle (spec §4.3)."""
        return tuple(i for i, _ in self)

    def format(self, system: "FeatureSystem") -> str:
        """Render the bundle with feature names, e.g. ``<!Labial _Coronal>`` (spec §4.3)."""
        parts = []
        for i, v in self:
            name = system.features[i].name
            parts.append("_" + name if v is None else format_value(v) + name)
        return "<" + " ".join(parts) + ">"

    def __repr__(self) -> str:
        return "BundleValue(%s)" % (tuple.__repr__(self),)


def format_value(value: str) -> str:
    """Canonical token for a value: alphabetic names are bracketed, ``[H]`` (spec §4.1)."""
    return "[%s]" % value if value[:1].isalpha() else value


def _suggest(name: str, candidates: Iterable[str]) -> Optional[str]:
    """Closest candidate to ``name``: case-insensitive equality first, then difflib."""
    cands = list(candidates)
    lowered = name.lower()
    for c in cands:
        if c.lower() == lowered:
            return c
    close = difflib.get_close_matches(name, cands, n=1, cutoff=0.6)
    return close[0] if close else None


# --------------------------------------------------------------------------------------------
# Feature types
# --------------------------------------------------------------------------------------------


class FeatureType:
    """An immutable feature type: a kind, a value tuple and named operations (spec §4.1).

    ``values`` is in declaration order. ``ops`` maps an operation name (written without its
    parentheses, e.g. ``'-'``, ``'++'``, ``'<Max>'``) to a tuple with one result per value;
    ``None`` is an undefined result (``_``). Built-in operations: Binary ``(-)`` swaps the
    values; Scalar ``(++)`` and ``(--)`` step up and down, saturating at the ends. Declared
    operations with the same name replace the built-ins.

    Use the class methods :meth:`unary`, :meth:`binary`, :meth:`scalar`, :meth:`enum` and
    :meth:`node` rather than the constructor.
    """

    __slots__ = ("kind", "values", "ops", "_value_set", "_canon", "_maps", "_inverse", "_cache")

    def __init__(
        self,
        kind: str,
        values: Sequence[Union[str, int]] = (),
        ops: Optional[Mapping[str, Sequence[Optional[Union[str, int]]]]] = None,
        loc: Optional[SourceLoc] = None,
    ) -> None:
        if kind not in KINDS:
            raise ValueError("unknown feature kind %r" % (kind,))
        self.kind = kind
        self.values = self._check_values(kind, values, loc)
        self._value_set = frozenset(self.values)
        self._canon = {v: v for v in self.values}
        if kind == NODE and ops:
            raise YascDefinitionError("a Node feature has no values, so it cannot have operations", loc)
        table: Dict[str, Tuple[Optional[str], ...]] = {}
        table.update(self._builtin_ops())
        for op, results in (ops or {}).items():
            table[self._check_op_name(op, loc)] = self._check_results(op, results, loc)
        self.ops = table
        self._maps = {op: dict(zip(self.values, res)) for op, res in table.items()}
        self._inverse = {op: self._invert(self._maps[op]) for op in table}
        self._cache: Dict[Tuple[str, ...], Tuple[Dict, Dict]] = {}

    # -- constructors ---------------------------------------------------------------------

    @classmethod
    def unary(cls, ops: Optional[Mapping] = None) -> "FeatureType":
        """A Unary type: the single value ``!`` (spec §4.1, §4.2)."""
        return cls(UNARY, (PRESENT,), ops)

    @classmethod
    def binary(cls, ops: Optional[Mapping] = None) -> "FeatureType":
        """A Binary type: values ``+ -`` with the built-in ``(-)`` swap (spec §4.1)."""
        return cls(BINARY, ("+", "-"), ops)

    @classmethod
    def scalar(
        cls, lo: int = DEFAULT_SCALAR_MIN, hi: int = DEFAULT_SCALAR_MAX, ops: Optional[Mapping] = None
    ) -> "FeatureType":
        """A Scalar type with integer values ``lo..hi`` and ``(++)``/``(--)`` (spec §4.1)."""
        if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int) or not isinstance(hi, int):
            raise YascDefinitionError("Scalar bounds must be integers, got (%r, %r)" % (lo, hi))
        if lo > hi:
            raise YascDefinitionError("Scalar(%d,%d): the minimum exceeds the maximum" % (lo, hi))
        return cls(SCALAR, tuple(str(i) for i in range(lo, hi + 1)), ops)

    @classmethod
    def enum(cls, values: Sequence[str], ops: Optional[Mapping] = None) -> "FeatureType":
        """A many-valued type, as ``Tone [H] [L]`` or the ad-hoc ``High + -`` (spec §4.1).

        Alphabetic names may be given with or without brackets (``'[H]'`` or ``'H'``).
        """
        return cls(ENUM, tuple(values), ops)

    @classmethod
    def node(cls) -> "FeatureType":
        """A geometry class node: no stored values, no operations (spec §4.3)."""
        return cls(NODE, ())

    def with_op(
        self, op: str, results: Sequence[Optional[Union[str, int]]], loc: Optional[SourceLoc] = None
    ) -> "FeatureType":
        """Return a copy of this type with operation ``op`` (re)defined (spec §4.1 ``(op) r...``)."""
        ops = dict(self.ops)
        ops[op] = results
        return FeatureType(self.kind, self.values, ops, loc)

    # -- validation helpers ---------------------------------------------------------------

    @staticmethod
    def _check_values(kind: str, values: Sequence, loc: Optional[SourceLoc]) -> Tuple[str, ...]:
        if kind == NODE:
            if values:
                raise YascDefinitionError("a Node feature has no values", loc)
            return ()
        out: List[str] = []
        for v in values:
            if kind == SCALAR:
                if isinstance(v, bool):
                    raise YascDefinitionError("invalid Scalar value %r" % (v,), loc)
                try:
                    s = str(int(v))
                except (TypeError, ValueError):
                    raise YascDefinitionError("invalid Scalar value %r" % (v,), loc) from None
            else:
                if not isinstance(v, str):
                    raise YascDefinitionError("feature values must be strings, got %r" % (v,), loc)
                s = v[1:-1] if len(v) > 2 and v[0] == "[" and v[-1] == "]" else v
                if not (_VALUE_TOKEN.match(s) or _ENUM_NAME.match(s)):
                    raise YascDefinitionError(
                        "invalid feature value %r" % (v,),
                        loc,
                        hint="a value is a string without letters, whitespace, _ or []{}()#, "
                        "or an alphabetic name in brackets such as [H]",
                    )
            if s in out:
                raise YascDefinitionError("duplicate feature value %r" % (v,), loc)
            out.append(s)
        if kind == UNARY and tuple(out) != (PRESENT,):
            raise YascDefinitionError("a Unary feature has exactly the value '!'", loc)
        if kind == BINARY and tuple(out) != ("+", "-"):
            raise YascDefinitionError("a Binary feature has exactly the values '+' and '-'", loc)
        if kind in (SCALAR, ENUM) and not out:
            raise YascDefinitionError("a %s feature needs at least one value" % kind, loc)
        return tuple(out)

    def _builtin_ops(self) -> Dict[str, Tuple[Optional[str], ...]]:
        vals = self.values
        if self.kind == BINARY:
            return {"-": ("-", "+")}
        if self.kind == SCALAR:
            last = len(vals) - 1
            return {
                "++": tuple(vals[min(i + 1, last)] for i in range(len(vals))),
                "--": tuple(vals[max(i - 1, 0)] for i in range(len(vals))),
            }
        return {}

    @staticmethod
    def _check_op_name(op: str, loc: Optional[SourceLoc]) -> str:
        if not isinstance(op, str):
            raise YascDefinitionError("operation names are strings, got %r" % (op,), loc)
        name = op[1:-1] if len(op) > 2 and op[0] == "(" and op[-1] == ")" else op
        if not _OP_NAME.match(name):
            raise YascDefinitionError(
                "invalid operation name %r" % (op,),
                loc,
                hint="an operation is a string without letters, such as (-) or (++), or <Name>",
            )
        return name

    def _check_results(self, op: str, results: Sequence, loc: Optional[SourceLoc]) -> Tuple[Optional[str], ...]:
        results = tuple(results)
        if len(results) != len(self.values):
            raise YascDefinitionError(
                "operation (%s) lists %d result%s but the feature has %d value%s"
                % (op, len(results), "" if len(results) == 1 else "s", len(self.values),
                   "" if len(self.values) == 1 else "s"),
                loc,
                hint="give one result per value, in declaration order; write _ for undefined",
            )
        return tuple(None if r is None or r == UNDEFINED else self.coerce(r, loc) for r in results)

    @staticmethod
    def _invert(mapping: Mapping[str, Optional[str]]) -> Dict[str, Tuple[str, ...]]:
        inv: Dict[str, List[str]] = {}
        for v, r in mapping.items():
            if r is not None:
                inv.setdefault(r, []).append(v)
        return {r: tuple(vs) for r, vs in inv.items()}

    # -- queries --------------------------------------------------------------------------

    @property
    def is_node(self) -> bool:
        """True for Node types (spec §4.3)."""
        return self.kind == NODE

    def has_value(self, value: object) -> bool:
        """True if ``value`` (a canonical string) belongs to this type (spec §6.2)."""
        try:
            return value in self._value_set
        except TypeError:
            return False

    def coerce(self, value: Union[str, int], loc: Optional[SourceLoc] = None, feature_name: Optional[str] = None) -> str:
        """Return the canonical value string for ``value`` or raise (spec §6.2 "values must
        belong to the feature's type").

        Accepts ``int`` for Scalar types and ``'[H]'`` as well as ``'H'`` for names.
        """
        s: object = value
        if isinstance(value, bool):
            s = None
        elif isinstance(value, int):
            s = str(value)
        elif isinstance(value, str) and len(value) > 2 and value[0] == "[" and value[-1] == "]":
            s = value[1:-1]
        canon = self._canon.get(s) if isinstance(s, str) else None
        if canon is None:
            what = "feature %r" % feature_name if feature_name else "this %s feature" % self.kind
            shown = " ".join(format_value(v) for v in self.values) or "(none: Node)"
            raise YascDefinitionError(
                "%r is not a value of %s" % (value, what), loc, hint="the values are: %s" % shown
            )
        return canon

    def apply(self, op: str, value: Optional[str]) -> Optional[str]:
        """Apply operation ``op`` to ``value`` (spec §4.1, §4.4).

        Returns ``None`` when the result is undefined (``_``) or ``value`` is not a value of
        the type. Raises :class:`YascDefinitionError` for an unknown operation.
        """
        return self.op_map(op).get(value)  # type: ignore[arg-type]

    def op_map(self, op: str) -> Dict[str, Optional[str]]:
        """The forward table ``value -> result`` of one operation (spec §4.1)."""
        m = self._maps.get(op)
        if m is None:
            self._unknown_op(op)
        return m  # type: ignore[return-value]

    def preimages(self, op: str, value: str) -> Tuple[str, ...]:
        """All values ``x`` with ``op(x) == value``, in declaration order (spec §6.2 ``op(a)F``).

        Precomputed at construction, so non-injective operations (several preimages) and
        values outside the operation's range (no preimage) are handled in O(1).
        """
        if op not in self._inverse:
            self._unknown_op(op)
        return self._inverse[op].get(value, ())

    def compose(self, ops: Sequence[str], feature_name: Optional[str] = None) -> Tuple[Dict, Dict]:
        """Forward and inverse tables for the composition ``op1#op2#...`` (spec §4.4).

        ``ops`` is in written order; the operation nearest the variable (the last one) is
        applied first, so ``('op1', 'op2')`` is ``op1(op2(a))``. An undefined intermediate
        result makes the whole result undefined. Returns ``(forward, inverse)``, where
        ``forward`` maps each value to its result (``None`` = undefined) and ``inverse`` maps
        a result to its preimages in declaration order. Tables are cached per ``ops``.
        """
        key = tuple(ops)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        for op in key:
            if op not in self._maps:
                self._unknown_op(op, feature_name)
        forward: Dict[str, Optional[str]] = {}
        for v in self.values:
            r: Optional[str] = v
            for op in reversed(key):
                r = self._maps[op][r]
                if r is None:
                    break
            forward[v] = r
        tables = (forward, self._invert(forward))
        self._cache[key] = tables
        return tables

    def apply_ops(self, ops: Sequence[str], value: Optional[str]) -> Optional[str]:
        """Apply a composition ``op1#op2#...`` to ``value`` (spec §4.4); ``None`` if undefined."""
        return self.compose(ops)[0].get(value)  # type: ignore[arg-type]

    def preimages_ops(self, ops: Sequence[str], value: str) -> Tuple[str, ...]:
        """Preimages of ``value`` under a composition of operations (spec §4.4, §6.2)."""
        return self.compose(ops)[1].get(value, ())

    def shares_values_with(self, other: "FeatureType") -> bool:
        """True if one variable may range over both types (spec §6.2): identical value sets,
        or both Binary. Node types share only with Node types."""
        if self.kind == NODE or other.kind == NODE:
            return self.kind == other.kind
        return self._value_set == other._value_set or (self.kind == other.kind == BINARY)

    def _unknown_op(self, op: str, feature_name: Optional[str] = None) -> None:
        known = " ".join("(%s)" % o for o in self.ops) or "none"
        what = "feature %r" % feature_name if feature_name else "this %s feature" % self.kind
        raise YascDefinitionError("%s has no operation (%s)" % (what, op), hint="defined operations: %s" % known)

    def canonical(self) -> str:
        """The type as written in a declaration, without the feature name (spec §4.1, §11.3)."""
        if self.kind == UNARY:
            return "Unary"
        if self.kind == BINARY:
            return "Binary"
        if self.kind == NODE:
            return "Node"
        if self.kind == SCALAR:
            return "Scalar(%s,%s)" % (self.values[0], self.values[-1])
        return " ".join(format_value(v) for v in self.values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FeatureType):
            return NotImplemented
        return self.kind == other.kind and self.values == other.values and self.ops == other.ops

    def __hash__(self) -> int:
        return hash((self.kind, self.values, tuple(sorted(self.ops.items()))))

    def __repr__(self) -> str:
        return "FeatureType(%s)" % self.canonical()


# --------------------------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TierDecl:
    """Placeholder for a ``Tier(TBU=..., Stray=..., OCP=...)`` modifier (spec §5.5; plan P8).

    ``tbu`` is the TBU segment spec (any object until P8). Only the flag and the settings are
    recorded in P1.
    """

    tbu: object = None
    stray: str = "float"
    ocp: str = "off"

    def __post_init__(self) -> None:
        if self.stray not in ("float", "delete"):
            raise YascDefinitionError("Tier Stray must be float or delete, got %r" % (self.stray,))
        if self.ocp not in ("off", "merge", "delete"):
            raise YascDefinitionError("Tier OCP must be off, merge or delete, got %r" % (self.ocp,))


class Feature:
    """A declared feature (spec §4.1): name, index, aliases, type and geometry links.

    ``index`` is the declaration order and the slot in every segment's value tuple.
    ``parent``/``children`` give the geometry (spec §4.3); ``descendants`` holds the indices
    of the leaf (non-Node) features below a node, in index order, and is ``()`` for leaves.
    ``scope`` is ``'segment'`` or ``'syllable'`` (spec §5.4); ``tier`` is a :class:`TierDecl`
    for autosegmental features (spec §5.5), else ``None``. Geometry fields are filled in by
    :meth:`FeatureSystem.seal`.
    """

    __slots__ = (
        "name", "index", "aliases", "type", "parent", "children", "descendants",
        "scope", "tier", "loc", "system", "child_names",
    )

    def __init__(
        self,
        system: "FeatureSystem",
        name: str,
        index: int,
        ftype: FeatureType,
        scope: str = SEGMENT,
        tier: Optional[TierDecl] = None,
        loc: Optional[SourceLoc] = None,
        child_names: Tuple[str, ...] = (),
    ) -> None:
        self.system = system
        self.name = name
        self.index = index
        self.aliases: Tuple[str, ...] = ()
        self.type = ftype
        self.parent: Optional[Feature] = None
        self.children: Tuple[Feature, ...] = ()
        self.descendants: Tuple[int, ...] = ()
        self.scope = scope
        self.tier = tier
        self.loc = loc
        self.child_names = child_names

    @property
    def is_node(self) -> bool:
        """True for geometry class nodes (spec §4.3)."""
        return self.type.kind == NODE

    @property
    def is_plain(self) -> bool:
        """True if the value lives on the segment itself: segment scope and no tier (design §4.1)."""
        return self.scope == SEGMENT and self.tier is None

    def coerce(self, value: Union[str, int], loc: Optional[SourceLoc] = None) -> str:
        """Canonical value string of ``value`` for this feature, or a definition error (spec §6.2)."""
        return self.type.coerce(value, loc, self.name)

    def canonical(self) -> str:
        """The declaration line of this feature (spec §4.1, §11.3)."""
        if self.is_node:
            text = "%s Node(%s)" % (self.name, " ".join(self.child_names))
        else:
            text = "%s %s" % (self.name, self.type.canonical())
        if self.scope == SYLLABLE:
            text += " Scope(Syllable)"
        if self.tier is not None:
            text += " Tier(...)"
        return text

    def __repr__(self) -> str:
        return "<Feature %s #%d %s>" % (self.name, self.index, self.type.kind)


# --------------------------------------------------------------------------------------------
# Implications
# --------------------------------------------------------------------------------------------


class Implication:
    """A one-way implication ``S --> T`` (strong) or ``S ~~> T`` (weak) (spec §4.5; design §3).

    ``trigger`` and ``target`` are :class:`yasc.segment.SegmentSpec` objects. A strong
    implication applies ``target`` as output to every segment that matches ``trigger``; a
    weak one only fills unspecified features (``weak_apply``). ``trigger_features`` (filled
    in by :meth:`FeatureSystem.seal`) is the set of feature indices whose change re-runs the
    implication: the features the trigger reads, plus, for a weak implication, the features
    its target writes (design §13, decision 16). ``always`` is true for an empty trigger ``{}``.
    """

    __slots__ = ("trigger", "target", "weak", "loc", "trigger_features", "always", "origin")

    def __init__(self, trigger, target, weak: bool = False, loc: Optional[SourceLoc] = None, origin: Optional[str] = None) -> None:
        self.trigger = trigger
        self.target = target
        self.weak = weak
        self.loc = loc
        self.trigger_features: FrozenSet[int] = frozenset()
        self.always = not trigger.constraints
        self.origin = origin

    def apply(self, seg):
        """Apply this implication to one segment once; return the (possibly same) segment.

        If the trigger matches, the first environment it yields is used for the target's
        variables (design §13, decision 16).
        """
        envs = self.trigger.match(seg)
        if not envs:
            return seg
        if self.weak:
            return self.target.weak_apply(seg, envs[0])
        return self.target.apply(seg, envs[0])

    def canonical(self) -> str:
        """``S --> T`` or ``S ~~> T`` (spec §4.5, §11.3)."""
        return "%s %s %s" % (self.trigger.canonical(), "~~>" if self.weak else "-->", self.target.canonical())

    def __repr__(self) -> str:
        return "<Implication %s>" % self.canonical()


# --------------------------------------------------------------------------------------------
# The feature system
# --------------------------------------------------------------------------------------------


class FeatureSystem:
    """A set of features with geometry and implications: one ``Phonology`` (spec §4).

    Features, aliases, operations and implications are added first; :meth:`seal` then
    validates the geometry (daughters may be declared before or after their node), computes
    the implication triggers, and freezes the system. Segments can only be built from a
    sealed system; each system interns its own segments (design §4.1).
    """

    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name
        self._features: List[Feature] = []
        self._by_name: Dict[str, Feature] = {}
        self._implications: List[Implication] = []
        self._sealed = False
        self._segments: "WeakValueDictionary" = WeakValueDictionary()
        self._node_desc: Tuple[Optional[Tuple[int, ...]], ...] = ()
        self._leaves: Tuple[int, ...] = ()
        self._empty = None

    # -- properties -----------------------------------------------------------------------

    @property
    def sealed(self) -> bool:
        """True once :meth:`seal` has run (spec §4; design §3)."""
        return self._sealed

    @property
    def features(self) -> Tuple[Feature, ...]:
        """All features in declaration (index) order (design §3)."""
        return tuple(self._features)

    @property
    def by_name(self) -> Mapping[str, Feature]:
        """Name and alias -> feature (a copy; design §3)."""
        return dict(self._by_name)

    @property
    def implications(self) -> Tuple[Implication, ...]:
        """One-way implications in declaration order, bidirectional forms expanded (spec §4.5)."""
        return tuple(self._implications)

    @property
    def size(self) -> int:
        """The number of features, i.e. the width of a segment's value tuple (design §4.1)."""
        return len(self._features)

    @property
    def leaf_indices(self) -> Tuple[int, ...]:
        """Indices of all non-Node features (spec §4.3). Available after :meth:`seal`."""
        return self._leaves

    def __len__(self) -> int:
        return len(self._features)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self):
        return iter(tuple(self._features))

    # -- registration ---------------------------------------------------------------------

    def _check_open(self, what: str, loc: Optional[SourceLoc]) -> None:
        if self._sealed:
            raise YascDefinitionError("cannot %s: the feature system is sealed" % what, loc)

    def _check_new_name(self, name: str, loc: Optional[SourceLoc]) -> None:
        if not isinstance(name, str) or not _IDENT.match(name):
            raise YascDefinitionError(
                "invalid feature name %r" % (name,), loc,
                hint="feature names are ASCII letters, digits and _, starting with a letter",
            )
        if name in self._by_name:
            other = self._by_name[name]
            what = "feature" if other.name == name else "alias of %r" % other.name
            raise YascDefinitionError("%r is already declared (as a %s)" % (name, what), loc)

    def add_feature(
        self,
        name: str,
        ftype: FeatureType,
        *,
        aliases: Iterable[str] = (),
        children: Iterable[str] = (),
        scope: str = SEGMENT,
        tier: Optional[TierDecl] = None,
        loc: Optional[SourceLoc] = None,
    ) -> Feature:
        """Declare a feature (spec §4.1). Returns the new :class:`Feature`.

        ``children`` (Node types only) names the daughters, which may be declared before or
        after this call (spec §4.3); they are resolved by :meth:`seal`. ``scope`` is
        ``'segment'`` or ``'syllable'`` (spec §5.4); ``tier`` marks an autosegmental feature
        (spec §5.5).
        """
        self._check_open("add feature %r" % (name,), loc)
        self._check_new_name(name, loc)
        if not isinstance(ftype, FeatureType):
            raise TypeError("ftype must be a FeatureType, got %r" % (ftype,))
        if scope not in SCOPES:
            raise YascDefinitionError("unknown scope %r (use segment or syllable)" % (scope,), loc)
        child_names = tuple(children)
        if child_names and not ftype.is_node:
            raise YascDefinitionError("only a Node feature can have daughters (%r is %s)" % (name, ftype.kind), loc)
        if ftype.is_node and tier is not None:
            raise YascDefinitionError("a Node feature cannot be a tier feature", loc)
        if tier is not None and not isinstance(tier, TierDecl):
            raise TypeError("tier must be a TierDecl, got %r" % (tier,))
        feat = Feature(self, name, len(self._features), ftype, scope, tier, loc, child_names)
        self._features.append(feat)
        self._by_name[name] = feat
        if aliases:
            self.add_aliases(feat, *aliases, loc=loc)
        return feat

    def add_aliases(self, feature: Union[str, Feature], *aliases: str, loc: Optional[SourceLoc] = None) -> Feature:
        """Add ``== alias1 alias2`` names for a feature (spec §4.1)."""
        self._check_open("add aliases", loc)
        feat = self.resolve(feature, loc)
        for alias in aliases:
            self._check_new_name(alias, loc)
            self._by_name[alias] = feat
            feat.aliases = feat.aliases + (alias,)
        return feat

    def define_op(
        self,
        feature: Union[str, Feature],
        op: str,
        results: Sequence[Optional[Union[str, int]]],
        loc: Optional[SourceLoc] = None,
    ) -> Feature:
        """Define ``(op) r1 ... rN`` on a feature, one result per value; ``_`` = undefined (spec §4.1)."""
        self._check_open("define an operation", loc)
        feat = self.resolve(feature, loc)
        if feat.is_node:
            raise YascDefinitionError("Node feature %r has no values, so it cannot have operations" % feat.name, loc)
        try:
            feat.type = feat.type.with_op(op, results, loc)
        except YascDefinitionError as err:
            err.message = "%s: %s" % (feat.name, err.message)
            raise
        return feat

    def add_implication(self, trigger, target, *, weak: bool = False, loc: Optional[SourceLoc] = None,
                        origin: Optional[str] = None) -> Implication:
        """Declare ``trigger --> target`` (strong) or ``trigger ~~> target`` (weak) (spec §4.5).

        ``target`` is validated as an output spec (spec §6.2), and every variable it uses must
        be bound by ``trigger``.
        """
        self._check_open("add an implication", loc)
        for spec in (trigger, target):
            if getattr(spec, "system", None) is not self:
                raise YascDefinitionError("implication specs must belong to this feature system", loc)
        target.for_output(loc)
        unbound = sorted(target.var_names() - trigger.var_names())
        if unbound:
            raise YascDefinitionError(
                "variable%s %s in the implication target %s not bound by the trigger"
                % ("" if len(unbound) == 1 else "s", ", ".join(repr(u) for u in unbound),
                   "is" if len(unbound) == 1 else "are"),
                loc,
            )
        imp = Implication(trigger, target, weak, loc, origin)
        self._implications.append(imp)
        return imp

    def add_bidirectional(
        self,
        left,
        right,
        *,
        op: Optional[str] = None,
        forward_weak: bool = False,
        backward_weak: bool = False,
        loc: Optional[SourceLoc] = None,
    ) -> Tuple[Implication, Implication]:
        """Declare ``S <--> T`` and its variants; expand them into two one-way implications (spec §4.5).

        ``left`` is S and ``right`` is T. The right half of the arrow is ``S → T``
        (``forward_weak`` for ``~~>``), the left half is ``T → S`` (``backward_weak`` for
        ``<~~``). With ``op`` (``<--(op)-->``), every variable in each target gets ``op``
        applied as its outermost operation: ``S --> op(T)`` and ``T --> op(S)``. The forward
        implication is added first.
        """
        fwd_target = right.with_op(op, loc) if op is not None else right
        bwd_target = left.with_op(op, loc) if op is not None else left
        arrow = "%s%s%s" % ("<~~" if backward_weak else "<--", "(%s)" % op if op is not None else "",
                            "~~>" if forward_weak else "-->")
        origin = "%s %s %s" % (left.canonical(), arrow, right.canonical())
        first = self.add_implication(left, fwd_target, weak=forward_weak, loc=loc, origin=origin)
        second = self.add_implication(right, bwd_target, weak=backward_weak, loc=loc, origin=origin)
        return first, second

    # -- sealing --------------------------------------------------------------------------

    def seal(self) -> "FeatureSystem":
        """Validate the geometry and freeze the system (spec §4.3; design §3).

        Checks that every daughter exists, that no feature has two parents or is its own
        ancestor, then computes ``parent``, ``children``, ``descendants`` and the implication
        trigger sets. One problem raises :class:`YascDefinitionError`; several raise a
        :class:`YascLoadError` holding all of them. Sealing twice is a no-op.
        """
        if self._sealed:
            return self
        errors: List[YascError] = []
        parents: Dict[int, Feature] = {}
        kids: Dict[int, List[Feature]] = {}
        for node in self._features:
            if not node.is_node:
                continue
            kids[node.index] = []
            for cname in node.child_names:
                child = self._by_name.get(cname)
                if child is None:
                    errors.append(self._unknown(cname, node.loc, "daughter of %r" % node.name))
                    continue
                if child is node:
                    errors.append(YascDefinitionError("Node %r lists itself as a daughter" % node.name, node.loc))
                    continue
                if child in kids[node.index]:
                    errors.append(YascDefinitionError(
                        "Node %r lists daughter %r twice" % (node.name, child.name), node.loc))
                    continue
                prev = parents.get(child.index)
                if prev is not None:
                    errors.append(YascDefinitionError(
                        "feature %r has two parents: %r and %r" % (child.name, prev.name, node.name),
                        node.loc, hint="each feature may have at most one parent node (spec §4.3)"))
                    continue
                parents[child.index] = node
                kids[node.index].append(child)
        # Cycles: with at most one parent each, follow parent chains.
        reported = set()
        for feat in self._features:
            seen = [feat.index]
            cur = parents.get(feat.index)
            while cur is not None:
                if cur.index in seen:
                    cycle = seen[seen.index(cur.index):]
                    key = frozenset(cycle)
                    if key not in reported:
                        reported.add(key)
                        names = [self._features[i].name for i in cycle] + [cur.name]
                        errors.append(YascDefinitionError(
                            "feature geometry is cyclic: %s" % " -> ".join(reversed(names)), cur.loc))
                    break
                seen.append(cur.index)
                cur = parents.get(cur.index)
        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise YascLoadError(errors)
        for feat in self._features:
            feat.parent = parents.get(feat.index)
            feat.children = tuple(kids.get(feat.index, ()))
        for feat in self._features:
            if feat.is_node:
                feat.descendants = tuple(sorted(self._collect_leaves(feat)))
        self._node_desc = tuple(f.descendants if f.is_node else None for f in self._features)
        self._leaves = tuple(f.index for f in self._features if not f.is_node)
        for imp in self._implications:
            # Weak implications also re-fire when a target feature changes (it may have become
            # unspecified), which makes {S _F} --> {T} and S ~~> T equivalent (spec §4.5;
            # design §13, decision 16). Strong ones fire only on trigger features.
            feats = imp.trigger.features_read()
            if imp.weak:
                feats = feats | imp.target.features_read()
            imp.trigger_features = feats
        self._sealed = True
        return self

    @staticmethod
    def _collect_leaves(node: Feature) -> List[int]:
        out: List[int] = []
        stack = list(node.children)
        while stack:
            f = stack.pop()
            if f.is_node:
                stack.extend(f.children)
            else:
                out.append(f.index)
        return out

    # -- lookup ---------------------------------------------------------------------------

    def _unknown(self, name: str, loc: Optional[SourceLoc], role: str = "feature") -> YascDefinitionError:
        guess = _suggest(name, self._by_name)
        hint = None
        if guess is not None:
            real = self._by_name[guess].name
            hint = "did you mean %r?" % guess if real == guess else "did you mean %r (alias of %r)?" % (guess, real)
        what = "unknown feature %r" % name if role == "feature" else "unknown feature %r (%s)" % (name, role)
        return YascDefinitionError(what, loc, hint=hint)

    def feature(self, name: str, loc: Optional[SourceLoc] = None) -> Feature:
        """Resolve a feature name or alias (spec §6.2); unknown names raise with a hint."""
        feat = self._by_name.get(name)
        if feat is None:
            raise self._unknown(name, loc)
        return feat

    def get(self, name: str) -> Optional[Feature]:
        """The feature called ``name`` (or with that alias), or ``None`` (spec §4.1)."""
        return self._by_name.get(name)

    def resolve(self, feature: Union[str, int, Feature], loc: Optional[SourceLoc] = None) -> Feature:
        """Accept a name, an alias, an index or a :class:`Feature` of this system (spec §6.2)."""
        if isinstance(feature, Feature):
            if feature.system is not self:
                raise YascDefinitionError("feature %r belongs to another feature system" % feature.name, loc)
            return feature
        if isinstance(feature, int) and not isinstance(feature, bool):
            return self._features[feature]
        return self.feature(feature, loc)

    def index(self, name: str, loc: Optional[SourceLoc] = None) -> int:
        """The index of a feature name or alias (spec §4.1)."""
        return self.feature(name, loc).index

    def is_node(self, index: int) -> bool:
        """True if feature ``index`` is a Node (spec §4.3)."""
        return self._features[index].is_node

    # -- operations -----------------------------------------------------------------------

    def apply_op(self, feature: Union[str, int, Feature], op: str, value: Optional[str]) -> Optional[str]:
        """Apply operation ``op`` of a feature to ``value``; ``None`` if undefined (spec §4.4)."""
        return self.resolve(feature).type.apply(op, value)

    def preimages(self, feature: Union[str, int, Feature], op: str, value: str) -> Tuple[str, ...]:
        """All values of a feature that ``op`` maps to ``value`` (spec §6.2 ``op(a)F``)."""
        return self.resolve(feature).type.preimages(op, value)

    # -- segments -------------------------------------------------------------------------

    def _require_sealed(self) -> None:
        if not self._sealed:
            raise YascDefinitionError("the feature system must be sealed before segments are built")

    @property
    def empty(self):
        """The segment with every feature unspecified (spec §4.2)."""
        if self._empty is None:
            from .segment import Segment

            self._require_sealed()
            self._empty = Segment.make(self, (None,) * len(self._features))
        return self._empty

    def segment(self, values: Union[None, str, Mapping] = None, **named: Union[str, int]):
        """Build a validated segment from ``{feature: value}`` (names, aliases, indices or
        :class:`Feature` keys); keyword arguments also work: ``fs.segment(Syll='+')``.
        ``values`` may also be a bundle string such as ``"{+Syll +High [H]Tone _Voice}"``
        (spec §11.2; P10, design §13 entry 170).

        Node features cannot be given values (spec §4.3). No implications are applied; use
        :meth:`close_new` for that.
        """
        from .segment import Segment

        self._require_sealed()
        if isinstance(values, str):
            values = dict(self._parse_bundle(values))
        vals: List[Optional[str]] = [None] * len(self._features)
        items = list((values or {}).items()) + list(named.items())
        for key, value in items:
            feat = self.resolve(key)
            if feat.is_node:
                raise YascDefinitionError(
                    "Node feature %r has no value of its own; specify its daughters" % feat.name)
            vals[feat.index] = None if value is None else feat.coerce(value)
        return Segment.make(self, tuple(vals))

    def _parse_bundle(self, text: str) -> List[Tuple["Feature", Optional[str]]]:
        """Read a bundle string ``{+Syll 2Stress [H]Tone !Lab Lab _Voice}`` into
        ``(feature, value)`` pairs for :meth:`segment` (spec §6.2 value constraints and
        §11.2; P10, design §13 entry 170). Braces are optional; ``_F`` leaves F unspecified
        and a bare name is a Unary feature's ``!``."""
        import re
        body = text.strip()
        if body.startswith("{") or body.endswith("}"):
            if not (body.startswith("{") and body.endswith("}")):
                raise YascDefinitionError("unbalanced braces in segment %r" % text)
            body = body[1:-1]
        out: List[Tuple[Feature, Optional[str]]] = []
        for tok in body.split():
            m = re.match(r"^(_|\[[A-Za-z][A-Za-z0-9]*\]|[^A-Za-z_\[\]{}()#\s]*)([A-Za-z][A-Za-z0-9_]*)$", tok)
            if m is None:
                raise YascDefinitionError("cannot read %r in segment %r" % (tok, text),
                                          hint="write vF (+F, 2F, !F), [X]F, _F, or a bare Unary F")
            val, name = m.groups()
            feat = self.resolve(name)
            if val == "_":
                value: Optional[str] = None
            elif val == "":
                if feat.type.kind != UNARY:
                    raise YascDefinitionError("feature %r needs a value in segment %r" % (feat.name, text))
                value = PRESENT
            else:
                value = val[1:-1] if val.startswith("[") else val
            out.append((feat, value))
        return out

    # -- implication closure --------------------------------------------------------------

    def close(self, seg, changed: Iterable[int], loc: Optional[SourceLoc] = None):
        """Apply implications after ``changed`` features of ``seg`` changed (spec §4.5; design §3).

        Keeps a work-set of changed feature indices. Each pass runs the implications in
        declaration order, considering only those whose trigger features intersect the
        features changed before the pass or earlier in the same pass. Features changed in a
        pass form the work-set of the next pass; the loop stops at a fixed point. After
        ``4 × len(implications)`` passes without one, :class:`YascRuntimeError` is raised.
        Returns the resulting segment (``seg`` itself when nothing changed).
        """
        imps = self._implications
        if not imps:
            return seg
        pending = changed if isinstance(changed, (set, frozenset)) else set(changed)
        if not pending:
            return seg
        limit = 4 * len(imps)
        passes = 0
        while pending:
            passes += 1
            if passes > limit:
                raise self._cycle_error(seg, fired, limit, loc)  # noqa: F821 - set in the loop
            fresh: set = set()
            fired: List[Implication] = []
            for imp in imps:
                if not imp.always:
                    tf = imp.trigger_features
                    if tf.isdisjoint(pending) and tf.isdisjoint(fresh):
                        continue
                new = imp.apply(seg)
                if new is not seg and new != seg:
                    fresh.update(seg.diff(new))
                    fired.append(imp)
                    seg = new
            pending = fresh
        return seg

    #: Plan P1 name for :meth:`close`.
    apply_implications = close

    def close_new(self, seg, loc: Optional[SourceLoc] = None):
        """Apply implications to a freshly built segment, all features counting as changed
        (spec §4.5 "input segments produced by the orthography also get implications")."""
        self._require_sealed()
        return self.close(seg, frozenset(range(len(self._features))), loc)

    def _cycle_error(self, seg, fired: List[Implication], limit: int, loc: Optional[SourceLoc]) -> YascRuntimeError:
        shown = "; ".join(imp.canonical() for imp in fired[:4]) or "?"
        return YascRuntimeError(
            "implication cycle: no fixed point after %d passes on %r" % (limit, seg),
            loc if loc is not None else (fired[0].loc if fired else None),
            hint="implications still changing the segment: %s" % shown,
        )

    def canonical(self) -> str:
        """The system as ``Phonology`` declaration lines (spec §4, §11.3)."""
        lines = []
        for f in self._features:
            if f.scope == ROLE:
                continue  # implicit role pseudo-features (spec §5.4) are not declared by the user
            lines.append(f.canonical())
            if f.aliases:
                lines.append("  == " + " ".join(f.aliases))
            builtin = FeatureType(f.type.kind, f.type.values).ops if not f.is_node else {}
            for op, res in f.type.ops.items():
                if builtin.get(op) != res:
                    lines.append("  (%s) %s" % (op, " ".join(UNDEFINED if r is None else format_value(r) for r in res)))
        for imp in self._implications:
            lines.append(imp.canonical())
        return "\n".join(lines)

    def __repr__(self) -> str:
        state = "sealed" if self._sealed else "open"
        return "<FeatureSystem %s%d features, %d implications, %s>" % (
            (self.name + ": ") if self.name else "", len(self._features), len(self._implications), state)
