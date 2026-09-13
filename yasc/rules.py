"""Rule engine: executable rules built from the P4 IR (spec §8; design §10).

Public API
==========

* :class:`ApplyContext` — settings, RNG, trace sink, record data, date window, name
  filters and pending-construct policy for one application (design §10).
* :class:`Outcome` — ``(form, applied)``; :class:`TraceStep` — one trace entry.
* :func:`build` — turn one IR record (:class:`~yasc.ir.IRBasicRule`,
  :class:`~yasc.ir.IRGroup`, :class:`~yasc.ir.IRInvoke`, ...) into an executable
  :class:`Rule`; :func:`build_rules` — every basic rule of a
  :class:`~yasc.ir.CompiledScript` by id.
* :func:`apply_rule` and :func:`run_section` — convenience entry points for tests and the
  runtime (plan P6).

Executable classes: :class:`BasicRule`, :class:`Group`, :class:`Invoke`,
:class:`PendingRule` (placeholders and records waiting for a later phase) and
:class:`CommandItem` (``!commands`` and ``$x := ...`` inside Rules, handed to the runtime
through ``ApplyContext.on_command``).

Rules are immutable after building and hold no per-form state, so one built rule can be
applied to every record of a lexicon (design §1).
"""

import random
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .errors import NotImplementedYet, SourceLoc, YascRuntimeError
from .form import ATTACH_LEFT, ATTACH_RIGHT, Form
from .ir import (
    IRAssign, IRBasicRule, IRCommand, IRContext, IRFilter, IRGroup, IRInvoke, IRLexical, IRPlaceholder, IRRhsItem,
)
from .matcher import Visibility, find_all, match_context
from .pattern import (
    Alt, Anything, Boundary, BracketAssert, Capture, Nothing, Opt, Ortho, Plus, Seq, Spec, Star,
)
from .features import SYLLABLE
from .segment import EMPTY, Env, Segment, SegmentSpec
from . import tiers as _tiers  # plan P8: autosegmental tiers (design §11)

__all__ = [
    "ApplyContext", "RecordData", "Outcome", "TraceStep", "Rule", "BasicRule", "Group", "Invoke", "PendingRule",
    "CommandItem", "Builder", "build", "build_rules", "apply_rule", "run_section", "run_sequence", "run_persistent",
    "lexical_ok", "required_specs", "insertion_attach", "resyllabify", "DEFAULT_SETTINGS",
]

#: Runtime settings with their defaults (spec §8.3, §8.11, §10.5).
DEFAULT_SETTINGS: Dict[str, Any] = {
    "DefaultMode": "simultaneous",
    "MaxIterations": 1000,
    "MaxVariants": 64,
    "Seed": 0,
    "OnUnparsable": "error",
    "EnforceConstraints": "off",
    "InputFormat": "tsv",
    "Trace": "off",
}


class Outcome(NamedTuple):
    """The result of applying a rule (design §10, ``Outcome(form, applied)``; fixes R15).

    ``applied`` is true when the form changed, or when a ``/:~`` rule matched (spec §8.2
    step 6, §8.7)."""

    form: Form
    applied: bool


class TraceStep(NamedTuple):
    """One trace entry (design §10): added when a rule changes the form, when a ``/:~``
    rule matches, or when a pending construct is skipped (``note`` says why). ``foci`` are
    the ``(start_gap, end_gap)`` spans rewritten, in the input form's coordinates.
    Rendering is deferred to output time (plan P6)."""

    rule_id: Optional[int]
    name: Optional[str]
    loc: Optional[SourceLoc]
    before: Form
    after: Form
    foci: Tuple[Tuple[int, int], ...]
    note: Optional[str] = None
    depth: int = 0


class RecordData(NamedTuple):
    """Non-phonological data of one input record (spec §5.1 item 5, §10.2): the lexical
    feature bundle (``{name: value}``, Unary features as ``"!"``), the date, the dialect and
    the fields."""

    lexical: Mapping[str, str] = {}
    date: Optional[int] = None
    dialect: Optional[str] = None
    fields: Tuple[str, ...] = ()


