"""Autosegmental tiers: autosegments, association lines, the No-Crossing Constraint, the
segment view, stray handling and the OCP (spec §5.5, §6.5; design §11; plan P8).

An :class:`AutoTier` is an immutable value attached to a :class:`~yasc.form.Form` (one per
``Tier(...)`` feature). It holds the autosegments in tier order and the association lines
as ``(segment index, autosegment id)`` pairs. Every operation returns a new tier and fails
with :class:`TierCrossing` when the result would violate the No-Crossing Constraint.
"""

from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from .errors import YascRuntimeError
from .marks import Mark
from .segment import PlainView, Segment

__all__ = [
    "Auto", "AutoTier", "TierCrossing", "TierView", "levels", "decompose", "ncc_ok",
]


_WORD_MARKS = frozenset({Mark.WORD, Mark.PHRASE})


class TierCrossing(YascRuntimeError):
    """An operation would make two association lines cross (spec §5.5 No-Crossing
    Constraint). Rule operations catch it and leave the focus unchanged (design §11)."""


class Auto(NamedTuple):
    """One autosegment (design §11): a stable ``id`` (unique within its tier), one *level*
    ``value`` (``'H'``) and its ``anchor`` gap. A linked autosegment is anchored at the gap
    before its first linked segment; a floating one keeps the gap it floats at (spec §5.5)."""

    id: int
    value: str
    anchor: int


# --------------------------------------------------------------------------------------------
# Levels and contours
# --------------------------------------------------------------------------------------------

_LEVELS: Dict[Tuple[str, ...], Tuple[str, ...]] = {}


def levels(values: Sequence[str]) -> Tuple[str, ...]:
    """The level values among a tier feature's declared ``values`` (spec §5.5 "contour values
    are sequences of level values"): those that are not the concatenation of two or more
    other declared values. ``H L M HL LH`` gives ``H L M`` (P8 decision 3)."""
    key = tuple(values)
    hit = _LEVELS.get(key)
    if hit is None:
        vs = set(key)
        hit = tuple(v for v in key if not _splits(v, vs - {v}, 2))
        _LEVELS[key] = hit
    return hit


def _splits(v: str, parts: set, need: int) -> bool:
    """True if ``v`` is a concatenation of at least ``need`` strings from ``parts``."""
    if not v:
        return need <= 0
    for p in parts:
        if p and v.startswith(p) and _splits(v[len(p):], parts, need - 1):
            return True
    return False


def decompose(value: str, lv: Sequence[str]) -> Tuple[str, ...]:
    """Split a (contour) value into level values, fewest parts first, preferring longer
    levels at the left (spec §5.5). A value that is not a concatenation of levels is one
    autosegment on its own."""
    n = len(value)
    best: List[Optional[Tuple[str, ...]]] = [None] * (n + 1)
    best[n] = ()
    for k in range(n - 1, -1, -1):
        for level in sorted(lv, key=len, reverse=True):
            if level and value.startswith(level, k) and best[k + len(level)] is not None:
                cand = (level,) + best[k + len(level)]  # type: ignore[operator]
                if best[k] is None or len(cand) < len(best[k]):  # type: ignore[arg-type]
                    best[k] = cand
    return best[0] if best[0] else (value,)


# --------------------------------------------------------------------------------------------
# Ordering and the No-Crossing Constraint
# --------------------------------------------------------------------------------------------


