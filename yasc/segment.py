"""Segments, segment specs, constraints and binding environments (spec §4.2, §4.3, §6.2;
design §4.1, §4.2).

* :class:`Segment` — an immutable, interned feature bundle; values are a tuple indexed by
  feature (``None`` = unspecified).
* :class:`SegView` — what a spec reads from: ``view[i]`` (raw leaf value), ``get``,
  ``present`` and ``segment``. :class:`Segment` itself and :class:`PlainView` implement it;
  Form-backed views (syllable and tier features, plan P7/P8) will too.
* Constraints (:class:`Eq`, :class:`In`, :class:`Absent`, :class:`Cmp`, :class:`Var`,
  :class:`OpVar`, :class:`WeakVar`) — one per row of the spec §6.2 table, each with match and
  (where legal) output semantics.
* :class:`SegmentSpec` — a compiled ``{...}``: ``match`` / ``apply`` / ``weak_apply``.
* :class:`Env` — a persistent binding environment.
"""

from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:  # Python >= 3.8
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

from .errors import SourceLoc, YascDefinitionError, YascRuntimeError
from .features import (
    BINARY,
    NODE,
    PRESENT,
    SCALAR,
    UNSPEC,
    BundleValue,
    Feature,
    FeatureSystem,
    format_value,
)

__all__ = [
    "UNSPEC",
    "PRESENT",
    "BundleValue",
    "Segment",
    "SegView",
    "PlainView",
    "Env",
    "EMPTY",
    "Constraint",
    "Eq",
    "In",
    "Absent",
    "Cmp",
    "Var",
    "OpVar",
    "WeakVar",
    "SegmentSpec",
]

# --------------------------------------------------------------------------------------------
# Segment
# --------------------------------------------------------------------------------------------


class Segment:
    """An immutable feature bundle (spec §5.1 item 1; design §4.1).

    ``values`` has one slot per feature index; ``None`` means unspecified, and Node slots are
    always ``None`` (nodes store nothing, spec §4.3). Build segments with :meth:`make` (fast,
    trusted values) or :meth:`FeatureSystem.segment` (validated). Equal segments of one
    system are normally the same object (interning), and the hash is cached.

    A segment is also a :class:`SegView` of itself.
    """

    __slots__ = ("system", "values", "_hash", "__weakref__")

    def __init__(self, system: FeatureSystem, values: Tuple[Optional[str], ...]) -> None:
        self.system = system
        self.values = values
        self._hash = hash(values)

    @staticmethod
    def make(system: FeatureSystem, values: Sequence[Optional[str]]) -> "Segment":
        """Return the interned segment with these values (design §4.1).

        ``values`` must already be canonical (as produced by specs and
        :meth:`FeatureSystem.segment`); only the width is checked. The system must be sealed.
        """
        if type(values) is not tuple:
            values = tuple(values)
        table = system._segments
        seg = table.get(values)
        if seg is not None:
            return seg
        if not system._sealed:
            raise YascDefinitionError("the feature system must be sealed before segments are built")
        if len(values) != len(system._features):
            raise ValueError("segment has %d values but the system has %d features"
                             % (len(values), len(system._features)))
        seg = Segment(system, values)
        table[values] = seg
        return seg

    # -- SegView protocol -----------------------------------------------------------------

    @property
    def segment(self) -> "Segment":
        """The segment itself (:class:`SegView` protocol)."""
        return self

    def __getitem__(self, index: int) -> Optional[str]:
        """Raw stored value of feature ``index`` (``None`` for Node slots) (design §4.1)."""
        return self.values[index]

    def get(self, feature: Union[int, str, Feature]) -> Optional[str]:
        """The value of a feature (spec §4.2); for a Node, ``'!'`` if present else ``None``
        (spec §4.3 rule 2). Accepts an index, a name or alias, or a :class:`Feature`."""
        i = feature if type(feature) is int else self.system.resolve(feature).index
        desc = self.system._node_desc[i]
        if desc is None:
            return self.values[i]
        vals = self.values
        for d in desc:
            if vals[d] is not None:
                return PRESENT
        return None

    def present(self, feature: Union[int, str, Feature]) -> bool:
        """Node presence (spec §4.3 rule 2); for a leaf, whether it is specified."""
        return self.get(feature) is not None

    def bundle(self, node: Union[int, str, Feature]) -> BundleValue:
        """The :class:`BundleValue` of a Node's descendants, unspecified ones included (spec §4.3)."""
        feat = self.system.resolve(node)
        if not feat.is_node:
            raise YascDefinitionError("%r is not a Node feature" % feat.name)
        vals = self.values
        return BundleValue([(d, vals[d]) for d in feat.descendants])

    # -- derived segments -----------------------------------------------------------------

    def replace(self, updates: Mapping[Union[int, str, Feature], Optional[Union[str, int]]]) -> "Segment":
        """A copy with some features set (validated) or unset (``None``) (spec §6.2 outputs)."""
        vals = list(self.values)
        for key, value in updates.items():
            feat = self.system.resolve(key)
            if feat.is_node:
                if value is not None:
                    raise YascDefinitionError("Node feature %r can only be unset (None)" % feat.name)
                for d in feat.descendants:
                    vals[d] = None
                continue
            vals[feat.index] = None if value is None else feat.coerce(value)
        return Segment.make(self.system, tuple(vals))

    def merge(self, other: "Segment") -> "Segment":
        """``other``'s specified features win, the rest of ``self`` is kept (spec §5.6 step 3,
        §8.2.4 ``[x]`` items)."""
        vals = tuple(o if o is not None else s for s, o in zip(self.values, other.values))
        return Segment.make(self.system, vals)

    def diff(self, other: "Segment") -> FrozenSet[int]:
        """Indices of the features whose values differ between two segments (spec §4.5)."""
        if other is self:
            return frozenset()
        return frozenset(i for i, (a, b) in enumerate(zip(self.values, other.values)) if a != b)

    def to_dict(self) -> Dict[str, str]:
        """``{feature name: value}`` for the specified leaf features, in index order."""
        feats = self.system._features
        return {feats[i].name: v for i, v in enumerate(self.values) if v is not None}

    # -- value semantics ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, Segment):
            return NotImplemented
        return self.system is other.system and self.values == other.values

    def __ne__(self, other: object) -> bool:
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __hash__(self) -> int:
        return self._hash

    def canonical(self) -> str:
        """``{+Syll -Voice !Labial}``: specified leaves in index order (spec §6.2, §10.5 ``%S``)."""
        feats = self.system._features
        return "{" + " ".join(format_value(v) + feats[i].name for i, v in enumerate(self.values) if v is not None) + "}"

    __repr__ = canonical

    def __reduce__(self):
        return (Segment.make, (self.system, self.values))