class ApplyContext:
    """Everything a rule application needs besides the form (design §1, §10).

    * ``settings`` — :data:`DEFAULT_SETTINGS` overridden by the given mapping (``!set``,
      spec §10.5); ``DefaultMode`` and ``MaxIterations`` are read by the engine;
    * ``rng`` — the seeded :class:`random.Random` (``Seed``; used from plan P9);
    * ``trace`` — a list receiving :class:`TraceStep` objects, or ``None`` for no trace;
    * ``record`` — :class:`RecordData` (lexical bundle for ``/:L±``, date, dialect, fields);
    * ``date_window`` — ``(from, to)`` from ``--from``/``--to`` (spec §8.10). Together with
      ``record.date`` it is applied by :meth:`date_allows`, which basic rules, groups and
      placeholders check before running (plan P6; design §13 entry 101);
    * ``only`` / ``skip`` — rule and group names (``!only``/``!skip``, spec §10.5);
    * ``skip_pending`` — skip constructs waiting for a later phase instead of raising
      :class:`~yasc.errors.NotImplementedYet`; each skip is traced and listed in
      ``skipped``;
    * ``on_command`` — ``callback(ir, form, ctx) -> Form | None`` for ``IRCommand`` and
      ``IRAssign`` members of a group (the runtime, plan P6). Without it they are ignored
      (pending ones follow ``skip_pending``);
    * ``builder`` — the :class:`Builder` memo used when IR records are applied directly.
    """

    def __init__(self, settings: Optional[Mapping[str, Any]] = None, *, trace: Optional[list] = None,
                 record: Optional[RecordData] = None, date_window: Optional[Tuple[Optional[int], Optional[int]]] = None,
                 only: Iterable[str] = (), skip: Iterable[str] = (), skip_pending: bool = False,
                 rng: Optional[random.Random] = None,
                 on_command: Optional[Callable[[Any, Form, "ApplyContext"], Optional[Form]]] = None,
                 builder: Optional["Builder"] = None) -> None:
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        if settings:
            self.settings.update(settings)
        self.rng = rng if rng is not None else random.Random(int(self.settings.get("Seed", 0)))
        self.trace = trace
        self.record = record if record is not None else RecordData()
        self.date_window = date_window
        self.only = frozenset(only)
        self.skip = frozenset(skip)
        self.skip_pending = skip_pending
        self.on_command = on_command
        self.builder = builder if builder is not None else Builder()
        self.skipped: List[Tuple[Optional[int], Optional[str], str]] = []
        self.depth = 0
        # Persistent rules active in the current group scope (spec §8.9) and re-entrancy guard.
        self.persistent: List["Rule"] = []
        self._in_persistent = False
        self._only_depth = 0
        # Date of the innermost enclosing dated group: undated members inherit it (P6).
        self.group_date: Optional[int] = None
        # Plan P7: the active syllabification (a yasc.syllable.Syllabifier, set by the
        # runtime from !use; used by /:$), and run-time warnings such as no-op writes of
        # syllable features in trace mode (spec §5.4, §12).
        self.syllabifier: Any = None
        self.warnings: List[str] = []
        # Plan P9 (yasc.variants): the active /:C domain (key, label, own), the derivation
        # explorer (decision points), enforced constraints, and ``p9`` = route every basic
        # rule through yasc.variants (a domain or constraints are active).
        self.domain: Any = None
        self.explorer: Any = None
        self.constraints: Tuple[Any, ...] = ()
        self.p9 = False
        self.p9_bypass: Any = None

    def warn(self, message: str) -> None:
        """Record a run-time warning once per context (spec §12); the runtime reports it."""
        if message not in self.warnings:
            self.warnings.append(message)

    def date_allows(self, date: Optional[int]) -> bool:
        """The date filter of spec §8.10 (design §13 entry 101). ``date`` is the rule's own
        date; ``None`` inherits the enclosing group's date, else means −∞. A dated record
        admits only rules dated strictly later; ``date_window = (A, B)`` admits dates in
        ``[A, B]``, and undated rules unless ``A`` is given."""
        rec = self.record.date
        win = self.date_window
        lo, hi = win if win is not None else (None, None)
        if rec is None and lo is None and hi is None:
            return True
        if date is None:
            date = self.group_date
        if rec is not None and (date is None or date <= rec):
            return False
        if lo is not None and (date is None or date < lo):
            return False
        return hi is None or date is None or date <= hi

    @property
    def max_iterations(self) -> int:
        """``MaxIterations`` (spec §8.3; default 1000)."""
        return int(self.settings.get("MaxIterations", 1000))

    @property
    def default_mode(self) -> str:
        """``DefaultMode``: ``simultaneous`` or ``once`` (spec §8.3)."""
        return str(self.settings.get("DefaultMode", "simultaneous"))

    def add_trace(self, step: TraceStep) -> None:
        """Append ``step`` to the trace if tracing is on (design §10)."""
        if self.trace is not None:
            self.trace.append(step)


# --------------------------------------------------------------------------------------------
# Helpers shared by rules and groups
# --------------------------------------------------------------------------------------------


def _pending_phase(ir) -> Optional[str]:
    """The plan phase an IR record still waits for, if any (design §13 entry 77)."""
    if isinstance(ir, IRPlaceholder):
        return ir.phase
    pend = getattr(ir, "pending", None)
    if isinstance(pend, str):
        return pend
    if pend:
        return pend[0]
    return None


def _handle_pending(rule: "Rule", phase: str, what: str, form: Form, ctx: ApplyContext) -> Outcome:
    """Raise :class:`NotImplementedYet`, or skip and trace when ``ctx.skip_pending``
    (plan P5 "pending constructs")."""
    if not ctx.skip_pending:
        raise NotImplementedYet("%s cannot run before plan phase %s" % (what, phase), rule.loc, phase=phase,
                                hint="compile and run with skip_pending to ignore it")
    ctx.skipped.append((rule.id, rule.name, phase))
    ctx.add_trace(TraceStep(rule.id, rule.name, rule.loc, form, form, (), "skipped: needs plan phase %s" % phase,
                            ctx.depth))
    return Outcome(form, False)


def _lexical_value_ok(c, have: Optional[str]) -> bool:
    k = c.kind
    if k == "value":
        return have is not None and have == str(c.value)
    if k == "bare":
        return have == "!"
    if k == "absent":
        return have is None
    if k == "in":
        return have is not None and have in tuple(str(v) for v in c.values)
    if k == "cmp":
        try:
            v = int(have)
        except (TypeError, ValueError):
            return False
        n = c.n
        return {">": v > n, "<": v < n, ">=": v >= n, "<=": v <= n}.get(c.cmp, False)
    if k == "var":
        return have is not None
    return True  # weakvar: anything


def lexical_ok(restrictions: Sequence[IRLexical], lexical: Mapping[str, str]) -> bool:
    """``/:L± {spec}`` against the record's lexical bundle (spec §8.6). ``/:L+`` needs every
    constraint of the spec to hold, ``/:L-`` needs at least one to fail. Lexical features
    are untyped, so a variable ``(a)F`` means "F is specified"."""
    for lx in restrictions:
        m = all(_lexical_value_ok(c, lexical.get(c.feature)) for c in lx.constraints)
        if m != lx.positive:
            return False
    return True


# --------------------------------------------------------------------------------------------
# Static analysis of compiled patterns (design §10 performance)
# --------------------------------------------------------------------------------------------


def _items(p) -> Tuple[Any, ...]:
    return p.items if isinstance(p, Seq) else (p,)


def required_specs(p) -> Tuple[Any, ...]:
    """Segment specs that every match of pattern ``p`` must consume (design §10 "quick
    reject"). Alternatives contribute nothing (their intersection is usually empty), nor do
    optional, starred, ``...`` and ``$n`` parts."""
    if isinstance(p, Capture):
        return required_specs(p.pattern)
    if isinstance(p, Spec):
        return (p.spec,)
    if isinstance(p, Ortho):
        return tuple(p.specs)
    if isinstance(p, Plus):
        return required_specs(p.pattern)
    if isinstance(p, Seq):
        out: List[Any] = []
        for x in p.items:
            for s in required_specs(x):
                if all(s is not y for y in out):
                    out.append(s)
        return tuple(out)
    return ()


def _nullable_zero_width_ok(x) -> bool:
    """True for items that may match nothing (skipped when looking for an adjacent
    boundary)."""
    if isinstance(x, Capture):
        return _nullable_zero_width_ok(x.pattern)
    return isinstance(x, (Opt, Star, Nothing, Anything, BracketAssert))