def _min_segs(links: Iterable[Tuple[int, int]]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for s, a in links:
        m = out.get(a)
        if m is None or s < m:
            out[a] = s
    return out


def _key(a: Auto, mins: Dict[int, int]) -> int:
    """Sort key in half-gap units: a floating autosegment sits *at* its gap (``2g``), a
    linked one just after the gap before its first segment (``2s+1``)."""
    m = mins.get(a.id)
    return 2 * a.anchor if m is None else 2 * m + 1


def ncc_ok(order: Sequence[Auto], links: Iterable[Tuple[int, int]]) -> bool:
    """The No-Crossing Constraint (spec §5.5; design §11): for links ``(s1, a1)`` and
    ``(s2, a2)``, ``s1 < s2`` implies ``order(a1) <= order(a2)``. O(L log L)."""
    pos = {a.id: t for t, a in enumerate(order)}
    by_seg: Dict[int, List[int]] = {}
    for s, a in links:
        by_seg.setdefault(s, []).append(pos[a])
    prev_max = -1
    for s in sorted(by_seg):
        ps = by_seg[s]
        if min(ps) < prev_max:
            return False
        prev_max = max(prev_max, max(ps))
    return True


def _normalise(autos: Sequence[Auto], links: FrozenSet[Tuple[int, int]], resort: bool = False
               ) -> Optional[Tuple[Auto, ...]]:
    """Anchor every linked autosegment at its first segment and restore key order with a
    stable sort. Without ``resort`` only floating autosegments may move: if the linked ones
    are out of order the lines cross, and ``None`` is returned. ``resort`` (segments moved,
    e.g. by a metathesis; :meth:`AutoTier.remap`) lets linked autosegments move too."""
    mins = _min_segs(links)
    out = [a if a.id not in mins or a.anchor == mins[a.id] else a._replace(anchor=mins[a.id]) for a in autos]
    keys = [_key(a, mins) for a in out]
    if not resort:
        lk = [k for k, a in zip(keys, out) if a.id in mins]
        if any(lk[t] > lk[t + 1] for t in range(len(lk) - 1)):
            return None
    if any(keys[t] > keys[t + 1] for t in range(len(keys) - 1)):
        out = [a for _k, _t, a in sorted((k, t, a) for t, (k, a) in enumerate(zip(keys, out)))]
    return tuple(out)


def _monotone(order: Sequence[Auto], links) -> bool:
    mins = _min_segs(links)
    keys = [_key(a, mins) for a in order]
    return all(keys[t] <= keys[t + 1] for t in range(len(keys) - 1))


# --------------------------------------------------------------------------------------------
# The tier
# --------------------------------------------------------------------------------------------


class AutoTier:
    """The autosegmental tier of one ``Tier(...)`` feature over a form of ``n`` segments
    (spec §5.5; design §11).

    * ``autos`` — the :class:`Auto` objects in tier order;
    * ``links`` — the association lines, a frozenset of ``(segment index, auto id)``;
    * ``next_id`` — the id the next new autosegment gets.

    Construction normalises the anchors of linked autosegments and checks the No-Crossing
    Constraint (raising :class:`TierCrossing`). Tiers are values: equality ignores the ids,
    so two forms with the same autosegments and lines are equal (design §4.3).
    """

    __slots__ = ("feature", "index", "n", "autos", "links", "next_id", "_pos", "_seg", "_values",
                 "_linked", "_eqkey", "_hash")

    #: ``Form.replace`` calls :meth:`after_replace` with the four-argument P3 signature.
    takes_frame = False

    def __init__(self, feature: Any, n: int, autos: Iterable[Auto] = (), links: Iterable[Tuple[int, int]] = (),
                 next_id: Optional[int] = None, *, check: bool = True, resort: bool = False) -> None:
        links = frozenset(links)
        autos = tuple(autos)
        ids = [a.id for a in autos]
        idset = set(ids)
        if len(idset) != len(ids):
            raise ValueError("autosegment ids must be unique: %r" % (ids,))
        for s, a in links:
            if not 0 <= s < n or a not in idset:
                raise ValueError("link %r outside segments 0..%d or to an unknown autosegment" % ((s, a), n - 1))
        for a in autos:
            if not 0 <= a.anchor <= n:
                raise ValueError("autosegment %r anchored outside gaps 0..%d" % (a, n))
        norm = _normalise(autos, links, resort or not check)
        if norm is None or (check and not ncc_ok(norm, links)):
            raise TierCrossing("association lines would cross on tier %s (spec §5.5 No-Crossing Constraint)"
                               % getattr(feature, "name", "?"))
        autos = norm
        self.feature = feature
        self.index = feature.index
        self.n = n
        self.autos = autos
        self.links = links
        self.next_id = (max(ids) + 1 if ids else 0) if next_id is None else max(next_id, max(ids) + 1 if ids else 0)
        pos = {a.id: t for t, a in enumerate(autos)}
        self._pos = pos
        seg: List[List[int]] = [[] for _ in range(n)]
        for s, a in links:
            seg[s].append(a)
        for x in seg:
            x.sort(key=pos.__getitem__)
        self._seg = tuple(tuple(x) for x in seg)
        self._values = tuple(("".join(autos[pos[a]].value for a in x) or None) for x in seg)
        self._linked = frozenset(a for _s, a in links)
        self._eqkey = (self.index, n, tuple((a.value, None if a.id in self._linked else a.anchor) for a in autos),
                       frozenset((s, pos[a]) for s, a in links))
        self._hash = hash(self._eqkey)

    # -- settings --------------------------------------------------------------------------

    @property
    def name(self) -> str:
        """The tier feature's name (spec §5.5)."""
        return self.feature.name

    @property
    def stray(self) -> str:
        """``float`` or ``delete``: what happens to an autosegment that loses its last link
        (spec §5.5 ``Stray``)."""
        decl = self.feature.tier
        return decl.stray if decl is not None else "float"

    @property
    def ocp_mode(self) -> str:
        """``off``, ``merge`` or ``delete`` (spec §5.5 ``OCP``)."""
        decl = self.feature.tier
        return decl.ocp if decl is not None else "off"

    @property
    def levels(self) -> Tuple[str, ...]:
        """The level values of the tier feature (:func:`levels`)."""
        return levels(self.feature.type.values)

    # -- queries ---------------------------------------------------------------------------

    def auto(self, auto_id: int) -> Optional[Auto]:
        """The autosegment with this id, or ``None`` (design §11)."""
        t = self._pos.get(auto_id)
        return None if t is None else self.autos[t]

    def order(self, auto_id: int) -> Optional[int]:
        """The position of an autosegment in tier order, or ``None``."""
        return self._pos.get(auto_id)

    def linked(self, s: int) -> Tuple[Auto, ...]:
        """The autosegments linked to segment ``s``, in tier order (spec §5.5)."""
        return tuple(self.autos[self._pos[a]] for a in self._seg[s])

    def value(self, s: int) -> Optional[str]:
        """The segment view of segment ``s``: the concatenated values of its linked
        autosegments in tier order, ``None`` (``_Tone``) when it has none (spec §5.5)."""
        return self._values[s]

    def segs_of(self, auto_id: int) -> Tuple[int, ...]:
        """The segments linked to an autosegment, in order."""
        return tuple(sorted(s for s, a in self.links if a == auto_id))

    def is_floating(self, auto_id: int) -> bool:
        """True if the autosegment has no association line (spec §5.5)."""
        return auto_id not in self._linked

    def floating(self, lo: int = 0, hi: Optional[int] = None) -> Tuple[Auto, ...]:
        """The floating autosegments anchored at gaps ``lo..hi`` (default: all), in tier order."""
        hi = self.n if hi is None else hi
        return tuple(a for a in self.autos if a.id not in self._linked and lo <= a.anchor <= hi)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AutoTier) and self._eqkey == other._eqkey

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        parts = []
        for a in self.autos:
            segs = self.segs_of(a.id)
            parts.append("%s#%d%s" % (a.value, a.id, ("->" + ",".join(map(str, segs))) if segs else "@%d" % a.anchor))
        return "AutoTier(%s, n=%d, %s)" % (self.name, self.n, " ".join(parts))

    # -- building blocks -------------------------------------------------------------------

    def _make(self, autos: Sequence[Auto], links: FrozenSet[Tuple[int, int]], n: Optional[int] = None,
              next_id: Optional[int] = None, resort: bool = False) -> "AutoTier":
        tier = AutoTier(self.feature, self.n if n is None else n, autos, links,
                        self.next_id if next_id is None else next_id, resort=resort)
        return self if tier == self else tier

    def _stray_anchor(self, auto_id: int, links: FrozenSet[Tuple[int, int]]) -> int:
        """Where an autosegment that lost its lines floats: at the gap before its first old
        segment, or after that segment if an earlier autosegment is still linked there."""
        m = min(s for s, a in self.links if a == auto_id)
        t = self._pos[auto_id]
        earlier = {x.id for x in self.autos[:t]}
        if any(a in earlier and s >= m for s, a in links):
            return min(m + 1, self.n)
        return m

    def _strays(self, autos: Sequence[Auto], links: FrozenSet[Tuple[int, int]],
                anchors: Optional[Dict[int, int]] = None) -> List[Auto]:
        """Apply ``Stray`` (spec §5.5) to the autosegments of ``autos`` that were linked in
        this tier and have no line in ``links``: delete them, or let them float at the gap
        given by ``anchors`` (default :meth:`_stray_anchor`)."""
        now = {a for _s, a in links}
        delete = self.stray == "delete"
        out = []
        for a in autos:
            if a.id in self._linked and a.id not in now:
                if delete:
                    continue
                anc = anchors.get(a.id) if anchors else None
                a = a._replace(anchor=self._stray_anchor(a.id, links) if anc is None else anc)
            out.append(a)
        return out

    @staticmethod
    def _insert_group(base: Sequence[Auto], group: Sequence[Auto], links: FrozenSet[Tuple[int, int]]) -> List[Auto]:
        """Insert new autosegments contiguously into ``base`` at the rightmost position that
        keeps the tier order consistent with the segments and satisfies the No-Crossing
        Constraint (spec §5.5 "the No-Crossing Constraint decides its order")."""
        base = list(base)
        for t in range(len(base), -1, -1):
            order = base[:t] + list(group) + base[t:]
            if _monotone(order, links) and ncc_ok(order, links):
                return order
        raise TierCrossing("no position for %s on the tier avoids crossing lines (spec §5.5)"
                           % "".join(a.value for a in group))

    def _new(self, values: Sequence[str], anchor: int) -> Tuple[List[Auto], int]:
        nid = self.next_id
        group = [Auto(nid + k, v, anchor) for k, v in enumerate(values)]
        return group, nid + len(group)

    # -- operations (spec §5.5, §6.5) ------------------------------------------------------

    def write(self, s: int, value: Optional[str]) -> "AutoTier":
        """Write the tier feature through the segment view of segment ``s`` (spec §5.5):
        remove its lines and link it to new autosegments for the levels of ``value``
        (``None``: just delink). Autosegments left without lines follow ``Stray``."""
        if value == self._values[s]:
            return self
        if value is None:
            return self.delink(s)
        return self.link_new(s, decompose(value, self.levels), replace=True)

    def link_new(self, s: int, values: Sequence[str], replace: bool = True) -> "AutoTier":
        """``S^[L]`` (``replace``) or ``S^+[L]``: link segment ``s`` to new autosegments with
        the given level values (spec §6.5)."""
        links = frozenset(l for l in self.links if not (replace and l[0] == s))
        group, nid = self._new(values, s)
        links2 = links | {(s, a.id) for a in group}
        order = self._insert_group(self._strays(self.autos, links2), group, links2)
        return self._make(order, links2, next_id=nid)

    def link(self, s: int, ids: Sequence[int], replace: bool = True) -> "AutoTier":
        """``S^=h`` (``replace``) or ``S^+=h``: link segment ``s`` to existing autosegments
        (spreading, contours; spec §6.5)."""
        for a in ids:
            if a not in self._pos:
                raise YascRuntimeError("autosegment #%d is no longer on tier %s" % (a, self.name))
        links = frozenset(l for l in self.links if not (replace and l[0] == s)) | {(s, a) for a in ids}
        if links == self.links:
            return self
        return self._make(self._strays(self.autos, links), links)

    def delink(self, s: int, ids: Optional[Iterable[int]] = None) -> "AutoTier":
        """``S^0`` (all lines of ``s``) or ``S^-=h`` (only those to ``ids``) (spec §6.5)."""
        ids = None if ids is None else set(ids)
        links = frozenset(l for l in self.links if not (l[0] == s and (ids is None or l[1] in ids)))
        if links == self.links:
            return self
        return self._make(self._strays(self.autos, links), links)

    def insert_floating(self, g: int, values: Sequence[str]) -> "AutoTier":
        """``^[H]`` on the RHS: new floating autosegments at gap ``g`` (spec §6.5)."""
        group, nid = self._new(values, g)
        return self._make(self._insert_group(self.autos, group, self.links), self.links, next_id=nid)

    def move_floating(self, anchors: Dict[int, int]) -> "AutoTier":
        """Re-anchor floating autosegments: ``anchors`` maps ids to new gaps (spec §5.5)."""
        if not anchors:
            return self
        autos = [a._replace(anchor=anchors[a.id]) if a.id in anchors and a.id not in self._linked else a
                 for a in self.autos]
        return self._make(autos, self.links)

    def delete(self, ids: Iterable[int]) -> "AutoTier":
        """Delete autosegments together with their lines (spec §6.5 ``^=h --> 0``)."""
        ids = set(ids)
        if not ids & set(self._pos):
            return self
        return self._make([a for a in self.autos if a.id not in ids], frozenset(l for l in self.links if l[1] not in ids))

    def relabel(self, auto_id: int, value: str) -> "AutoTier":
        """Give an autosegment a new value, keeping its lines (tier-only rules, spec §6.5)."""
        return self._make([a._replace(value=value) if a.id == auto_id else a for a in self.autos], self.links)

    # -- upkeep after segmental changes (design §4.3 step 5) -------------------------------

    def after_replace(self, i: int, j: int, k: int, index_map: Sequence[Optional[int]]) -> "AutoTier":
        """The tier after ``Form.replace(i, j, new)`` with ``k = len(new)`` (design §4.3):
        lines follow ``index_map``, lines of deleted segments go, autosegments left without
        lines follow ``Stray``, and floating anchors move like the form's gap marks (a pure
        insertion attaches left, P3 decision 4)."""
        delta = k - (j - i)
        if delta == 0 and all(index_map[p] == p for p in range(i, j)):
            return self
        links = frozenset((index_map[s], a) for s, a in self.links if index_map[s] is not None)
        return self._rebuild(self.n + delta, links, _gap_map(i, j, k))

    def remap(self, n_new: int, sources: Dict[int, Sequence[int]], gmap) -> "AutoTier":
        """The tier after a whole focus rewrite (P8 decision 6): ``sources[s]`` lists the new
        positions that carry old segment ``s`` (itself, or copies made by ``$n`` and
        metathesis), so lines follow moved and copied segments; ``gmap`` maps old gaps to
        new ones for floating anchors. Autosegments are re-sorted if segments changed order;
        a remaining crossing raises :class:`TierCrossing`."""
        links = frozenset((q, a) for s, a in self.links for q in sources.get(s, ()))
        return self._rebuild(n_new, links, gmap, resort=True)

    def _rebuild(self, n_new: int, links: FrozenSet[Tuple[int, int]], gmap, resort: bool = False) -> "AutoTier":
        autos = [a if a.id in self._linked else a._replace(anchor=gmap(a.anchor)) for a in self.autos]
        anchors = {a: gmap(m) for a, m in _min_segs(self.links).items()}
        autos = self._strays(autos, links, anchors)
        mins = _min_segs(links)
        autos = [a._replace(anchor=mins[a.id]) if a.id in mins else a for a in autos]
        return self._make(autos, links, n=n_new, resort=resort)

    # -- the OCP (spec §5.5) ---------------------------------------------------------------

    def ocp(self, mode: Optional[str] = None, gaps: Optional[Sequence[FrozenSet]] = None) -> "AutoTier":
        """Apply the OCP (spec §5.5, §10.5 ``!ocp``): adjacent autosegments with the same
        value and no word boundary between them are merged (the first keeps the lines of
        both) or the second is deleted with its lines. ``mode`` defaults to the tier's
        ``OCP`` setting; ``gaps`` are the form's stored marks (P8 decision 8)."""
        mode = mode or self.ocp_mode
        if mode == "off":
            return self
        autos = list(self.autos)
        links = set(self.links)
        t = 0
        changed = False
        while t + 1 < len(autos):
            a, b = autos[t], autos[t + 1]
            if a.value == b.value and not _word_between(a, b, links, gaps):
                moved = {l for l in links if l[1] == b.id}
                links -= moved
                if mode == "merge":
                    links |= {(s, a.id) for s, _b in moved}
                del autos[t + 1]
                changed = True
                continue
            t += 1
        if not changed:
            return self
        return self._make(autos, frozenset(links))