# --------------------------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------------------------


class SegView(Protocol):
    """What a :class:`SegmentSpec` reads from (design §4.1).

    * ``view[i]`` — the raw value of leaf feature ``i`` (``None`` = unspecified). Specs read
      Node presence and bundles through the descendants with this operator.
    * ``get(feature)`` / ``present(feature)`` — as on :class:`Segment`.
    * ``segment`` — the underlying :class:`Segment`; specs that only mention plain
      (segment-scope, non-tier) features read ``view.segment.values`` directly (fast path).

    Form-backed views (plan P7/P8) resolve syllable-scope and tier features in ``__getitem__``.
    """

    segment: Segment

    def __getitem__(self, index: int) -> Optional[str]: ...

    def get(self, feature: Union[int, str, Feature]) -> Optional[str]:
        """Value of a feature; ``'!'``/``None`` presence for a Node (spec §4.3)."""
        ...

    def present(self, feature: Union[int, str, Feature]) -> bool:
        """Node presence, or whether a leaf is specified (spec §4.3 rule 2)."""
        ...


class PlainView:
    """A :class:`SegView` over a bare :class:`Segment`: every feature is read from it (design §4.1)."""

    __slots__ = ("segment",)

    def __init__(self, segment: Segment) -> None:
        self.segment = segment

    def __getitem__(self, index: int) -> Optional[str]:
        return self.segment.values[index]

    def get(self, feature: Union[int, str, Feature]) -> Optional[str]:
        """Value of a feature, node presence for Nodes (spec §4.3)."""
        return self.segment.get(feature)

    def present(self, feature: Union[int, str, Feature]) -> bool:
        """Node presence (spec §4.3 rule 2)."""
        return self.segment.present(feature)

    def __repr__(self) -> str:
        return "PlainView(%r)" % (self.segment,)


# --------------------------------------------------------------------------------------------
# Environments
# --------------------------------------------------------------------------------------------


def _assoc(items: Tuple[Tuple[Any, Any], ...], key: Any, value: Any) -> Tuple[Tuple[Any, Any], ...]:
    """Sorted-tuple association: return ``items`` with ``key`` set to ``value``."""
    for n, (k, v) in enumerate(items):
        if k == key:
            if v is value or (type(v) is type(value) and v == value):
                return items
            return items[:n] + ((key, value),) + items[n + 1:]
        if key < k:
            return items[:n] + ((key, value),) + items[n:]
    return items + ((key, value),)


def _lookup(items: Tuple[Tuple[Any, Any], ...], key: Any) -> Any:
    for k, v in items:
        if k == key:
            return v
    return None