def _edge_has_boundary(p, at_end: bool) -> bool:
    """True if ``p`` has a boundary assertion at its right end (``at_end``) or left end,
    possibly separated from that end by items that may match nothing (``#C*``)."""
    if p is None:
        return False
    seq = list(_items(p))
    if at_end:
        seq.reverse()
    for x in seq:
        if isinstance(x, Capture):
            x = x.pattern
        if isinstance(x, Boundary):
            return True
        if isinstance(x, Alt):
            if x.items and all(_edge_has_boundary(a, at_end) for a in x.items):
                return True
        if not _nullable_zero_width_ok(x):
            return False
    return False


def insertion_attach(contexts: Sequence[IRContext]) -> str:
    """``attach`` for a pure insertion (design §13 entries 50 and 87): ``"right"`` when a
    positive context has a boundary immediately left of the locus (``# ___ C``) and none has
    one immediately right of it; otherwise ``"left"`` (``C ___ #`` gives ``C e #``)."""
    left = any(_edge_has_boundary(c.left_pattern, True) for c in contexts)
    right = any(_edge_has_boundary(c.right_pattern, False) for c in contexts)
    return ATTACH_RIGHT if left and not right else ATTACH_LEFT


# --------------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------------


class Rule:
    """An executable rule (design §10): ``apply(form, ctx) -> Outcome``.

    ``ir`` is the IR record it was built from; ``id``, ``name`` and ``loc`` identify it in
    traces and errors; ``persistent`` marks ``/::`` (spec §8.9); ``date`` is the rule's date
    (spec §8.10), carried for the runtime's date filter (plan P6)."""

    ir: Any = None
    id: Optional[int] = None
    name: Optional[str] = None
    loc: Optional[SourceLoc] = None
    persistent: bool = False
    date: Optional[int] = None

    def apply(self, form: Form, ctx: ApplyContext) -> Outcome:  # pragma: no cover - abstract
        """Apply the rule once in its own mode (spec §8.3)."""
        raise NotImplementedError

    def _error(self, message: str, hint: Optional[str] = None) -> YascRuntimeError:
        return YascRuntimeError(message, self.loc, hint=hint, rule_id=self.id, rule_name=self.name)

    def _name_blocked(self, ctx: ApplyContext) -> bool:
        """``!skip``/``!only`` by name (spec §10.5)."""
        if self.name is not None and self.name in ctx.skip:
            return True
        return bool(ctx.only) and ctx._only_depth == 0 and self.name not in ctx.only and not isinstance(self, Group)

    def __repr__(self) -> str:
        return "<%s %s%s>" % (type(self).__name__, self.id if self.id is not None else "",
                              (" %r" % self.name) if self.name else "")


def resyllabify(form: Form, ctx: Optional["ApplyContext"], rule: Optional["Rule"] = None) -> Form:
    """``/:$`` (spec §8.7, §5.4): recompute the syllable tier before a rule, with the active
    syllabification (``ctx.syllabifier``, set by ``!use``), else with the one that built the
    form's tier (plan P7; design §13 entry 121)."""
    syl = ctx.syllabifier if ctx is not None else None
    if syl is None and form.syllables is not None:
        syl = form.syllables.syllabifier
    if syl is None:
        raise YascRuntimeError("/:$ needs a Syllabification, but none is active", rule.loc if rule else None,
                               rule_id=getattr(rule, "id", None), rule_name=getattr(rule, "name", None),
                               hint="define a Syllabification section, or select one with !use (spec §5.7)")
    return syl.syllabify(form)


class _Piece(NamedTuple):
    """The rewrite of one focus: replace ``segs[i:j]`` position by position."""

    i: int
    j: int
    outs: Tuple[Tuple[Segment, ...], ...]     # one output tuple per position i..j-1
    aligns: Tuple[Optional[int], ...]         # per position: offset of the segment itself, or None
    insert: Tuple[Segment, ...]               # pure insertion at gap i (only when i == j)
    # Plan P7: syllable-scope writes (spec §5.4) as (q, offset, spec, env, weak): the output
    # segment at ``offset`` in the output of position ``i+q`` (q = -1: in ``insert``).
    sylw: Tuple[Tuple[int, int, Any, Env, bool], ...] = ()
    # Plan P8: the focus environment, for the whole-focus tier upkeep (tiers.rewrite).
    env: Any = None


def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    """Simultaneous-mode overlap (spec §8.3): shared segments, or zero-width foci at the
    same gap, or a zero-width focus strictly inside another focus."""
    (i1, j1), (i2, j2) = a, b
    if i1 == j1 and i2 == j2:
        return i1 == i2
    if i1 == j1:
        return i2 < i1 < j2
    if i2 == j2:
        return i1 < i2 < j1
    return max(i1, i2) < min(j1, j2)