def _gap_map(i: int, j: int, k: int):
    """The gap map of ``Form.replace(i, j, new)`` with ``attach='left'`` (P3 decision 4)."""
    delta = k - (j - i)

    def gmap(g: int) -> int:
        if g < i:
            return g
        if g > j:
            return g + delta
        if i == j or g == j:
            return i + k
        return i
    return gmap


def _word_between(a: Auto, b: Auto, links, gaps) -> bool:
    """True if a stored word or phrase mark separates ``a`` and ``b`` (``a`` first). A
    floating autosegment at a boundary gap belongs to the word before it."""
    if gaps is None:
        return False
    sa = [s for s, x in links if x == a.id]
    sb = [s for s, x in links if x == b.id]
    lo = 2 * max(sa) + 1 if sa else 2 * a.anchor
    hi = 2 * min(sb) + 1 if sb else 2 * b.anchor
    for g in range((lo + 1) // 2, len(gaps)):
        if 2 * g >= hi:
            break
        if gaps[g] & _WORD_MARKS:
            return True
    return False


# --------------------------------------------------------------------------------------------
# The segment view (spec §5.5; design §4.1)
# --------------------------------------------------------------------------------------------


class TierView:
    """A Form-backed :class:`~yasc.segment.SegView` for forms with autosegmental tiers
    (design §4.1, §11): tier features read the segment view of their tier (spec §5.5); all
    other features come from ``base`` (a plain or syllable view)."""

    __slots__ = ("segment", "base", "form", "index")

    def __init__(self, base: Any, form: Any, index: int) -> None:
        self.segment = base.segment
        self.base = base
        self.form = form
        self.index = index

    def __getitem__(self, idx: int) -> Optional[str]:
        t = self.form.tier_by_index(idx)
        if t is None:
            return self.base[idx]
        return t._values[self.index]

    def get(self, feature) -> Optional[str]:
        """Value of a feature by name, index or object (spec §5.5)."""
        f = self.segment.system.resolve(feature)
        if f.is_node:
            return self.base.get(f)
        return self[f.index]

    def present(self, feature) -> bool:
        """Whether a leaf is specified, or a Node present (spec §4.3 rule 2)."""
        f = self.segment.system.resolve(feature)
        if f.is_node:
            return self.base.present(f)
        return self[f.index] is not None

    def __repr__(self) -> str:
        return "TierView(%r, %d)" % (self.segment, self.index)


# --------------------------------------------------------------------------------------------
# Pattern elements (spec §6.5; design §11 "LINK" and "FLOAT" edges)
# --------------------------------------------------------------------------------------------


class TierX:
    """The ``X`` of a tier element ``^[Tier.]X[=name]`` (spec §6.5): ``[H]`` (a value),
    ``(a)`` / ``(?a)`` (bind the value to variable ``a``), ``*`` (any), ``0`` (no links, only
    in ``S^0``) or empty (``^=h``: any)."""

    __slots__ = ("text", "value", "var", "none")

    def __init__(self, text: str) -> None:
        self.text = text or ""
        self.value = self.var = None
        self.none = self.text == "0"
        if self.text.startswith("["):
            self.value = self.text[1:-1]
        elif self.text.startswith("("):
            self.var = self.text[1:-1].lstrip("?")

    def bind(self, a: Auto, env, name: Optional[str]):
        """``env`` extended by matching autosegment ``a`` against X (and ``=name``), or
        ``None``."""
        if self.value is not None and a.value != self.value:
            return None
        if self.var is not None:
            env = env.unify_var(self.var, a.value)
            if env is None:
                return None
        if name is not None:
            bound = env.auto(name)
            if bound is None:
                env = env.bind_auto(name, a.id)
            elif bound != a.id:
                return None
        return env


class LinkSpec:
    """The payload of the SEG edge compiled from ``S^X['][=name]`` (spec §6.5; design §11
    ``LINK``): segment spec ``spec`` must match, and the segment must have at least one line
    (exactly one with ``exact``) to an autosegment of tier ``tier`` (``None``: the single
    tier) that matches X. ``S^0`` requires no line. Each matching autosegment gives its own
    environment when it binds a name or a variable. Behaves like a non-plain
    :class:`~yasc.segment.SegmentSpec` for the matcher, so it is matched on ``form.view(k)``."""

    __slots__ = ("spec", "tier", "x", "exact", "name", "system")

    has_vars = True

    def __init__(self, spec: Any, tier: Optional[str], x: str, exact: bool = False, name: Optional[str] = None) -> None:
        self.spec = spec
        self.tier = tier
        self.x = TierX(x)
        self.exact = exact
        self.name = name
        self.system = getattr(spec, "system", None)

    def is_plain(self) -> bool:
        """Always false: links depend on the form (design §13 entries 43, 45)."""
        return False

    def canonical(self) -> str:
        """``V^[H]'=h`` (spec §6.5; design §13 entry 32)."""
        return (self.spec.canonical() + "^" + (self.tier + "." if self.tier else "") + self.x.text
                + ("'" if self.exact else "") + ("=" + self.name if self.name else ""))

    def match(self, view: Any, env: Any) -> Tuple[Any, ...]:
        """The environments under which the segment of ``view`` matches (spec §6.5)."""
        envs = self.spec.match(view, env)
        if not envs:
            return ()
        form = getattr(view, "form", None)
        tier = form.tier(self.tier) if form is not None else None
        links = tier.linked(view.index) if tier is not None else ()
        x = self.x
        if x.none:
            return tuple(envs) if not links else ()
        if self.exact and len(links) != 1:
            return ()
        out: List[Any] = []
        for e in envs:
            for a in links:
                e2 = x.bind(a, e, self.name)
                if e2 is not None and e2 not in out:
                    out.append(e2)
                    if self.name is None and x.var is None:
                        break
        return tuple(out)


def _window_base(form: Any) -> Tuple[Any, int]:
    """``(form, offset)`` for a form or a P9 domain window (``DomainView``: ``form`` and
    ``a``), so tier lookups use the full form in its own gap coordinates."""
    base = getattr(form, "form", None)
    if base is not None and hasattr(form, "a"):
        return base, form.a
    return form, 0


def shift_env(env: Any, a: int) -> Any:
    """``env`` with its capture spans shifted by ``a`` gaps: the environment of a piece
    computed on a domain window, in form coordinates (plan P9 ``_shift``; P8 decision 21)."""
    if env is None or not a or not env.caps:
        return env
    caps = tuple(sorted((n, (i + a, None if j is None else j + a)) for n, (i, j) in env.caps.items()))
    return type(env)(tuple(sorted(env.vars.items())), caps, tuple(sorted(env.alts.items())),
                     tuple(sorted(env.autos.items())))


class FloatPred:
    """The payload of a FLOAT op compiled from ``^X[=name]`` (spec §6.5; design §11): a
    zero-width test for a floating autosegment anchored at the current (virtual) gap."""

    __slots__ = ("tier", "x", "name")

    def __init__(self, tier: Optional[str], x: str, name: Optional[str] = None) -> None:
        self.tier = tier
        self.x = TierX(x)
        self.name = name

    def canonical(self) -> str:
        """``^[H]=h`` (spec §6.5)."""
        return "^" + (self.tier + "." if self.tier else "") + self.x.text + ("=" + self.name if self.name else "")

    def match(self, form: Any, lo: int, hi: int, env: Any) -> List[Any]:
        """The environments extended by each matching floating autosegment at gaps
        ``lo..hi`` (spec §6.5)."""
        form, off = _window_base(form)
        get = getattr(form, "tier", None)
        tier = get(self.tier) if get is not None else None
        if tier is None:
            return []
        out: List[Any] = []
        for a in tier.floating(lo + off, hi + off):
            e2 = self.x.bind(a, env, self.name)
            if e2 is not None and e2 not in out:
                out.append(e2)
                if self.name is None and self.x.var is None:
                    break
        return out


# --------------------------------------------------------------------------------------------
# Right-hand-side operations (spec §6.5)
# --------------------------------------------------------------------------------------------

#: :attr:`TierOp.kind` values: the spec §6.5 RHS table.
OP_KINDS = ("set_new", "add_new", "set_ref", "add_ref", "clear", "del_ref", "keep", "float")


class TierOp(NamedTuple):
    """One autosegmental RHS operation (spec §6.5), the ``pattern`` of an ``IRRhsItem`` of
    kind ``tier``: ``set_new`` ``S^[L]``, ``add_new`` ``S^+[L]``, ``set_ref`` ``S^=h``,
    ``add_ref`` ``S^+=h``, ``clear`` ``S^0``, ``del_ref`` ``S^-=h``, ``keep`` ``S^*`` and
    ``float`` ``^[H]`` (a floating autosegment at this gap)."""

    kind: str
    tier: Optional[str]
    x: TierX
    name: Optional[str]
    source: str = ""

    @classmethod
    def from_syntax(cls, node: Any) -> "TierOp":
        """The operation of an RHS ``Linked``, ``LinkOp`` or ``AutoFloat`` node (spec §6.5).
        Raises ``ValueError`` with a message for forms the table does not define."""
        x = TierX(node.x)
        name = node.name
        kind_name = type(node).__name__
        has_value = x.value is not None or x.var is not None
        if kind_name == "AutoFloat":
            if not has_value:
                raise ValueError("a floating autosegment on the right-hand side needs a value: ^[H] or ^(a)")
            kind = "float"
        elif kind_name == "LinkOp":
            if node.op == "+":
                kind = "add_new" if has_value else "add_ref" if name else None
            else:
                kind = "del_ref" if name and not has_value else None
            if kind is None:
                raise ValueError("^%s%s: write S^+[L], S^+=h or S^-=h" % (node.op, node.x))
        elif x.none:
            kind = "clear"
        elif has_value:
            kind = "set_new"
        elif x.text == "*":
            kind = "keep"
        elif name:
            kind = "set_ref"
        else:
            raise ValueError("unsupported tier element %s on the right-hand side" % node.canonical())
        return cls(kind, node.tier, x, name, node.canonical())

    def value(self, env: Any) -> Optional[str]:
        """The value of ``[L]`` or of the variable in ``(a)`` under ``env``."""
        if self.x.value is not None:
            return self.x.value
        v = env.var(self.x.var) if self.x.var else None
        return v if isinstance(v, str) else None

    def apply(self, tier: "AutoTier", r: int, env: Any) -> "AutoTier":
        """Apply the operation to segment ``r`` of ``tier`` (spec §6.5)."""
        k = self.kind
        if k == "keep":
            return tier
        if k == "clear":
            return tier.delink(r)
        if k in ("set_new", "add_new"):
            v = self.value(env)
            if v is None:
                raise YascRuntimeError("%s: variable %r is not bound to a tier value" % (self.source, self.x.var))
            return tier.link_new(r, decompose(v, tier.levels), replace=k == "set_new")
        h = env.auto(self.name)
        if h is None:
            raise YascRuntimeError("%s: =%s captured no autosegment" % (self.source, self.name),
                                   hint="capture it in the LHS or a context, e.g. V^*=%s (spec §6.5)" % self.name)
        if k == "del_ref":
            return tier.delink(r, [h])
        return tier.link(r, [h], replace=k == "set_ref")


def after_rule(form: Any) -> Any:
    """Tier upkeep after a rule changed the form (P8 decision 8): the ``OCP`` setting of
    each tier is enforced (spec §5.5)."""
    tiers = form.tiers
    new = tuple(t.ocp(None, form.gaps) if isinstance(t, AutoTier) else t for t in tiers)
    if all(a is b for a, b in zip(new, tiers)):
        return form
    return form.with_tiers(new)


def _tier_slots(fs: Any) -> Tuple[int, ...]:
    return tuple(f.index for f in fs.features if getattr(f, "tier", None) is not None and not f.is_node)


def strip_tier_values(seg: Segment, slots: Sequence[int]) -> Segment:
    """``seg`` without values for the tier features ``slots`` (they live on the tier)."""
    vals = seg.values
    if all(vals[s] is None for s in slots):
        return seg
    lst = list(vals)
    for s in slots:
        lst[s] = None
    return Segment.make(seg.system, tuple(lst))


# --------------------------------------------------------------------------------------------
# Rule-engine hooks (design §11 "RHS link operations are applied after the segmental
# replacement"; P8 decisions 6 and 7)
# --------------------------------------------------------------------------------------------


def _base_item(it: Any) -> Any:
    """The segmental part of an RHS item (the ``S`` of ``S^X``)."""
    return it.alternatives[0][0] if it.kind == "tier" else it


def _written(item: Any, idx: int, seg: Segment) -> Tuple[bool, Optional[str]]:
    """Whether RHS ``item`` writes tier feature ``idx``, and the value it wrote into the
    output segment ``seg`` (spec §5.5 "writing through the segment view")."""
    k = item.kind
    if k in ("spec", "weak"):
        if any(c.feature.index == idx for c in item.spec.constraints):
            return True, seg.values[idx]
        return False, None
    if k == "replace":
        return True, seg.values[idx]
    if k == "merge" and item.segment.values[idx] is not None:
        return True, item.segment.values[idx]
    return False, None


def _pick(tiers: Dict[int, "AutoTier"], name: Optional[str]) -> "AutoTier":
    for t in tiers.values():
        if name is None or t.feature.name == name or name in t.feature.aliases:
            return t
    raise YascRuntimeError("the form has no autosegmental tier %s" % (name or ""))


def _lhs_floats(rule: Any) -> List[Tuple[int, Any]]:
    """``(capture number, AutoFloat)`` for the top-level floating elements of the LHS."""
    from .pattern import AutoFloat, Capture, Seq
    body = rule.ir.lhs_pattern
    if isinstance(body, Capture) and body.n == 0:
        body = body.pattern
    items = body.items if isinstance(body, Seq) else (body,)
    return [(x.n, x.pattern) for x in items if isinstance(x, Capture) and isinstance(x.pattern, AutoFloat)]


def _float_id(tier: "AutoTier", af: Any, g: int, env: Any) -> Optional[int]:
    if af.name and env.auto(af.name) is not None:
        return env.auto(af.name)
    x = TierX(af.x)
    for a in tier.floating(g, g):
        if x.bind(a, env, None) is not None:
            return a.id
    return None


def rewrite(rule: Any, form: Any, piece: Any, ctx: Any = None) -> Tuple[Any, bool, int]:
    """``BasicRule.rewrite`` for a form with autosegmental tiers (design §11; P8 decisions
    6, 7): the segments are rewritten by the ordinary engine on a copy without tiers; then
    every tier is rebuilt from the old one with a *whole-focus* source map, so lines follow
    segments that were kept, moved (metathesis) or copied (``$n``), and floating anchors
    follow the gaps. Tier-feature writes (``{[H]Tone}``), RHS link operations (``S^=h``...)
    and floating elements (``^[H]``, ``^=h --> 0``) are applied last. An operation that
    would cross lines leaves the focus unchanged."""
    from .form import Form
    from .rules import BasicRule
    env = piece.env
    i, j = piece.i, piece.j
    lay = rule.visibility(form)
    items = rule._expand(rule.ir.rhs, env) if env is not None else []
    vis = [p for p in range(i, j) if lay is None or lay.mask[p]]
    kv, l = len(vis), len(items)
    starts: List[int] = []
    pos = i
    for out in piece.outs:
        starts.append(pos)
        pos += len(out)
    total = pos - i if i < j else len(piece.insert)
    delta = total - (j - i)
    sources: Dict[int, List[int]] = {}
    writes: List[Tuple[int, Any, Optional[int]]] = []
    ops: List[Tuple[int, TierOp]] = []
    ends: List[int] = []

    def place(it: Any, r: int, p: Optional[int]) -> int:
        b = _base_item(it)
        if b.kind == "backref":
            srcpos = rule._copy(form, env, b.n, lay)[1]
            for off, s in enumerate(srcpos):
                sources.setdefault(s, []).append(r + off)
            length = len(srcpos)
        else:
            length = 1
            writes.append((r, b, p))
        if it.kind == "tier":
            ops.append((r, it.pattern))
        return length

    aligned = {vis[q]: items[q] for q in range(min(kv, l))}
    for p in range(i, j):
        st, al = starts[p - i], piece.aligns[p - i]
        it = aligned.get(p)
        if (it is None or _base_item(it).kind != "backref") and piece.outs[p - i] and al is not None:
            sources.setdefault(p, []).append(st + al)
        if it is not None:
            ends.append(st + place(it, st, p))
    if l > kv:
        r = ends[-1] if kv else i
        for it in items[kv:]:
            r += place(it, r, None)
            ends.append(r)
    full: Dict[int, List[int]] = {s: [s] for s in range(i)}
    full.update({s: [s + delta] for s in range(j, form.n)})
    full.update(sources)
    attach_left = getattr(rule, "attach", "left") == "left"

    def gmap(g: int) -> int:
        if g < i:
            return g
        if g > j:
            return g + delta
        if i == j:
            return i + total if attach_left else i
        return i + total if g == j else starts[g - i]

    new, _changed, _t = BasicRule.rewrite(rule, form.with_tiers(()), piece, ctx)
    old = {t.index: t for t in form.tiers if isinstance(t, AutoTier)}
    try:
        tiers = {idx: t.remap(new.n, full, gmap) for idx, t in old.items()}
        segs = list(new.segs)
        for r, b, p in writes:
            for idx in list(tiers):
                wrote, v = _written(b, idx, segs[r])
                if not wrote or (b.kind == "weak" and p is not None and old[idx].value(p) is not None):
                    continue
                tiers[idx] = tiers[idx].write(r, v)
        for r, op in ops:
            t = _pick(tiers, op.tier)
            tiers[t.index] = op.apply(t, r, env)
        tiers = _floats(rule, form, old, tiers, env, ends, i)
    except TierCrossing:
        return form, False, j - i
    slots = tuple(old)
    for r in range(i, i + total):
        segs[r] = strip_tier_values(segs[r], slots)
    order = [tiers.get(getattr(t, "index", None), t) for t in form.tiers]
    out = Form(tuple(segs), new.gaps, new.brackets, new.syllables, tuple(order), new.pending_syllable_marks)
    if out == form:
        return form, False, total
    return out, True, total


def _floats(rule: Any, form: Any, old: Dict[int, "AutoTier"], tiers: Dict[int, "AutoTier"], env: Any,
            ends: List[int], i: int) -> Dict[int, "AutoTier"]:
    """Floating elements (spec §6.5): the LHS ``^X`` elements are paired in order with the
    RHS ``^[L]`` elements. A pair relabels the matched autosegment; an LHS element with no
    partner is deleted with its lines (``^=h --> 0``); an RHS element with no partner inserts
    a new floating autosegment after the output of the RHS items before it."""
    lhs = _lhs_floats(rule)
    rhs: List[Tuple[int, TierOp]] = []
    c = 0
    for it in rule.ir.rhs:
        if it.kind == "tier" and not it.alternatives:
            rhs.append((c, it.pattern))
        else:
            c += 1
    if not lhs and not rhs:
        return tiers
    for k, (n, af) in enumerate(lhs):
        span = env.cap(n)
        t0 = _pick(old, af.tier)
        aid = _float_id(t0, af, span[0], env) if span is not None else None
        if aid is None:
            continue
        t = tiers[t0.index]
        if k < len(rhs):
            v = rhs[k][1].value(env)
            if v is not None and v != t.auto(aid).value:
                tiers[t.index] = t.relabel(aid, v)
        else:
            tiers[t.index] = t.delete([aid])
    for c, op in rhs[len(lhs):]:
        g = i if c == 0 or not ends else ends[min(c, len(ends)) - 1]
        t = _pick(tiers, op.tier)
        v = op.value(env)
        if v is None:
            raise YascRuntimeError("%s: variable %r is not bound to a tier value" % (op.source, op.x.var))
        tiers[t.index] = t.insert_floating(g, decompose(v, t.levels))
    return tiers


# --------------------------------------------------------------------------------------------
# Tier-only rules (spec §6.5 "/:T"; design §11)
# --------------------------------------------------------------------------------------------


class _Track:
    """The only "tier" of a projected form: the autosegment id of every projected position
    (``None`` for inserted ones), kept up to date by ``Form.replace`` (design §4.3 step 5)."""

    __slots__ = ("ids", "n", "index")

    takes_frame = False

    def __init__(self, ids: Iterable[Optional[int]]) -> None:
        self.ids = tuple(ids)
        self.n = len(self.ids)
        self.index = None

    def after_replace(self, i: int, j: int, k: int, index_map: Sequence[Optional[int]]) -> "_Track":
        """Follow ``Form.replace`` (design §4.3)."""
        new: List[Optional[int]] = [None] * (self.n - (j - i) + k)
        for p, q in enumerate(index_map):
            if q is not None:
                new[q] = self.ids[p]
        return _Track(new)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Track) and other.ids == self.ids

    def __hash__(self) -> int:
        return hash(self.ids)