class Env:
    """A persistent, immutable binding environment (spec §6.3; design §4.2).

    Four namespaces, each a sorted tuple of ``(key, value)`` pairs:

    * ``vars``: variable name -> value string, :class:`BundleValue` or :data:`UNSPEC`;
    * ``caps``: capture number -> span ``(i, j)`` (plan P2);
    * ``alts``: disjunction id -> alternative index (plan P2);
    * ``autos``: capture name -> autosegment id (plan P8).

    Keys within one namespace must be mutually orderable. Bound values are never ``None``,
    so the lookups return ``None`` for "unbound". Binding returns a new ``Env`` (or ``self``
    when nothing changes). The hash is cached; equal environments compare equal.
    """

    __slots__ = ("_vars", "_caps", "_alts", "_autos", "_hash")

    def __init__(self, vars: Tuple = (), caps: Tuple = (), alts: Tuple = (), autos: Tuple = ()) -> None:
        self._vars = vars
        self._caps = caps
        self._alts = alts
        self._autos = autos
        self._hash = hash((vars, caps, alts, autos))

    @classmethod
    def of(cls, vars: Optional[Mapping[str, Any]] = None, caps: Optional[Mapping[int, Any]] = None,
           alts: Optional[Mapping[Any, int]] = None, autos: Optional[Mapping[str, Any]] = None) -> "Env":
        """Build an environment from plain mappings (tests and tools; design §4.2)."""
        def norm(m):
            return tuple(sorted((m or {}).items()))
        return cls(norm(vars), norm(caps), norm(alts), norm(autos))

    # -- variables ------------------------------------------------------------------------

    def var(self, name: str) -> Any:
        """The value bound to variable ``name``, or ``None`` if unbound (spec §6.2)."""
        for k, v in self._vars:
            if k == name:
                return v
        return None

    def has_var(self, name: str) -> bool:
        """True if variable ``name`` is bound (spec §6.3)."""
        return self.var(name) is not None

    def bind_var(self, name: str, value: Any) -> "Env":
        """Return an environment with ``name`` bound to ``value`` (replacing any binding)."""
        if value is None:
            raise ValueError("variables cannot be bound to None; use UNSPEC for 'unspecified'")
        new = _assoc(self._vars, name, value)
        return self if new is self._vars else Env(new, self._caps, self._alts, self._autos)

    def unify_var(self, name: str, value: Any) -> Optional["Env"]:
        """Bind ``name`` if unbound; return ``self`` if already bound to ``value``; else ``None``
        (spec §6.3 "later occurrences must agree")."""
        bound = self.var(name)
        if bound is None:
            return self.bind_var(name, value)
        return self if bound == value else None

    def agrees_op(self, name: str, ftype, ops: Sequence[str], value: Optional[str]) -> bool:
        """True if ``name`` is bound and ``op1#...(bound) == value`` for a feature type (spec §4.4, §6.3)."""
        bound = self.var(name)
        if bound is None or bound is UNSPEC or type(bound) is BundleValue:
            return False
        forward = ftype.compose(ops)[0]
        return bound in forward and forward[bound] == value

    # -- other namespaces -----------------------------------------------------------------

    def cap(self, n: int) -> Any:
        """The span captured by LHS item ``n``, or ``None`` (spec §6.4; plan P2)."""
        return _lookup(self._caps, n)

    def bind_cap(self, n: int, span: Any) -> "Env":
        """Return an environment with capture ``n`` set to ``span`` (spec §6.4)."""
        new = _assoc(self._caps, n, span)
        return self if new is self._caps else Env(self._vars, new, self._alts, self._autos)

    def alt(self, key: Any) -> Optional[int]:
        """The alternative index recorded for disjunction ``key``, or ``None`` (spec §6.1, §8.2.4)."""
        return _lookup(self._alts, key)

    def bind_alt(self, key: Any, index: int) -> "Env":
        """Return an environment recording alternative ``index`` for disjunction ``key``."""
        new = _assoc(self._alts, key, index)
        return self if new is self._alts else Env(self._vars, self._caps, new, self._autos)

    def auto(self, name: str) -> Any:
        """The autosegment captured as ``=name``, or ``None`` (spec §6.5; plan P8)."""
        return _lookup(self._autos, name)

    def bind_auto(self, name: str, auto_id: Any) -> "Env":
        """Return an environment with autosegment capture ``name`` set (spec §6.5)."""
        new = _assoc(self._autos, name, auto_id)
        return self if new is self._autos else Env(self._vars, self._caps, self._alts, new)

    # -- inspection -----------------------------------------------------------------------

    @property
    def vars(self) -> Dict[str, Any]:
        """A dict copy of the variable bindings."""
        return dict(self._vars)

    @property
    def caps(self) -> Dict[int, Any]:
        """A dict copy of the captures."""
        return dict(self._caps)

    @property
    def alts(self) -> Dict[Any, int]:
        """A dict copy of the disjunction indices."""
        return dict(self._alts)

    @property
    def autos(self) -> Dict[str, Any]:
        """A dict copy of the autosegment captures."""
        return dict(self._autos)

    @property
    def is_empty(self) -> bool:
        """True if nothing is bound in any namespace."""
        return not (self._vars or self._caps or self._alts or self._autos)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, Env):
            return NotImplemented
        return (self._hash == other._hash and self._vars == other._vars and self._caps == other._caps
                and self._alts == other._alts and self._autos == other._autos)

    def __ne__(self, other: object) -> bool:
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        parts = []
        for label, items in (("vars", self._vars), ("caps", self._caps), ("alts", self._alts), ("autos", self._autos)):
            if items:
                parts.append("%s=%r" % (label, dict(items)))
        return "Env(%s)" % ", ".join(parts)