class BasicRule(Rule):
    """An executable basic rule ``LHS --> RHS mods`` (spec §8.1–§8.5, §8.7; design §10).

    Built from an :class:`~yasc.ir.IRBasicRule`. :meth:`candidates` yields the foci
    ``(i, j, env)`` in preference order (spec §8.2 steps 1–4); :meth:`apply` runs the rule
    in its mode (spec §8.3).
    """

    def __init__(self, ir: IRBasicRule) -> None:
        self.ir = ir
        self.id = ir.id
        self.name = ir.name
        self.loc = ir.loc
        self.persistent = ir.persistent
        self.date = ir.date
        self.fs = ir.phonology
        self.lhs = ir.lhs
        self.direction = ir.direction
        self.pending = _pending_phase(ir)
        self.weak = ir.weak
        self.raw = ir.raw
        self.attach = insertion_attach(ir.contexts)
        req = []
        for s in required_specs(ir.lhs_pattern):
            if s.is_plain():
                req.append((s, None if s.has_vars else ir.lhs.match_cache.setdefault(s, {})))
        self.required = tuple(req)
        self._vis_specs = ir.visibility
        self._split: Dict[int, Tuple[Any, Any, Any]] = {}
        # Plan P9: dialect/category restrictions, /% and /??? go through yasc.variants.
        self.p9 = bool(ir.optional or ir.stochastic or ir.restrictions.dialect or ir.restrictions.category)

    def _syl_split(self, spec) -> Tuple[Any, Any]:
        """``(segment part, syllable part)`` of an RHS spec (spec §5.4): constraints on
        ``Scope(Syllable)`` features are written to the segment's syllable, the others to the
        segment. The syllable part is ``None`` when the spec writes no syllable feature.
        Memoised per spec (plan P7)."""
        hit = self._split.get(id(spec))
        if hit is None:
            syl = [c for c in spec.constraints if c.feature.scope == SYLLABLE]
            if not syl:
                hit = (spec, spec, None)
            else:
                plain = SegmentSpec(spec.system, [c for c in spec.constraints if c.feature.scope != SYLLABLE],
                                    spec.strict, spec.loc)
                hit = (spec, plain, SegmentSpec(spec.system, syl, False, spec.loc))
            self._split[id(spec)] = hit  # the entry keeps ``spec`` alive, so its id stays valid
        return hit[1], hit[2]

    # -- focus search (spec §8.2 steps 1-4) ------------------------------------------------

    def visibility(self, form: Form) -> Optional[Visibility]:
        """The ``/:F±`` mask of ``form`` (spec §8.5), or ``None`` if everything is visible."""
        v = self._vis_specs
        if v is None:
            return None
        return Visibility.of(form, [not v.hides(form.view(k)) for k in range(form.n)])

    def quick_reject(self, form: Form) -> bool:
        """True if some required LHS spec matches no segment of ``form``, so the rule cannot
        apply (design §10 "quick reject")."""
        segs = form.segs
        for spec, table in self.required:
            if table is None:
                if not any(spec.match(s, EMPTY) for s in segs):
                    return True
                continue
            for s in segs:
                r = table.get(s)
                if r is None:
                    r = table[s] = bool(spec.match(s, EMPTY))
                if r:
                    break
            else:
                return True
        return False

    def candidates(self, form: Form, lay: Optional[Visibility] = None) -> Iterator[Tuple[int, int, Env]]:
        """Every focus ``(i, j, env)`` that satisfies the positive contexts (conjunctive,
        backtracking), the negative contexts and the input filters, in preference order
        (spec §6.3, §8.2 steps 1–4)."""
        ir = self.ir
        for i, j, env in find_all(self.lhs, form, EMPTY, direction=self.direction, visible=lay,
                                  first_specs=ir.first_specs):
            for e in self._contexts(form, i, j, env, lay, 0):
                if ir.neg_contexts and self._negated(form, i, j, e, lay):
                    continue
                if ir.in_filters:
                    for e2 in _filters(ir.in_filters, form, i, j, e, lay, 0):
                        yield i, j, e2
                else:
                    yield i, j, e

    def _contexts(self, form, i, j, env, lay, k) -> Iterator[Env]:
        ctxs = self.ir.contexts
        if k == len(ctxs):
            yield env
            return
        c = ctxs[k]
        for e in match_context(c.left, c.right, form, i, j, env, lay):
            yield from self._contexts(form, i, j, e, lay, k + 1)

    def _negated(self, form, i, j, env, lay) -> bool:
        """A ``/!`` context blocks the focus if it has any match under ``env``; its bindings
        are discarded (spec §8.2 step 3)."""
        for c in self.ir.neg_contexts:
            for _ in match_context(c.left, c.right, form, i, j, env, lay):
                return True
        return False

    # -- RHS construction (spec §8.2.4) ----------------------------------------------------

    def _expand(self, items: Sequence[IRRhsItem], env: Env) -> List[IRRhsItem]:
        """Resolve class correspondence by the recorded alternative index (spec §8.2.4)."""
        out: List[IRRhsItem] = []
        for it in items:
            if it.kind == "classcorr":
                k = env.alt(it.alt)
                if k is None or not 0 <= k < len(it.alternatives):
                    raise self._error("class correspondence: the aligned LHS disjunction recorded no alternative",
                                      hint="the LHS item must be a disjunction << a | b >> (spec §8.2.4)")
                out.extend(self._expand(it.alternatives[k], env))
            elif it.kind == "tier":
                if it.alternatives:  # plan P8: S^X keeps its place; a floating ^X is zero-width
                    out.append(it)
            else:
                out.append(it)
        return out

    def _close(self, old: Segment, new: Segment) -> Segment:
        """Implications on a changed segment (spec §4.5, §8.2.4), unless ``/:Raw``."""
        if self.raw or new is old or new == old:
            return new
        return self.fs.close(new, old.diff(new), self.loc)

    def _copy(self, form: Form, env: Env, n: int, lay) -> Tuple[Tuple[Segment, ...], Tuple[int, ...]]:
        """The visible segments of capture ``n`` and their positions (spec §6.4, §8.5)."""
        span = env.cap(n)
        if span is None or span[1] is None:
            raise self._error("$%d refers to an LHS item that matched nothing" % n)
        pos = tuple(p for p in range(span[0], span[1]) if lay is None or lay.mask[p])
        return tuple(form.segs[p] for p in pos), pos

    def _modify(self, it: IRRhsItem, m: Segment, p: int, form: Form, env: Env, lay, sylw: Optional[list] = None):
        """Item ``it`` acting on LHS segment ``m`` at position ``p``: ``(outputs, align)``
        where ``align`` is the offset of ``m`` itself among the outputs, or ``None``.
        Writes of ``Scope(Syllable)`` features are not applied to the segment but appended
        to ``sylw`` as ``(p, 0, spec, env, weak)`` (spec §5.4; plan P7)."""
        k = it.kind
        if k == "tier":  # plan P8: the segment part of S^X; the link part runs in tiers.rewrite
            return self._modify(it.alternatives[0][0], m, p, form, env, lay, sylw)
        if k == "backref":
            segs, pos = self._copy(form, env, it.n, lay)
            return segs, (pos.index(p) if p in pos else None)
        if k in ("spec", "weak"):
            spec, syl = self._syl_split(it.spec)
            new = spec.apply(m, env) if k == "spec" else spec.weak_apply(m, env)
            if syl is not None and sylw is not None:
                sylw.append((p, 0, syl, env, k == "weak"))
        elif k == "merge":
            new = m.merge(it.segment)
        elif k == "replace":
            new = it.segment
        else:  # pragma: no cover - the compiler produces no other kinds
            raise self._error("unknown RHS item kind %r" % k)
        return (self._close(m, new),), 0

    def _insert(self, it: IRRhsItem, form: Form, env: Env, lay) -> Tuple[Segment, ...]:
        """An excess RHS item: a new segment from the item alone, defaults filled in by the
        implications (spec §8.2.4 "Excess RHS")."""
        k = it.kind
        if k == "tier":  # plan P8: the segment part of S^X
            return self._insert(it.alternatives[0][0], form, env, lay)
        if k == "backref":
            return self._copy(form, env, it.n, lay)[0]
        empty = self.fs.empty
        if k == "spec":
            new = self._syl_split(it.spec)[0].apply(empty, env)
        elif k == "weak":
            new = self._syl_split(it.spec)[0].weak_apply(empty, env)
        else:  # merge / replace
            new = it.segment
        if not self.raw:
            new = self.fs.close_new(new, self.loc)
        return (new,)

    def piece(self, form: Form, i: int, j: int, env: Env, lay=None) -> _Piece:
        """The rewrite of the focus ``(i, j)`` under ``env`` (spec §8.2.4): positional
        alignment over the visible LHS segments, excess LHS deleted, excess RHS inserted
        after the last one (or at gap ``i``); invisible segments are kept (spec §8.5)."""
        items = self._expand(self.ir.rhs, env)
        segs = form.segs
        outs: List[Tuple[Segment, ...]] = [(segs[p],) for p in range(i, j)]
        aligns: List[Optional[int]] = [0] * (j - i)
        vis = [p for p in range(i, j) if lay is None or lay.mask[p]]
        kv, l = len(vis), len(items)
        sylw: List[Tuple[int, int, Any, Env, bool]] = []
        for q in range(min(kv, l)):
            p = vis[q]
            outs[p - i], aligns[p - i] = self._modify(items[q], segs[p], p, form, env, lay, sylw)
        for q in range(l, kv):
            outs[vis[q] - i] = ()
            aligns[vis[q] - i] = None
        insert: Tuple[Segment, ...] = ()
        if l > kv:
            extra: Tuple[Segment, ...] = ()
            extra_w = []
            for it in items[kv:]:
                if it.kind in ("spec", "weak"):
                    syl = self._syl_split(it.spec)[1]
                    if syl is not None:
                        extra_w.append((len(extra), syl, it.kind == "weak"))
                extra += self._insert(it, form, env, lay)
            if kv:
                base = len(outs[vis[-1] - i])
                outs[vis[-1] - i] = outs[vis[-1] - i] + extra
                sylw.extend((vis[-1], base + off, syl, env, weak) for off, syl, weak in extra_w)
            else:
                insert = extra
                sylw.extend((-1, off, syl, env, weak) for off, syl, weak in extra_w)
        return _Piece(i, j, tuple(outs), tuple(aligns), insert, tuple(sylw), env)

    def rewrite(self, form: Form, piece: _Piece, ctx: Optional[ApplyContext] = None) -> Tuple[Form, bool, int]:
        """Apply one piece: ``(new_form, changed, new_span_length)`` (spec §8.2.4).

        Segments are replaced one position at a time, right to left, so a boundary between
        two surviving positions stays where it is and a deletion unions the marks of the
        gaps it merges (design §13 entry 88). A pure insertion uses the rule's ``attach``."""
        if form.tiers and not self.ir.tier:  # plan P8: tiers follow the whole focus (design §11)
            return _tiers.rewrite(self, form, piece, ctx)
        i, j = piece.i, piece.j
        if i == j:
            if not piece.insert:
                return form, False, 0
            form = form.replace(i, i, piece.insert, attach=self.attach)
            changed, total = True, len(piece.insert)
        else:
            changed = False
            total = 0
            for q in range(j - i - 1, -1, -1):
                out = piece.outs[q]
                total += len(out)
                p = i + q
                if len(out) == 1 and out[0] == form.segs[p]:
                    continue
                al = piece.aligns[q]
                form = form.replace(p, p + 1, out, align=(al if out else None,))
                changed = True
        if piece.sylw:
            new = self._write_syllables(form, piece, ctx)
            changed = changed or new is not form
            form = new
        return form, changed, total

    def _write_syllables(self, form: Form, piece: _Piece, ctx: Optional[ApplyContext]) -> Form:
        """Apply the piece's ``Scope(Syllable)`` writes to the syllables of the rewritten
        segments (spec §5.4), after the segmental rewrite so that the upkeep has already
        placed inserted segments. A write to an unsyllabified segment is a no-op; in trace
        mode it produces a warning (spec §5.4, §12)."""
        i = piece.i
        starts = []
        pos = i
        for out in piece.outs:
            starts.append(pos)
            pos += len(out)
        tier = form.syllables
        for p, off, spec, env, weak in piece.sylw:
            q = i + off if p < 0 else starts[p - i] + off
            new = tier.write(q, spec, env, weak) if tier is not None else None
            if new is None:
                if ctx is not None and ctx.trace is not None:
                    ctx.warn("rule %s%s (line %s): writing %s to unsyllabified segment %d has no effect (spec §5.4)"
                             % (self.id, " %r" % self.name if self.name else "",
                                self.loc.line if self.loc else "?", spec.canonical(), q))
                continue
            tier = new
        if tier is form.syllables:
            return form
        return form.with_syllables(tier, form.pending_syllable_marks)

    def output_ok(self, form: Form, i: int, j: int, env: Env) -> bool:
        """Output filters on a single-focus output whose replaced span is ``(i, j)``
        (spec §8.4)."""
        filters = self.ir.out_filters
        if not filters:
            return True
        lay = self.visibility(form)
        for _ in _filters(filters, form, i, j, env, lay, 0):
            return True
        return False

    # -- modes (spec §8.3) -----------------------------------------------------------------

    def mode(self, ctx: ApplyContext) -> str:
        """``simultaneous``, ``once`` or ``iterative``: the rule's own mode, else the
        ``DefaultMode`` setting (spec §8.3)."""
        m = self.ir.mode
        if m is None:
            return "once" if ctx.default_mode == "once" else "simultaneous"
        return m

    def apply(self, form: Form, ctx: ApplyContext) -> Outcome:
        """Apply the rule in its mode, honouring ``/:*``, ``/:L±``, pending modifiers and
        ``!only``/``!skip`` (spec §8.3, §8.6, §10.5)."""
        if not ctx.date_allows(self.date):
            return Outcome(form, False)
        if self.pending:
            return _handle_pending(self, self.pending, "rule %s" % (self.name or self.id), form, ctx)
        if self._name_blocked(ctx):
            return Outcome(form, False)
        lex = self.ir.restrictions.lexical
        if lex and not lexical_ok(lex, ctx.record.lexical):
            return Outcome(form, False)
        if self.p9 or ctx.p9:  # plan P9: /:D± /:C± /% /??? domains and constraints
            from .variants import apply_basic
            return apply_basic(self, form, ctx)
        if self.ir.tier:  # plan P8: /:T tier-only rule on the projected tier (spec §6.5)
            return _tiers.apply_tier_rule(self, form, ctx)
        if self.ir.resyllabify:
            form = resyllabify(form, ctx, self)
        if self.ir.repeat:
            return self._repeat(form, ctx)
        return self.run(form, ctx, self.mode(ctx))

    def run(self, form: Form, ctx: ApplyContext, mode: str) -> Outcome:
        """One application in ``mode`` without ``/:*`` (spec §8.3); traces a change, or a
        ``/:~`` match (design §10)."""
        if self.required and self.quick_reject(form):
            return Outcome(form, False)
        if mode == "simultaneous":
            new, foci, matched = self._simultaneous(form, ctx)
        elif mode == "once":
            new, foci, matched = self._once(form, ctx)
        else:
            new, foci, matched = self._iterative(form, ctx)
        tier = new.syllables
        if new is not form and tier is not None and tier.persistent:
            new = tier.syllabifier.syllabify(new)  # Persistent syllabification (spec §5.4, §5.7)
        if new is not form and new.tiers:
            new = _tiers.after_rule(new)  # plan P8: the tiers' OCP setting (spec §5.5)
        applied = new is not form or (self.weak and matched)
        if applied:
            ctx.add_trace(TraceStep(self.id, self.name, self.loc, form, new, tuple(foci), None, ctx.depth))
        return Outcome(new, applied)

    def _simultaneous(self, form: Form, ctx: Optional[ApplyContext] = None):
        """Non-overlapping foci of the input form, each output-filtered on its own
        single-focus output, rewritten right to left (spec §8.3, §8.4)."""
        lay = self.visibility(form)
        kept: List[_Piece] = []
        spans: List[Tuple[int, int]] = []
        filtered = bool(self.ir.out_filters)
        for i, j, env in self.candidates(form, lay):
            span = (i, j)
            if any(_overlaps(span, s) for s in spans):
                continue
            pc = self.piece(form, i, j, env, lay)
            if filtered:
                single, _ch, total = self.rewrite(form, pc, ctx)
                if not self.output_ok(single, i, i + total, env):
                    continue
            kept.append(pc)
            spans.append(span)
        if not kept:
            return form, (), False
        new = form
        changed = False
        for pc in sorted(kept, key=lambda p: (p.i, p.j), reverse=True):
            new, ch, _t = self.rewrite(new, pc, ctx)
            changed = changed or ch
        return (new if changed else form), tuple(sorted(spans)), True

    def _first(self, form: Form, ok=None, ctx: Optional[ApplyContext] = None):
        """The first candidate that changes the form (or any, for ``/:~``) and passes the
        output filters: ``(i, j, new_form, changed, span_length)`` or ``None``."""
        lay = self.visibility(form)
        for i, j, env in self.candidates(form, lay):
            if ok is not None and not ok(i, j):
                continue
            pc = self.piece(form, i, j, env, lay)
            new, ch, total = self.rewrite(form, pc, ctx)
            if not ch and not self.weak:
                continue
            if not self.output_ok(new, i, i + total, env):
                continue
            return i, j, (new if ch else form), ch, total
        return None

    def _once(self, form: Form, ctx: Optional[ApplyContext] = None):
        """``/:1``: the first applicable focus (spec §8.3)."""
        hit = self._first(form, None, ctx)
        if hit is None:
            return form, (), False
        return hit[2], ((hit[0], hit[1]),), True

    def _iterative(self, form: Form, ctx: ApplyContext):
        """``/*``: apply, then search the updated form from the end (L→R) or start (R→L) of
        the rewritten span; at most ``MaxIterations`` applications (spec §8.3; design §10)."""
        cap = ctx.max_iterations
        d = self.direction
        cursor = 0 if d > 0 else form.n
        foci: List[Tuple[int, int]] = []
        count = 0
        cur = form
        while True:
            c = cursor
            hit = self._first(cur, (lambda i, j: i >= c) if d > 0 else (lambda i, j: j <= c), ctx)
            if hit is None:
                break
            i, j, cur, _ch, total = hit
            count += 1
            if count > cap:
                raise self._error("/* rule applied more than MaxIterations = %d times" % cap,
                                  hint="the rule keeps finding new foci; raise it with !set MaxIterations = n "
                                  "or add a context that stops it (spec §8.3)")
            foci.append((i, j))
            if d > 0:
                cursor = i + 1 if (i == j and total == 0) else i + total
                if cursor > cur.n:
                    break
            else:
                cursor = i - 1 if (i == j and total == 0) else i
                if cursor < 0:
                    break
        return cur, tuple(foci), bool(foci)

    def _repeat(self, form: Form, ctx: ApplyContext) -> Outcome:
        """``/:*``: re-apply (``/:1``, or ``/*`` if given) until nothing changes; a repeated
        form is a cycle error, and at most ``MaxIterations`` passes run (spec §8.3)."""
        mode = "iterative" if self.ir.mode == "iterative" else "once"
        cap = ctx.max_iterations
        seen = {form}
        cur = form
        applied = False
        passes = 0
        while True:
            out = self.run(cur, ctx, mode)
            if not out.applied:
                break
            applied = True
            if out.form == cur:
                break
            passes += 1
            if out.form in seen:
                raise self._error("/:* rule cycles: the form after pass %d already occurred" % passes,
                                  hint="a repeated rule must converge (spec §8.3)")
            if passes >= cap:
                raise self._error("/:* rule did not converge within MaxIterations = %d passes" % cap)
            seen.add(out.form)
            cur = out.form
        return Outcome(cur, applied)