def project(form: Any, tier: "AutoTier", autos: Optional[Sequence[Auto]] = None) -> Any:
    """The *projected form* of a tier (design §11): one segment per autosegment in tier
    order, carrying only the tier value; a word mark between two autosegments that a word
    boundary separates (spec §6.5 "boundaries are the word boundaries projected onto the
    tier")."""
    from .form import Form
    fs = tier.feature.system
    width = len(fs.empty.values)
    autos = tier.autos if autos is None else tuple(autos)
    segs = []
    for a in autos:
        vals: List[Optional[str]] = [None] * width
        vals[tier.index] = a.value
        segs.append(Segment.make(fs, tuple(vals)))
    gaps = [frozenset()] * (len(segs) + 1)
    for t in range(len(segs) - 1):
        if _word_between(autos[t], autos[t + 1], tier.links, form.gaps):
            gaps[t + 1] = frozenset({Mark.WORD})
    return Form(tuple(segs), tuple(gaps), (), None, (_Track(a.id for a in autos),))


def unproject(tier: "AutoTier", proj: Any, selected: Optional[Sequence[Auto]] = None,
              bounds: Optional[Tuple[int, int]] = None) -> "AutoTier":
    """Map a rewritten projected form back onto ``tier`` (design §11): kept autosegments
    take their new value; deleted ones go with their lines; inserted ones float between
    their neighbours; a position whose value was unset is deleted. With ``selected`` (the
    autosegments of a domain window ``bounds``; P8 decision 21) the others are kept as
    they are and the rewritten block takes the place of the selected ones."""
    track = proj.tiers[0]
    idx = tier.index
    nid = tier.next_id
    out: List[Auto] = []
    for k, seg in enumerate(proj.segs):
        v = seg.values[idx]
        if v is None:
            continue
        aid = track.ids[k]
        if aid is not None and tier.auto(aid) is not None:
            out.append(tier.auto(aid)._replace(value=v))
        else:
            out.append(Auto(nid, v, -1))
            nid += 1
    lo_b, hi_b = bounds if bounds is not None else (0, tier.n)
    if selected is not None:
        sel = {a.id for a in selected}
        merged: List[Auto] = []
        placed = False
        for a in tier.autos:
            if a.id in sel:
                if not placed:
                    merged.extend(out)
                    placed = True
            else:
                merged.append(a)
        if not placed:
            merged.extend(out)
        out = merged
    ids = {a.id for a in out}
    links = frozenset(l for l in tier.links if l[1] in ids)
    segs: Dict[int, List[int]] = {}
    for s, a in links:
        segs.setdefault(a, []).append(s)
    for t, a in enumerate(out):
        if a.anchor != -1:
            continue
        left = lo_b
        for b in reversed(out[:t]):
            if b.anchor != -1:
                left = max(segs[b.id]) + 1 if b.id in segs else b.anchor
                break
        right = hi_b
        for b in out[t + 1:]:
            if b.anchor != -1:
                right = min(segs[b.id]) if b.id in segs else b.anchor
                break
        out[t] = a._replace(anchor=min(left, right))
    return AutoTier(tier.feature, tier.n, out, links, nid)