#: The empty environment.
EMPTY = Env()


# --------------------------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------------------------


def _present(vals, desc: Tuple[int, ...]) -> bool:
    for d in desc:
        if vals[d] is not None:
            return True
    return False


class Constraint:
    """One constraint of a segment spec, on one feature (spec §6.2).

    Subclasses implement ``test(vals)`` (variable-free) or ``extend(vals, env)`` (variables;
    returns a tuple of environments, empty on failure), ``write(vals, env)`` (output
    semantics on a mutable value list) and ``weak_write`` (fill only unspecified).
    ``vals`` is a segment's value tuple or a :class:`SegView`; only ``vals[i]`` is used.
    """

    __slots__ = ("feature", "index", "loc", "node")

    #: True for constraints that read or bind variables.
    is_var = False

    def __init__(self, feature: Feature, loc: Optional[SourceLoc] = None) -> None:
        if not isinstance(feature, Feature):
            raise TypeError("constraints take a resolved Feature, got %r" % (feature,))
        self.feature = feature
        self.index = feature.index
        self.loc = loc
        self.node = feature.type.kind == NODE

    def features_read(self) -> Tuple[int, ...]:
        """Feature indices this constraint reads: its own, plus a Node's descendants (spec §4.3)."""
        return (self.index,) + self.feature.descendants

    def var_names(self) -> Tuple[str, ...]:
        """Variables used by this constraint (spec §6.2)."""
        return ()

    def check_output(self, loc: Optional[SourceLoc] = None) -> None:
        """Raise :class:`YascDefinitionError` if this constraint is illegal in an output (spec §6.2)."""

    def with_outer_op(self, op: str, loc: Optional[SourceLoc] = None) -> "Constraint":
        """This constraint with ``op`` applied to its variable value (spec §4.5 ``<--(op)-->``)."""
        return self

    def _unset(self, vals: List[Optional[str]]) -> None:
        if self.node:
            for d in self.feature.descendants:
                vals[d] = None
        else:
            vals[self.index] = None

    def _is_unspecified(self, vals: List[Optional[str]]) -> bool:
        if self.node:
            return not _present(vals, self.feature.descendants)
        return vals[self.index] is None

    def weak_write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Weak output (``~{...}``, ``~~>``): write only if the feature is unspecified (spec §4.5, §8.2.4)."""
        if self._is_unspecified(vals):
            self.write(vals, env)

    def canonical(self) -> str:  # pragma: no cover - overridden
        """The constraint as written inside ``{...}`` (spec §6.2, §11.3)."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return "<%s %s>" % (type(self).__name__, self.canonical())


class Eq(Constraint):
    """``vF`` — match: F = v; output: set F := v (spec §6.2 row 1).

    On a Node only ``!N`` is allowed: it matches when the node is present (spec §4.3) and is
    illegal as an output (design §13, decision 12).
    """

    __slots__ = ("value",)

    def __init__(self, feature: Feature, value: Union[str, int] = PRESENT, loc: Optional[SourceLoc] = None) -> None:
        super().__init__(feature, loc)
        if self.node:
            if value != PRESENT:
                raise YascDefinitionError(
                    "Node feature %r can only be written !%s (present) or _%s (absent)"
                    % (feature.name, feature.name, feature.name), loc)
            self.value = PRESENT
        else:
            self.value = feature.coerce(value, loc)

    def test(self, vals) -> bool:
        """F = v; for a Node, some descendant is specified (spec §4.3 rule 2)."""
        if self.node:
            return _present(vals, self.feature.descendants)
        return vals[self.index] == self.value

    def check_output(self, loc: Optional[SourceLoc] = None) -> None:
        """``!N`` on a Node is illegal as an output (spec §4.3; design §13, decision 12)."""
        if self.node:
            raise YascDefinitionError(
                "!%s cannot be an output: a node becomes present only through its daughters"
                % self.feature.name, self.loc or loc,
                hint="write a daughter value, e.g. {!Labial}, or bind the node: {(p)%s}" % self.feature.name)

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Set F := v (spec §6.2)."""
        if self.node:
            self.check_output()
        vals[self.index] = self.value

    def canonical(self) -> str:
        """``+High``, ``!Labial``, ``2Stress``, ``[H]Tone`` (spec §6.2)."""
        return format_value(self.value) + self.feature.name


class Absent(Constraint):
    """``_F`` — match: F unspecified (a Node: no descendant specified); output: unset F, and
    for a Node delink the whole subtree (spec §6.2 row 2, §4.3 rule 3)."""

    __slots__ = ()

    def test(self, vals) -> bool:
        """F is unspecified (spec §4.2)."""
        if self.node:
            return not _present(vals, self.feature.descendants)
        return vals[self.index] is None

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Unset F / delink the subtree (spec §4.3 rule 3)."""
        self._unset(vals)

    def weak_write(self, vals: List[Optional[str]], env: "Env") -> None:
        """A weak output never removes anything (design §13, decision 14)."""

    def canonical(self) -> str:
        """``_F`` (spec §6.2)."""
        return "_" + self.feature.name