def _filter_envs(f: IRFilter, form: Form, i: int, j: int, env: Env, lay) -> Iterator[Env]:
    """The environments under which filter pattern ``E`` matches (spec §8.4): anywhere in
    the form without ``___``, or anchored around the span ``(i, j)`` with it."""
    if f.has_locus:
        yield from match_context(f.left, f.right, form, i, j, env, lay)
        return
    seen = set()
    for _a, _b, e in find_all(f.anywhere, form, env, visible=lay):
        if e not in seen:
            seen.add(e)
            yield e


def _filters(filters: Sequence[IRFilter], form: Form, i: int, j: int, env: Env, lay, k: int) -> Iterator[Env]:
    """Thread ``env`` through ``filters[k:]`` (spec §8.4): a positive filter must match
    (its bindings are shared, with backtracking); a negative one must have no match."""
    if k == len(filters):
        yield env
        return
    f = filters[k]
    if f.positive:
        for e in _filter_envs(f, form, i, j, env, lay):
            yield from _filters(filters, form, i, j, e, lay, k + 1)
    else:
        for _ in _filter_envs(f, form, i, j, env, lay):
            return
        yield from _filters(filters, form, i, j, env, lay, k + 1)


# --------------------------------------------------------------------------------------------
# Groups, invocations, placeholders, commands (spec §8.8, §8.9)
# --------------------------------------------------------------------------------------------