def apply_tier_rule(rule: Any, form: Any, ctx: Any, window: Optional[Tuple[int, int]] = None) -> Any:
    """``BasicRule.apply`` of a ``/:T Tier`` rule (spec §6.5, §8.7): run the ordinary rule
    machinery (mode, ``/:*``, contexts, filters) on the projected tier, then map the result
    back. The trace shows one step with the real forms. Inside a P9 category domain,
    ``window`` is its segment span ``(a, b)``: only the autosegments within it are seen
    (spec §8.6 "material outside it is invisible"; P8 decision 21)."""
    from .rules import Outcome, TraceStep
    tier = form.tier(rule.ir.tier)
    if tier is None:
        return Outcome(form, False)
    sel = None
    if window is not None and tuple(window) != (0, form.n):
        a, b = window
        segs_of: Dict[int, List[int]] = {}
        for s, x in tier.links:
            segs_of.setdefault(x, []).append(s)
        sel = [x for x in tier.autos
               if (all(a <= s < b for s in segs_of[x.id]) if x.id in segs_of else a <= x.anchor <= b)]
    proj = project(form, tier, sel)
    saved = ctx.trace
    ctx.trace = None
    try:
        out = rule._repeat(proj, ctx) if rule.ir.repeat else rule.run(proj, ctx, rule.mode(ctx))
    finally:
        ctx.trace = saved
    if not out.applied:
        return Outcome(form, False)
    try:
        new_tier = unproject(tier, out.form, sel, window)
    except TierCrossing:
        return Outcome(form, False)
    new = form
    if new_tier != tier:
        new = after_rule(form.with_tiers(new_tier if t is tier else t for t in form.tiers))
    if new is not form or rule.weak:
        ctx.add_trace(TraceStep(rule.id, rule.name, rule.loc, form, new, (), None, ctx.depth))
    return Outcome(new, new is not form or rule.weak)