class In(Constraint):
    """``{v1 v2}F`` — match: F ∈ {v1, v2}; illegal as an output (spec §6.2 row 3)."""

    __slots__ = ("values", "_set")

    def __init__(self, feature: Feature, values: Iterable[Union[str, int]], loc: Optional[SourceLoc] = None) -> None:
        super().__init__(feature, loc)
        if self.node:
            raise YascDefinitionError("a value set {...}%s is not allowed on a Node feature" % feature.name, loc)
        given = set(feature.coerce(v, loc) for v in values)
        if not given:
            raise YascDefinitionError("an empty value set {}%s never matches" % feature.name, loc)
        self.values = tuple(v for v in feature.type.values if v in given)
        self._set = frozenset(given)

    def test(self, vals) -> bool:
        """F is one of the listed values (spec §6.2)."""
        return vals[self.index] in self._set

    def check_output(self, loc: Optional[SourceLoc] = None) -> None:
        """Always raises: ``{v1 v2}F`` has no output meaning (spec §6.2 row 3)."""
        raise YascDefinitionError(
            "a value set {...}%s cannot be an output: it does not say which value to set"
            % self.feature.name, self.loc or loc)

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Always raises (spec §6.2 row 3); :meth:`SegmentSpec.for_output` catches this earlier."""
        self.check_output()

    def canonical(self) -> str:
        """``{v1 v2}F`` with the values in declaration order (spec §6.2)."""
        return "{%s}%s" % (" ".join(format_value(v) for v in self.values), self.feature.name)


class Cmp(Constraint):
    """``>n F``, ``<n F``, ``>=n F``, ``<=n F`` — Scalar comparison; illegal as an output
    (spec §6.2 row 4). The set of satisfying values is precomputed."""

    __slots__ = ("op", "n", "_allowed")

    _OPS = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
    }

    def __init__(self, feature: Feature, op: str, n: int, loc: Optional[SourceLoc] = None) -> None:
        super().__init__(feature, loc)
        if feature.type.kind != SCALAR:
            raise YascDefinitionError(
                "comparison %s%s%s needs a Scalar feature (%r is %s)"
                % (op, n, feature.name, feature.name, feature.type.kind), loc)
        if op not in self._OPS:
            raise YascDefinitionError("unknown comparison %r (use > < >= <=)" % (op,), loc)
        if isinstance(n, bool) or not isinstance(n, int):
            try:
                n = int(n)
            except (TypeError, ValueError):
                raise YascDefinitionError("comparison needs an integer, got %r" % (n,), loc) from None
        self.op = op
        self.n = n
        fn = self._OPS[op]
        self._allowed = frozenset(v for v in feature.type.values if fn(int(v), n))

    def test(self, vals) -> bool:
        """F is specified and compares as required (spec §6.2)."""
        return vals[self.index] in self._allowed

    def check_output(self, loc: Optional[SourceLoc] = None) -> None:
        """Always raises: comparisons have no output meaning (spec §6.2 row 4)."""
        raise YascDefinitionError("a comparison %s%s%s cannot be an output"
                                  % (self.op, self.n, self.feature.name), self.loc or loc)

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Always raises (spec §6.2 row 4); :meth:`SegmentSpec.for_output` catches this earlier."""
        self.check_output()

    def canonical(self) -> str:
        """``>=2Stress`` (spec §6.2; the optional space is dropped)."""
        return "%s%d%s" % (self.op, self.n, self.feature.name)