def _form_filters_ok(filters: Sequence[IRFilter], form: Form) -> bool:
    """Whole-form group filters ``/:i±``/``/:o±`` without ``___`` (spec §8.8)."""
    for f in filters:
        hit = False
        for _ in _filter_envs(f, form, 0, 0, EMPTY, None):
            hit = True
            break
        if hit != f.positive:
            return False
    return True


def run_persistent(form: Form, ctx: ApplyContext) -> Form:
    """Re-run the active persistent rules to a fixed point (spec §8.9): in declaration
    order, repeating until a pass changes nothing, at most ``MaxIterations`` passes. Changes
    made here do not re-trigger the loop (item 4)."""
    if not ctx.persistent or ctx._in_persistent:
        return form
    ctx._in_persistent = True
    try:
        cap = ctx.max_iterations
        passes = 0
        while True:
            changed = False
            for p in list(ctx.persistent):
                out = p.apply(form, ctx)
                if out.form != form:
                    form = out.form
                    changed = True
            if not changed:
                return form
            passes += 1
            if passes >= cap:
                p = ctx.persistent[0]
                raise YascRuntimeError("persistent rules did not reach a fixed point within MaxIterations = %d "
                                       "passes" % cap, p.loc, rule_id=p.id, rule_name=p.name,
                                       hint="persistent rules must converge (spec §8.9)")
    finally:
        ctx._in_persistent = False