# --------------------------------------------------------------------------------------------
# Commands (spec §5.5, §10.5)
# --------------------------------------------------------------------------------------------


def _words(form: Any) -> List[Tuple[int, int]]:
    """Segment ranges ``[a, b)`` of the words of ``form`` (stored word and phrase marks)."""
    out = []
    a = 0
    for g in range(1, form.n):
        if form.gaps[g] & _WORD_MARKS:
            out.append((a, g))
            a = g
    out.append((a, form.n))
    return out


def associate(form: Any, tier_name: Optional[str] = None, direction: str = ">", spread: str = "last") -> Any:
    """``!associate Tier [dir=>|<] [mode=one-to-one] [spread=last|none]`` (spec §5.5): the
    Association Convention, word by word (P8 decision 9). Floating autosegments are linked
    one-to-one to toneless TBUs in the direction given, skipping any link that would cross;
    then, with ``spread=last``, every toneless TBU is linked to the last autosegment of the
    nearest TBU before it (in the direction) that has one. Leftover autosegments stay
    floating."""
    from .segment import EMPTY
    tier = form.tier(tier_name)
    if tier is None:
        raise YascRuntimeError("!associate: the form has no tier %s" % (tier_name or ""))
    decl = tier.feature.tier
    tbu = getattr(decl, "tbu", None)
    tbus = [s for s in range(form.n) if tbu is None or tbu.match(form.view(s), EMPTY)]
    fwd = direction != "<"
    for a, b in _words(form):
        word_tbus = [s for s in tbus if a <= s < b]
        if not fwd:
            word_tbus.reverse()
        floats = [x for x in tier.floating(a if a == 0 else a + 1, b)]
        if not fwd:
            floats.reverse()
        used = -1
        for n_f, f in enumerate(floats):
            for k in range(used + 1, len(word_tbus)):
                s = word_tbus[k]
                if tier.value(s) is not None:
                    continue
                try:
                    # The floating autosegments still to come keep their tier order: they
                    # move past the TBU just linked (before it, right to left).
                    rest = {x.id: (max(x.anchor, s + 1) if fwd else min(x.anchor, s)) for x in floats[n_f + 1:]}
                    tier = tier.move_floating(rest).link(s, [f.id])
                except TierCrossing:
                    continue
                used = k
                break
        if spread == "last":
            prev = None
            for s in word_tbus:
                if tier.value(s) is not None:
                    prev = s
                    continue
                if prev is None:
                    continue
                src = tier.linked(prev)
                try:
                    tier = tier.link(s, [(src[-1] if fwd else src[0]).id])
                except TierCrossing:
                    continue
                prev = s
    return _with_tier(form, tier)