class _VarBase(Constraint):
    """Shared code of the variable constraints (spec §6.2)."""

    __slots__ = ("name",)
    is_var = True

    def __init__(self, feature: Feature, name: str, loc: Optional[SourceLoc] = None) -> None:
        super().__init__(feature, loc)
        if not isinstance(name, str) or not name:
            raise YascDefinitionError("invalid variable name %r" % (name,), loc)
        self.name = name

    def var_names(self) -> Tuple[str, ...]:
        return (self.name,)

    def _current(self, vals) -> Any:
        """The feature's value as a variable value: a string, a BundleValue, or None."""
        if self.node:
            desc = self.feature.descendants
            if not _present(vals, desc):
                return None
            return BundleValue([(d, vals[d]) for d in desc])
        return vals[self.index]

    def _bound(self, env: "Env") -> Any:
        b = env.var(self.name)
        if b is None:
            raise YascRuntimeError("variable %r is unbound in an output (%s)" % (self.name, self.canonical()), self.loc)
        return b

    def _write_value(self, vals: List[Optional[str]], b: Any) -> None:
        """Write a bound variable value into the feature (spec §6.2, §4.3 rule 4)."""
        if b is UNSPEC:
            self._unset(vals)
            return
        if self.node:
            desc = self.feature.descendants
            if type(b) is not BundleValue or b.indices != desc:
                raise YascRuntimeError(
                    "variable %r holds %s, which is not a %s sub-bundle"
                    % (self.name, b.format(self.feature.system) if type(b) is BundleValue else repr(b),
                       self.feature.name), self.loc)
            for d, v in b:
                vals[d] = v
            return
        if not self.feature.type.has_value(b):
            raise YascRuntimeError(
                "variable %r holds %r, which is not a value of %r"
                % (self.name, b, self.feature.name), self.loc,
                hint="a variable may be shared only by features with identical value sets, or Binary ones (spec §6.2)")
        vals[self.index] = b


class Var(_VarBase):
    """``(a)F`` — match: F is specified; bind ``a`` or check it against the binding; output:
    set F := a (spec §6.2 row 5). On a Node, ``a`` is the :class:`BundleValue` of the subtree
    (spec §4.3 rule 4). ``Var(f, 'a', ops)`` with non-empty ``ops`` builds an :class:`OpVar`.
    """

    __slots__ = ()

    def __new__(cls, feature: Feature, name: str, ops: Sequence[str] = (), loc: Optional[SourceLoc] = None):
        if cls is Var and ops:
            cls = OpVar
        return object.__new__(cls)

    def __init__(self, feature: Feature, name: str, ops: Sequence[str] = (), loc: Optional[SourceLoc] = None) -> None:
        super().__init__(feature, name, loc)

    def extend(self, vals, env: "Env") -> Tuple["Env", ...]:
        """Bind or check ``a`` against F's value (spec §6.3)."""
        v = self._current(vals)
        if v is None:
            return ()
        for k, b in env._vars:
            if k == self.name:
                return (env,) if b == v else ()
        return (env.bind_var(self.name, v),)

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Set F := a; ``UNSPEC`` unsets; a bundle replaces a Node's subtree (spec §6.2)."""
        self._write_value(vals, self._bound(env))

    def with_outer_op(self, op: str, loc: Optional[SourceLoc] = None) -> Constraint:
        """``(a)F`` becomes ``op(a)F`` (spec §4.5 ``<--(op)-->``)."""
        return OpVar(self.feature, self.name, (op,), loc or self.loc)

    def canonical(self) -> str:
        """``(a)F`` (spec §6.2)."""
        return "(%s)%s" % (self.name, self.feature.name)


class OpVar(Var):
    """``op1#op2(a)F`` — match: F = op1(op2(a)); an unbound ``a`` is bound to each preimage
    in turn; output: set F := op(a), or unset F if the result is undefined (spec §6.2 row 7,
    §4.4). The forward and inverse tables are precomputed at construction."""

    __slots__ = ("ops", "_forward", "_inverse")

    def __init__(self, feature: Feature, name: str, ops: Sequence[str] = (), loc: Optional[SourceLoc] = None) -> None:
        super().__init__(feature, name, (), loc)
        if not ops:
            raise YascDefinitionError("OpVar needs at least one operation", loc)
        if self.node:
            raise YascDefinitionError("operations cannot apply to Node feature %r" % feature.name, loc)
        norm = tuple(o[1:-1] if len(o) > 2 and o[0] == "(" and o[-1] == ")" else o for o in ops)
        try:
            self._forward, self._inverse = feature.type.compose(norm, feature.name)
        except YascDefinitionError as err:
            err.loc = err.loc or loc
            raise
        self.ops = norm

    def extend(self, vals, env: "Env") -> Tuple["Env", ...]:
        """Check F = op(a), or bind ``a`` to every preimage of F's value (spec §6.2)."""
        v = vals[self.index]
        if v is None:
            return ()
        b = env.var(self.name)
        if b is None:
            pre = self._inverse.get(v)
            if not pre:
                return ()
            return tuple(env.bind_var(self.name, x) for x in pre)
        try:
            r = self._forward.get(b)
        except TypeError:
            return ()
        return (env,) if r is not None and r == v else ()

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Set F := op(a); an undefined result or ``UNSPEC`` unsets F (spec §6.2, §4.1)."""
        b = self._bound(env)
        if b is UNSPEC:
            vals[self.index] = None
            return
        if type(b) is BundleValue or b not in self._forward:
            raise YascRuntimeError(
                "variable %r holds %r, which is not a value of %r" % (self.name, b, self.feature.name), self.loc,
                hint="a variable may be shared only by features with identical value sets, or Binary ones (spec §6.2)")
        vals[self.index] = self._forward[b]

    def with_outer_op(self, op: str, loc: Optional[SourceLoc] = None) -> Constraint:
        """``o(a)F`` becomes ``op#o(a)F``: ``op`` is applied last (spec §4.4, §4.5)."""
        return OpVar(self.feature, self.name, (op,) + self.ops, loc or self.loc)

    def canonical(self) -> str:
        """``op1#op2(a)F`` (spec §4.4, §6.2)."""
        return "%s(%s)%s" % ("#".join(self.ops), self.name, self.feature.name)