def run_sequence(members: Sequence["Rule"], form: Form, ctx: ApplyContext, kind: str = "seq") -> Outcome:
    """Run ``members`` as one group scope of ``kind`` ``seq``, ``and`` or ``or``
    (spec §8.8, §8.9; design §10). Persistent members join the active list after their
    first application and stay active until the scope ends; after every member that
    changes the form the active persistent rules run to a fixed point. ``and`` returns the
    original form with ``applied=False`` at the first member that does not apply (its
    trace entries are removed); ``or`` stops at the first member that applies. Commands
    (:class:`CommandItem`) neither apply nor fail."""
    n_persist = len(ctx.persistent)
    n_trace = len(ctx.trace) if ctx.trace is not None else 0
    cur = form
    applied_any = False
    try:
        for m in members:
            if isinstance(m, CommandItem):
                cur = m.apply(cur, ctx).form
                continue
            out = m.apply(cur, ctx)
            if m.persistent and not ctx._in_persistent:
                ctx.persistent.append(m)
            changed = out.form != cur
            cur = out.form
            if changed:
                cur = run_persistent(cur, ctx)
            if out.applied:
                applied_any = True
                if kind == "or":
                    return Outcome(cur, True)
            elif kind == "and":
                if ctx.trace is not None:
                    del ctx.trace[n_trace:]
                return Outcome(form, False)
    finally:
        del ctx.persistent[n_persist:]
    if kind == "and":
        return Outcome(cur, True)
    return Outcome(cur, applied_any)


class Group(Rule):
    """A rule group or ``Rules`` section (spec §8.8; design §10): ``kind`` is ``seq``,
    ``and`` or ``or``; ``members`` are built rules. Trailing modifiers: ``/:*`` (repeat the
    group until nothing changes), ``/:L±``, whole-form ``/:i±``/``/:o±`` filters (an output
    filter that fails undoes the group), ``/:~`` (the group counts as applied whenever it
    runs), ``/::`` and ``/"``. ``/:C±``, ``/:C*``, ``/:D±``, ``/%`` and ``/???`` are
    handled by :func:`yasc.variants.apply_group` (plan P9)."""

    def __init__(self, ir: IRGroup, builder: "Builder") -> None:
        self.ir = ir
        self.kind = ir.kind
        self.name = ir.name
        self.loc = ir.loc
        self.persistent = ir.persistent
        self.date = ir.date
        self.pending = _pending_phase(ir)
        self.members: Tuple[Rule, ...] = tuple(builder.build(m) for m in ir.members)
        # Plan P9: trailing /:D± /:C± /:C* /% /??? go through yasc.variants.apply_group.
        self.p9 = bool(ir.optional or ir.stochastic or ir.cyclic or ir.restrictions.dialect
                       or ir.restrictions.category)

    def apply(self, form: Form, ctx: ApplyContext) -> Outcome:
        """Run the group once, or to a fixed point with ``/:*`` (spec §8.8)."""
        ir = self.ir
        if self.date is not None and not ctx.date_allows(self.date):
            return Outcome(form, False)
        if self.pending:
            return _handle_pending(self, self.pending, "group %s" % (self.name or ""), form, ctx)
        if self.name is not None and self.name in ctx.skip:
            return Outcome(form, False)
        lex = ir.restrictions.lexical
        if lex and not lexical_ok(lex, ctx.record.lexical):
            return Outcome(form, False)
        if self.p9:  # plan P9 (yasc.variants.apply_group re-enters once through p9_bypass)
            if ctx.p9_bypass is self:
                ctx.p9_bypass = None
            else:
                from .variants import apply_group
                return apply_group(self, form, ctx)
        if ir.in_filters and not _form_filters_ok(ir.in_filters, form):
            return Outcome(form, False)
        only = bool(ctx.only) and self.name in ctx.only
        n_trace = len(ctx.trace) if ctx.trace is not None else 0
        ctx.depth += 1
        if only:
            ctx._only_depth += 1
        saved_date = ctx.group_date
        if self.date is not None:
            ctx.group_date = self.date
        try:
            out = self._repeat(form, ctx) if ir.repeat else run_sequence(self.members, form, ctx, self.kind)
        finally:
            ctx.depth -= 1
            ctx.group_date = saved_date
            if only:
                ctx._only_depth -= 1
        if ir.out_filters and not _form_filters_ok(ir.out_filters, out.form):
            if ctx.trace is not None:
                del ctx.trace[n_trace:]
            return Outcome(form, False)
        if ir.weak:
            return Outcome(out.form, True)
        return out

    def _repeat(self, form: Form, ctx: ApplyContext) -> Outcome:
        """``]] /:*``: repeat the group until nothing changes; a repeated form is a cycle
        error, and at most ``MaxIterations`` passes run (spec §8.3, §8.8)."""
        cap = ctx.max_iterations
        seen = {form}
        cur = form
        applied = False
        passes = 0
        while True:
            out = run_sequence(self.members, cur, ctx, self.kind)
            if not out.applied:
                break
            applied = True
            if out.form == cur:
                break
            passes += 1
            if out.form in seen:
                raise self._error("/:* group cycles: the form after pass %d already occurred" % passes)
            if passes >= cap:
                raise self._error("/:* group did not converge within MaxIterations = %d passes" % cap)
            seen.add(out.form)
            cur = out.form
        return Outcome(cur, applied)