def _with_tier(form: Any, tier: "AutoTier") -> Any:
    old = form.tier_by_index(tier.index)
    if old == tier:
        return form
    return form.with_tiers(tier if t is old else t for t in form.tiers)


def command(ir: Any, form: Any) -> Any:
    """Run ``!associate`` or ``!ocp`` (spec §5.5, §10.5) on ``form``; return the new form.
    ``!ocp Tier`` without a mode uses the tier's ``OCP`` setting, or ``merge`` when it is
    ``off``."""
    name = ir.args[0].value
    tier = form.tier(name)
    if tier is None:
        raise YascRuntimeError("!%s: %s is not an autosegmental tier of this form" % (ir.name, name), ir.loc,
                               hint="declare it with Tier(...) in the Phonology (spec §5.5)")
    if ir.name == "ocp":
        mode = ir.args[1].value if len(ir.args) > 1 else (tier.ocp_mode if tier.ocp_mode != "off" else "merge")
        return _with_tier(form, tier.ocp(mode, form.gaps))
    opts = dict(a.value for a in ir.args[1:])
    return associate(form, name, opts.get("dir", ">"), opts.get("spread", "last"))


# --------------------------------------------------------------------------------------------
# Compiler support (spec §6.5; design §7.3)
# --------------------------------------------------------------------------------------------


def resolve_tier(fs: Any, name: Optional[str], loc: Any = None) -> Any:
    """The tier feature ``^Tier.X`` refers to (spec §6.5): ``Tier`` defaults to the single
    tier and is required when several are declared."""
    from .errors import YascDefinitionError
    tiers = [f for f in fs.features if getattr(f, "tier", None) is not None]
    if not tiers:
        raise YascDefinitionError("autosegmental notation needs a Tier(...) feature, but none is declared", loc,
                                  hint="declare one, e.g. Tone [H] [L] Tier(TBU={+Syll}) (spec §5.5)")
    if name is None:
        if len(tiers) > 1:
            raise YascDefinitionError("several tiers are declared (%s): name one, e.g. ^%s.[H]"
                                      % (", ".join(f.name for f in tiers), tiers[0].name), loc, hint="spec §6.5")
        return tiers[0]
    f = fs.resolve(name, loc)
    if f.tier is None:
        raise YascDefinitionError("%s is not a tier feature" % f.name, loc,
                                  hint="tier features are declared with Tier(...) (spec §5.5)")
    return f


def check_x(feature: Any, x: str, loc: Any = None) -> None:
    """A tier element's ``[H]`` must be a declared value of the tier feature (spec §6.5)."""
    from .errors import YascDefinitionError
    tx = TierX(x)
    if tx.value is not None and tx.value not in feature.type.values:
        raise YascDefinitionError("[%s] is not a value of tier %s" % (tx.value, feature.name), loc,
                                  hint="values: " + " ".join("[%s]" % v for v in feature.type.values))


def check_element(fs: Any, node: Any) -> Any:
    """Validate the tier and ``X`` of a pattern element ``S^X`` or ``^X`` (spec §6.5)."""
    feature = resolve_tier(fs, node.tier, node.loc)
    check_x(feature, node.x, node.loc)
    return feature


def compile_rhs(comp: Any, node: Any, lhs_items: Any, pos: int, rule: Any, n_items: int) -> List[Any]:
    """Compile an RHS ``S^X``, ``S^+X``, ``S^-=h`` or ``^[H]`` (spec §6.5) into one
    ``IRRhsItem`` of kind ``tier``: ``pattern`` is the :class:`TierOp`, and
    ``alternatives[0][0]`` the compiled segment part (none for a floating element)."""
    from .errors import YascDefinitionError
    from .ir import IRRhsItem
    loc = getattr(node, "loc", None)
    check_element(comp.fs, node)
    try:
        op = TierOp.from_syntax(node)
    except ValueError as e:
        raise YascDefinitionError(str(e), loc, hint="see the RHS table of spec §6.5") from None
    src = node.canonical()
    if op.kind == "float":
        return [IRRhsItem("tier", pattern=op, source=src, loc=loc)]
    inner = comp._rhs(node.spec, False, lhs_items, pos, rule, n_items)
    if len(inner) != 1 or inner[0].kind in ("classcorr", "tier"):
        raise YascDefinitionError("%s: the segment before ^ must be one segment (a spec, [x], $n or a macro)" % src,
                                  loc, hint="spec §6.5")
    return [IRRhsItem("tier", pattern=op, alternatives=((inner[0],),), source=src, loc=loc)]