class WeakVar(_VarBase):
    """``(?a)F`` — match: anything; bind ``a`` to F's value or to :data:`UNSPEC`, or check the
    existing binding (unspecification included); output: copy F exactly (spec §6.2 row 6)."""

    __slots__ = ()

    def extend(self, vals, env: "Env") -> Tuple["Env", ...]:
        """Bind or check ``a`` against F's value, UNSPEC for unspecified (spec §6.2)."""
        v = self._current(vals)
        if v is None:
            v = UNSPEC
        b = env.var(self.name)
        if b is None:
            return (env.bind_var(self.name, v),)
        return (env,) if b == v else ()

    def write(self, vals: List[Optional[str]], env: "Env") -> None:
        """Copy the bound value, including unspecification (spec §6.2)."""
        self._write_value(vals, self._bound(env))

    def with_outer_op(self, op: str, loc: Optional[SourceLoc] = None) -> Constraint:
        """Always raises: operations on ``(?a)`` are unsupported (design §13, decision 13)."""
        raise YascDefinitionError(
            "cannot apply (%s) to the weak variable (?%s)%s" % (op, self.name, self.feature.name), loc or self.loc)

    def canonical(self) -> str:
        """``(?a)F`` (spec §6.2)."""
        return "(?%s)%s" % (self.name, self.feature.name)


# --------------------------------------------------------------------------------------------
# Segment specs
# --------------------------------------------------------------------------------------------