class Invoke(Rule):
    """``$Name`` inside Rules: run the named section, group or rule (spec §8.8)."""

    def __init__(self, ir: IRInvoke, builder: "Builder") -> None:
        self.ir = ir
        self.name = None
        self.loc = ir.loc
        self.target = builder.build(ir.target)

    def apply(self, form: Form, ctx: ApplyContext) -> Outcome:
        """Apply the invoked target (spec §8.8)."""
        return self.target.apply(form, ctx)


class PendingRule(Rule):
    """An :class:`~yasc.ir.IRPlaceholder`: raises :class:`NotImplementedYet` when run, or
    is skipped with ``ApplyContext.skip_pending`` (design §13 entry 77)."""

    def __init__(self, ir: IRPlaceholder) -> None:
        self.ir = ir
        self.id = ir.id
        self.name = ir.name
        self.loc = ir.loc
        self.phase = ir.phase
        self.date = ir.date

    def apply(self, form: Form, ctx: ApplyContext) -> Outcome:
        """Refuse or skip (design §13 entry 97; plan P5 "pending constructs"); a placeholder
        outside the date filter is silently inactive (spec §8.10)."""
        if not ctx.date_allows(self.date):
            return Outcome(form, False)
        return _handle_pending(self, self.phase, "%s %s" % (self.ir.what, self.name or self.id or ""), form, ctx)


class CommandItem(Rule):
    """An ``IRCommand`` or ``IRAssign`` member of a group (spec §10.5, §10.1). The runtime
    (plan P6) executes it through ``ApplyContext.on_command``; the engine only handles
    pending commands (``!syllabify`` and the like)."""

    def __init__(self, ir) -> None:
        self.ir = ir
        self.loc = ir.loc
        self.pending = getattr(ir, "pending", None)

    def apply(self, form: Form, ctx: ApplyContext) -> Outcome:
        """Hand the command to the runtime; never counts as applied (spec §8.8)."""
        if self.pending:
            return _handle_pending(self, self.pending, "!%s" % self.ir.name, form, ctx)
        if ctx.on_command is None:
            return Outcome(form, False)
        new = ctx.on_command(self.ir, form, ctx)
        return Outcome(form if new is None else new, False)


# --------------------------------------------------------------------------------------------
# Building and entry points
# --------------------------------------------------------------------------------------------


class Builder:
    """Builds executable rules from IR records, once per record (design §13 entry 87)."""

    def __init__(self) -> None:
        self._memo: Dict[int, Tuple[Any, Rule]] = {}

    def build(self, ir) -> Rule:
        """The executable :class:`Rule` for an IR record, or the rule itself (design §10,
        §13 entry 87)."""
        if isinstance(ir, Rule):
            return ir
        hit = self._memo.get(id(ir))
        if hit is not None and hit[0] is ir:
            return hit[1]
        if isinstance(ir, IRBasicRule):
            r: Rule = BasicRule(ir)
        elif isinstance(ir, IRGroup):
            r = Group(ir, self)
        elif isinstance(ir, IRInvoke):
            r = Invoke(ir, self)
        elif isinstance(ir, IRPlaceholder):
            r = PendingRule(ir)
        elif isinstance(ir, (IRCommand, IRAssign)):
            r = CommandItem(ir)
        else:
            raise TypeError("cannot build a rule from %r" % (ir,))
        self._memo[id(ir)] = (ir, r)
        return r


def build(ir, builder: Optional[Builder] = None) -> Rule:
    """Build one IR record into an executable :class:`Rule` (design §10)."""
    return (builder or Builder()).build(ir)


def build_rules(compiled, builder: Optional[Builder] = None) -> Dict[int, Rule]:
    """Every basic rule and placeholder of a compiled script, by rule id (design §10)."""
    b = builder or Builder()
    return {r.id: b.build(r) for r in compiled.rules if getattr(r, "id", None) is not None}


def apply_rule(rule_or_ir, form: Form, ctx: Optional[ApplyContext] = None) -> Outcome:
    """Apply one rule or group (built or IR) to ``form`` (spec §8.2–§8.3), then run any
    persistent rules active in ``ctx`` if the form changed (spec §8.9)."""
    ctx = ctx if ctx is not None else ApplyContext()
    rule = ctx.builder.build(rule_or_ir)
    out = rule.apply(form, ctx)
    if out.form != form:
        return Outcome(run_persistent(out.form, ctx), out.applied)
    return out


def run_section(compiled, name_or_ir, form: Form, ctx: Optional[ApplyContext] = None) -> Outcome:
    """Run a ``Rules`` section (or any group, rule or name in ``compiled.named``) on
    ``form`` (spec §8.8). ``name_or_ir`` is a section name without ``$``, a ``/"`` name or
    an IR record."""
    if isinstance(name_or_ir, str):
        key = name_or_ir.lstrip("$")
        target = compiled.rule_sections.get(key) or compiled.named.get(key)
        if target is None:
            raise KeyError("no Rules section or named rule %r" % name_or_ir)
    else:
        target = name_or_ir
    return apply_rule(target, form, ctx)