def _tier_node(p: Any, feature: Any) -> Any:
    """One pattern node of a tier-only rule (spec §6.5): ``[H]`` is an autosegment with
    value H, ``(a)`` any autosegment binding its value to ``a``; ``{}``, ``...``, specs on the
    tier feature and the pattern operators keep their meaning."""
    from dataclasses import replace
    from .errors import YascDefinitionError
    from .pattern import Alt, Macro, Opt, Plus, Seq, Spec, Star
    from .segment import Eq, SegmentSpec, Var
    from .syntax import RawOrtho
    fs = feature.system
    if isinstance(p, RawOrtho):
        out = []
        for w in p.text.split() or [p.text]:
            if w not in feature.type.values:
                raise YascDefinitionError("[%s] is not a value of tier %s" % (w, feature.name), p.loc,
                                          hint="in a /:T rule, [X] is an autosegment with value X (spec §6.5)")
            out.append(Spec(SegmentSpec(fs, [Eq(feature, w, p.loc)], False, p.loc), p.loc))
        return out[0] if len(out) == 1 else Seq(tuple(out), p.loc)
    if isinstance(p, Opt) and isinstance(p.pattern, Macro) and p.pattern.refine is None:
        return Spec(SegmentSpec(fs, [Var(feature, p.pattern.name, (), p.loc)], False, p.loc), p.loc)
    if isinstance(p, (Seq, Alt)):
        return replace(p, items=tuple(_tier_node(x, feature) for x in p.items))
    if isinstance(p, (Opt, Star, Plus)):
        return replace(p, pattern=_tier_node(p.pattern, feature))
    return p


def tier_rule_syntax(r: Any, feature: Any) -> Any:
    """A ``/:T`` rule with its LHS, RHS, contexts and filters rewritten by
    :func:`_tier_node` (spec §6.5 "specs are written [X], (a), {} or ...")."""
    from dataclasses import replace
    from .pattern import Pattern
    from .syntax import Rhs, RhsItem
    rhs = Rhs(tuple(RhsItem(_tier_node(it.pattern, feature), it.weak, it.loc) for it in r.rhs.items), r.rhs.loc)
    return replace(r, lhs=_tier_node(r.lhs, feature), rhs=rhs, mods=tier_mods(r.mods, feature))


def tier_mods(mods: Sequence[Any], feature: Any) -> Tuple[Any, ...]:
    """Contexts and filters of a ``/:T`` rule, rewritten by :func:`_tier_node` (spec §6.5)."""
    from dataclasses import replace
    from .pattern import Pattern
    return tuple(replace(m, arg=_tier_node(m.arg, feature)) if isinstance(m.arg, Pattern) else m for m in mods)


def segments_with_tiers(form: Any) -> List[Segment]:
    """The segments of ``form`` with each tier feature's segment view written back into
    them (spec §5.5): ``[á]`` in a pattern or on the RHS then carries its tone."""
    if not form.tiers:
        return list(form.segs)
    out = []
    for k, seg in enumerate(form.segs):
        vals = list(seg.values)
        for t in form.tiers:
            if isinstance(t, AutoTier) and t.value(k) is not None:
                vals[t.index] = t.value(k)
        out.append(Segment.make(seg.system, tuple(vals)))
    return out


# --------------------------------------------------------------------------------------------
# Orthography support (spec §5.5 "Orthography", §5.6)
# --------------------------------------------------------------------------------------------


def tier_features(fs: Any) -> List[Any]:
    """The ``Tier(...)`` features of a feature system, in declaration order (spec §5.5)."""
    return [f for f in fs.features if getattr(f, "tier", None) is not None and not f.is_node]


def build_tiers(form: Any, floats: Sequence[Tuple[int, int, str]] = (), fs: Any = None) -> Any:
    """The form of parsed text with its autosegmental tiers (spec §5.5; P8 decision 10):
    every segment whose bundle carries a tier value (from a tone diacritic) gets new
    autosegments for its levels, linked to it alone, and the value leaves the segment;
    ``floats`` are ``(gap, feature index, value)`` triples from ``FloatingPrefix`` text,
    which become floating autosegments. The tiers' ``OCP`` setting is then enforced."""
    from .form import Form
    if fs is None:
        fs = form.segs[0].system if form.segs else None
    feats = tier_features(fs) if fs is not None else []
    if not feats and floats:
        raise YascRuntimeError("floating autosegments need a Tier(...) feature (spec §5.5)")
    if not feats:
        return form
    tiers = []
    for f in feats:
        lv = levels(f.type.values)
        autos: List[Auto] = []
        links: List[Tuple[int, int]] = []
        for k in range(form.n + 1):
            for g, idx, v in floats:
                if g == k and idx == f.index:
                    for level in decompose(v, lv):
                        autos.append(Auto(len(autos), level, g))
            if k < form.n:
                v = form.segs[k].values[f.index]
                if v is not None:
                    for level in decompose(v, lv):
                        links.append((k, len(autos)))
                        autos.append(Auto(len(autos), level, k))
        tiers.append(AutoTier(f, form.n, autos, links, len(autos)))
    slots = tuple(f.index for f in feats)
    segs = tuple(strip_tier_values(s, slots) for s in form.segs)
    return after_rule(Form(segs, form.gaps, form.brackets, form.syllables, tuple(tiers),
                           form.pending_syllable_marks))


def floating_by_gap(form: Any) -> Dict[int, List[Tuple[int, str]]]:
    """``gap -> [(feature index, value), ...]`` for the floating autosegments of every
    tier, in tier order (spec §5.5 "Floating tones render with FloatingPrefix")."""
    out: Dict[int, List[Tuple[int, str]]] = {}
    for t in form.tiers:
        if isinstance(t, AutoTier):
            for a in t.floating():
                out.setdefault(a.anchor, []).append((t.index, a.value))
    return out


def concat_tiers(parts: Sequence[Tuple[int, Any]], n: int) -> Tuple["AutoTier", ...]:
    """The tiers of a concatenation (plan P9 paradigm cells; P8 decision 22): ``parts`` are
    ``(segment offset, form)`` in order and ``n`` is the total length. Each tier feature
    found in a part gets one tier whose autosegments are those of the parts, in order, with
    fresh ids and shifted lines and anchors."""
    feats: Dict[int, Any] = {}
    for _off, f in parts:
        for t in f.tiers:
            if isinstance(t, AutoTier):
                feats.setdefault(t.index, t.feature)
    out = []
    for idx, feature in feats.items():
        autos: List[Auto] = []
        links: List[Tuple[int, int]] = []
        for off, f in parts:
            t = f.tier_by_index(idx)
            if t is None:
                continue
            new_id = {}
            for a in t.autos:
                new_id[a.id] = len(autos)
                autos.append(Auto(len(autos), a.value, a.anchor + off))
            links.extend((s + off, new_id[a]) for s, a in t.links)
        out.append(AutoTier(feature, n, autos, links, len(autos)))
    return tuple(out)