class SegmentSpec:
    """A compiled segment spec ``{c1 c2 ...}`` or strict ``'{...}`` (spec §6.1, §6.2; design §4.2).

    ``constraints`` is sorted by feature index (stable, so several constraints on one
    feature keep their order). ``strict`` requires every other feature to be unspecified in
    a match and replaces the whole segment in an output. ``has_vars`` is true if any
    constraint uses a variable.

    Specs may be built before the system is sealed (implications); anything that depends on
    the geometry is computed on first use.
    """

    __slots__ = ("system", "constraints", "strict", "has_vars", "loc", "_tests", "_vcons", "_plain", "_uncovered",
                 "_single")

    def __init__(
        self,
        system: FeatureSystem,
        constraints: Iterable[Constraint] = (),
        strict: bool = False,
        loc: Optional[SourceLoc] = None,
        output: bool = False,
    ) -> None:
        cons = tuple(sorted(constraints, key=lambda c: c.index))
        for c in cons:
            if not isinstance(c, Constraint):
                raise TypeError("SegmentSpec takes Constraint objects, got %r" % (c,))
            if c.feature.system is not system:
                raise YascDefinitionError("feature %r belongs to another feature system" % c.feature.name, c.loc or loc)
        self.system = system
        self.constraints = cons
        self.strict = bool(strict)
        self.loc = loc
        self._tests = tuple(c for c in cons if not c.is_var)
        self._vcons = tuple(c for c in cons if c.is_var)
        self.has_vars = bool(self._vcons)
        self._plain: Optional[bool] = None
        self._uncovered: Optional[Tuple[int, ...]] = None
        self._single = None
        if output:
            self.for_output(loc)

    # -- construction helpers -------------------------------------------------------------

    @classmethod
    def from_segment(cls, seg: Segment, strict: bool = False, loc: Optional[SourceLoc] = None) -> "SegmentSpec":
        """A spec with ``Eq`` for each specified feature of ``seg`` (spec §6.1 ``[abc]``, ``'S``)."""
        feats = seg.system._features
        cons = [Eq(feats[i], v, loc) for i, v in enumerate(seg.values) if v is not None]
        return cls(seg.system, cons, strict, loc)

    def combine(self, other: "SegmentSpec", loc: Optional[SourceLoc] = None) -> "SegmentSpec":
        """``S1:S2`` — one spec satisfying both (spec §6.1); strict if either is."""
        if other.system is not self.system:
            raise YascDefinitionError("cannot combine specs of different feature systems", loc)
        return SegmentSpec(self.system, self.constraints + other.constraints, self.strict or other.strict,
                           loc or self.loc)

    def with_op(self, op: str, loc: Optional[SourceLoc] = None) -> "SegmentSpec":
        """This spec with ``op`` applied (outermost) to every variable value (spec §4.5)."""
        return SegmentSpec(self.system, [c.with_outer_op(op, loc) for c in self.constraints], self.strict,
                           loc or self.loc)

    def for_output(self, loc: Optional[SourceLoc] = None) -> "SegmentSpec":
        """Check that the spec is legal as an output (spec §6.2 "Output meaning"); return self.

        ``In``, ``Cmp`` and ``!Node`` raise :class:`YascDefinitionError`.
        """
        for c in self.constraints:
            c.check_output(loc or self.loc)
        return self

    # -- queries --------------------------------------------------------------------------

    def var_names(self) -> FrozenSet[str]:
        """Names of all variables used in the spec (spec §6.2)."""
        return frozenset(n for c in self._vcons for n in c.var_names())

    def features_read(self) -> FrozenSet[int]:
        """Feature indices the spec reads, with Node descendants (design §3 ``trigger_features``)."""
        out: set = set()
        for c in self.constraints:
            out.update(c.features_read())
        return frozenset(out)

    def is_plain(self) -> bool:
        """True if every feature read is segment-scope and not a tier feature (design §4.1)."""
        if self._plain is None:
            feats = self.system._features
            self._plain = all(feats[i].is_plain for i in self.features_read())
        return self._plain

    def _strict_indices(self) -> Tuple[int, ...]:
        if self._uncovered is None:
            covered = self.features_read()
            self._uncovered = tuple(f.index for f in self.system._features
                                    if not f.is_node and f.is_plain and f.index not in covered)
        return self._uncovered

    # -- matching -------------------------------------------------------------------------

    def match(self, view, env: Env = EMPTY) -> Tuple[Env, ...]:
        """All environments under which ``view`` satisfies the spec (spec §6.2, §6.3).

        ``view`` is a :class:`Segment` or a :class:`SegView`. Returns a tuple (empty = no
        match). A variable-free spec returns ``(env,)`` or ``()``; an unbound op-variable
        yields one environment per preimage, in value declaration order.
        """
        if type(view) is Segment:
            vals = view.values
        else:
            plain = self._plain
            if plain is None:
                plain = self.is_plain()
            vals = view.segment.values if plain else view
        if self.strict:
            unc = self._uncovered
            if unc is None:
                unc = self._strict_indices()
            for i in unc:
                if vals[i] is not None:
                    return ()
        for c in self._tests:
            if not c.test(vals):
                return ()
        vcons = self._vcons
        if not vcons:
            return (env,)
        envs: Tuple[Env, ...] = (env,)
        for c in vcons:
            if len(envs) == 1:
                envs = c.extend(vals, envs[0])
            else:
                envs = tuple(e2 for e in envs for e2 in c.extend(vals, e))
            if not envs:
                return ()
        return envs

    def matches(self, view, env: Env = EMPTY) -> bool:
        """True if :meth:`match` yields at least one environment (spec §6.2)."""
        return bool(self.match(view, env))

    # -- output ---------------------------------------------------------------------------

    def apply(self, seg: Segment, env: Env = EMPTY) -> Segment:
        """Output semantics (spec §6.2 last column, §8.2.4): apply every constraint in feature
        order. A strict spec replaces the segment: it starts from an empty bundle."""
        if self.strict:
            vals = [None] * len(seg.values)
        else:
            vals = list(seg.values)
        for c in self.constraints:
            c.write(vals, env)
        return Segment.make(seg.system, tuple(vals))

    def weak_apply(self, seg: Segment, env: Env = EMPTY) -> Segment:
        """Weak output (``~{...}``, ``~~>``): only unspecified features are filled; a Node is
        filled only if absent; ``_F`` does nothing; ``strict`` is ignored (spec §4.5, §8.2.4)."""
        vals = list(seg.values)
        for c in self.constraints:
            c.weak_write(vals, env)
        return Segment.make(seg.system, tuple(vals))

    # -- printing -------------------------------------------------------------------------

    def canonical(self) -> str:
        """``{+Syll -(a)High}``, or ``'{...}`` when strict (spec §6.2, §11.3)."""
        return ("'" if self.strict else "") + "{" + " ".join(c.canonical() for c in self.constraints) + "}"

    def __repr__(self) -> str:
        return "<SegmentSpec %s>" % self.canonical()
