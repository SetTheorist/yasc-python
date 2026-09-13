"""Advanced rule application (plan P9; decisions in ``docs/decisions/P9.md``).

* category restrictions and domains ``/:C±`` and cyclic application ``/:C*`` (spec §8.6);
* dialect restrictions ``/:D±`` (spec §8.6, §10.4);
* stochastic ``/%n`` and optional ``/???`` rules, with or without ``:each`` (spec §8.7);
* derivation variants: decision points, labels and exploration (spec §8.11);
* phonotactic constraints as output filters (spec §4.6).

:mod:`yasc.rules` sends a basic rule or a group here only when it uses one of these
features, or when a domain or enforced constraints are active in the
:class:`~yasc.rules.ApplyContext` (``ctx.p9``). Scripts that use none of them keep the P5
path unchanged (plan P9 "no new slow path").

**Variants by replay** (P9 decision 5). A rule cannot return several forms, so a decision
point (:func:`choose`) takes the first alternative, and :func:`explore` re-runs the
computation once for each other alternative, replaying the earlier choices. The runtime
explores one top-level statement at a time and merges identical variants in between.
"""

from typing import Any, Callable, List, Optional, Sequence, Tuple

from .errors import YascRuntimeError
from .form import Form
from .marks import Bracket, Mark
from .matcher import find_all
from .rules import Outcome, TraceStep, _overlaps, resyllabify
from . import tiers as _tiers  # plan P8: tiers inside domains (P8 decision 21)

__all__ = [
    "DomainView", "Explorer", "choose", "explore", "join_label", "names_ok", "draw", "constraint_violations",
    "constraints_ok", "apply_basic", "apply_group", "run_cyclic", "innermost", "rule_token",
]

_PHRASE = frozenset({Mark.PHRASE})


# --------------------------------------------------------------------------------------------
# Domains (spec §8.6)
# --------------------------------------------------------------------------------------------


class DomainView:
    """A read-only window over the gaps ``a..b`` of a form: the form a rule sees inside a
    domain (spec §8.6: "material outside it is invisible"; P9 decision 1).

    It implements :class:`~yasc.marks.FormLike` in window coordinates (gaps ``0..b-a``) and
    also ``segs``, which :meth:`yasc.rules.BasicRule.piece` reads. Views and syllable
    features resolve through the full form. Only the brackets inside the window are
    visible. The window edges carry the phrase mark, as the edges of a form do, so ``#``
    matches at a domain edge (spec §8.5: the boundaries of invisible material are unioned
    into the visible gap).
    """

    __slots__ = ("form", "a", "b", "n", "segs", "brackets")

    def __init__(self, form: Form, a: int, b: int) -> None:
        self.form = form
        self.a = a
        self.b = b
        self.n = b - a
        self.segs = form.segs[a:b]
        self.brackets = tuple(Bracket(x.label, x.open_gap - a, x.close_gap - a) for x in form.brackets
                              if a <= x.open_gap and x.close_gap <= b)

    def seg(self, i: int):
        """Segment ``i`` of the window (spec §5.1)."""
        return self.segs[i]

    def view(self, i: int):
        """The full form's view of segment ``a+i`` (syllable features resolve; spec §5.4)."""
        return self.form.view(self.a + i)

    def gap_marks(self, g: int):
        """The marks of gap ``a+g``, plus the phrase mark at the window edges (spec §5.2)."""
        m = self.form.gap_marks(self.a + g)
        if g == 0 or g == self.n:
            m = m | _PHRASE
        return m

    def __len__(self) -> int:
        return self.n


def _view(form: Form, a: int, b: int):
    if a == 0 and b == form.n:
        return form
    return DomainView(form, a, b)


def _contains(outer: Bracket, inner: Bracket) -> bool:
    return outer.open_gap <= inner.open_gap and inner.close_gap <= outer.close_gap


def innermost(brackets: Sequence[Bracket]) -> List[int]:
    """Indices of the brackets that contain no other bracket (spec §8.6 ``/:C*`` step 1).
    Nested brackets directly follow their parent in document order (design §13 entry 48)."""
    last = len(brackets) - 1
    return [k for k, b in enumerate(brackets) if k == last or not _contains(b, brackets[k + 1])]


def _window(form: Form, key: Optional[int], own: bool) -> Tuple[int, int, Tuple[Tuple[int, int], ...]]:
    """``(a, b, inner)`` for domain ``key`` (a bracket index, or ``None`` for the whole form).
    With ``own``, ``inner`` lists the spans (window coordinates) of the brackets nested in
    the domain: foci inside them belong to those brackets, not to this domain (P9 decision 2)."""
    bs = form.brackets
    if key is None or key >= len(bs):
        a, b = 0, form.n
        nested = bs if own else ()
    else:
        a, b = bs[key].open_gap, bs[key].close_gap
        nested = []
        if own:
            for x in bs[key + 1:]:
                if not (a <= x.open_gap and x.close_gap <= b):
                    break
                nested.append(x)
    return a, b, tuple((x.open_gap - a, x.close_gap - a) for x in nested)


def _owned(inner, i: int, j: int) -> bool:
    return not any(o <= i and j <= c for o, c in inner)


def names_ok(restrictions, name: Optional[str]) -> bool:
    """``/:D±`` or ``/:C±`` restrictions (:class:`~yasc.ir.IRNames`) against the current
    dialect or domain label (``None``: none): ``+`` needs the name listed, ``-`` needs it
    absent, and every restriction must hold (spec §8.6)."""
    return all((name in r.names) == r.positive for r in restrictions)


# --------------------------------------------------------------------------------------------
# Decisions and exploration (spec §8.11)
# --------------------------------------------------------------------------------------------


class Explorer:
    """One derivation path being run (spec §8.11; P9 decision 5). ``forced`` are the choices
    to replay, by decision index; later decisions take alternative 0. ``choices`` and
    ``options`` record every decision made, ``tokens`` the label tokens chosen. ``marker``
    (set by the runtime) is called at the decision where this path leaves its parent; its
    result is kept in ``mark``, so output from before that point can be dropped as a
    duplicate. ``on_token`` is called with each label token as it is chosen."""

    def __init__(self, forced: Sequence[int] = ()) -> None:
        self.forced = tuple(forced)
        self.choices: List[int] = []
        self.options: List[int] = []
        self.tokens: List[str] = []
        self.marker: Optional[Callable[[], Any]] = None
        self.mark: Any = None
        self.on_token: Optional[Callable[[str], None]] = None
        self.where: Any = None

    def choose(self, tokens: Sequence[Optional[str]], where: Any = None) -> int:
        """Pick one of ``tokens`` (one per alternative) and return its index. A single
        alternative is not a decision, but its token is still recorded."""
        if len(tokens) > 1:
            d = len(self.choices)
            k = self.forced[d] if d < len(self.forced) else 0
            if d == len(self.forced) - 1 and self.marker is not None:
                self.mark = self.marker()
            if k >= len(tokens):  # pragma: no cover - replay is deterministic
                raise YascRuntimeError("internal error: a replayed derivation took a different path")
            self.choices.append(k)
            self.options.append(len(tokens))
            self.where = where
        else:
            k = 0
        tok = tokens[k] if tokens else None
        if tok is not None:
            self.tokens.append(tok)
            if self.on_token is not None:
                self.on_token(tok)
        return k


def choose(ctx, tokens: Sequence[Optional[str]], where: Any = None) -> int:
    """A decision point (spec §8.11): the index of the alternative taken on the current path
    (``ctx.explorer``). Without an explorer the first alternative is taken."""
    ex = getattr(ctx, "explorer", None)
    if ex is None:
        return 0
    return ex.choose(tokens, where)


def explore(run_path: Callable[[Explorer], Any], limit: int) -> List[Tuple[Explorer, Any]]:
    """Run ``run_path(explorer)`` once per derivation path (spec §8.11; P9 decision 5):
    every combination of alternatives at the decision points it meets. Returns
    ``(explorer, value)`` pairs in derivation order (first alternatives first). More than
    ``limit`` paths raise :class:`~yasc.errors.YascRuntimeError` (``MaxVariants``)."""
    done: List[Tuple[Explorer, Any]] = []
    stack: List[Tuple[int, ...]] = [()]
    while stack:
        forced = stack.pop()
        ex = Explorer(forced)
        done.append((ex, run_path(ex)))
        # Depth-first in label order: the deepest decision's next alternative is on top.
        for d in range(len(forced), len(ex.choices)):
            for k in range(ex.options[d] - 1, 0, -1):
                stack.append(tuple(ex.choices[:d]) + (k,))
        if len(done) + len(stack) > limit:
            w = ex.where
            raise YascRuntimeError("more than MaxVariants = %d variants" % limit, getattr(w, "loc", None),
                                   rule_id=getattr(w, "id", None), rule_name=getattr(w, "name", None),
                                   hint="optional rules and paradigms multiply the variants; raise the cap with "
                                   "!set MaxVariants = n (spec §8.11)")
    done.sort(key=lambda p: p[0].choices)
    return done


def join_label(label: Optional[str], token: str) -> str:
    """Append a decision ``token`` to a variant label; a merged label ``a|b`` gets it on each
    alternative (spec §8.11; P9 decision 6)."""
    if not label:
        return token
    return "|".join((alt + " " + token) if alt else token for alt in label.split("|"))


def rule_token(rule) -> str:
    """The label name of a rule or group: its ``/"`` name, else ``R<id>`` for a basic rule
    or ``G<line>`` for a group (spec §8.11 "R12:yes")."""
    if rule.name:
        return rule.name
    if getattr(rule, "id", None) is not None:
        return "R%d" % rule.id
    return "G%s" % (rule.loc.line if rule.loc else "")


def draw(ctx, percent: float) -> bool:
    """One stochastic trial with the seeded RNG: true with probability ``percent`` %
    (spec §8.7)."""
    return ctx.rng.random() * 100.0 < percent


# --------------------------------------------------------------------------------------------
# Constraints (spec §4.6)
# --------------------------------------------------------------------------------------------


def constraint_violations(constraints, form) -> List[Tuple[Any, int, int]]:
    """Every match of every ``Constraint *`` pattern in ``form`` as ``(constraint, start,
    end)`` (spec §4.6; the API for word generators)."""
    out = []
    for c in constraints:
        seen = set()
        for s, e, _env in find_all(c.nfa, form):
            if (s, e) not in seen:
                seen.add((s, e))
                out.append((c, s, e))
    return out


def constraints_ok(constraints, form, i: int, j: int, fs=None) -> bool:
    """True unless a constraint (of phonology ``fs``, if given) matches across the
    rewritten span ``i..j`` of an output form (spec §4.6 as ``/:o-`` filters; P9
    decision 9): a match must overlap the span, or cross gap ``i`` when the span is empty."""
    for c in constraints:
        if fs is not None and c.phonology is not None and c.phonology is not fs:
            continue
        for s, e, _env in find_all(c.nfa, form):
            if (s < j and e > i) if j > i else (s < i < e):
                return False
    return True


# --------------------------------------------------------------------------------------------
# Basic rules in a domain, with per-focus decisions and constraints
# --------------------------------------------------------------------------------------------


def _shift(pc, a: int):
    """A rewrite piece computed on a window starting at gap ``a``, in form coordinates."""
    if a == 0:
        return pc
    return pc._replace(i=pc.i + a, j=pc.j + a,
                       sylw=tuple((p + a if p >= 0 else p, off, spec, env, weak)
                                  for p, off, spec, env, weak in pc.sylw), env=_tiers.shift_env(pc.env, a))


def _out_ok(rule, ctx, form: Form, a: int, b: int, i: int, j: int, env) -> bool:
    """Output filters and enforced constraints on the output window ``a..b`` (spec §8.4, §4.6)."""
    view = _view(form, a, b)
    if rule.ir.out_filters and not rule.output_ok(view, i, j, env):
        return False
    cons = ctx.constraints
    return not cons or constraints_ok(cons, view, i, j, rule.fs)


def _simultaneous(rule, form: Form, ctx, key, own, decide):
    """Simultaneous mode inside a domain (spec §8.3); ``decide()`` keeps or drops each
    selected focus that changes the form (``:each``, spec §8.7)."""
    a, b, inner = _window(form, key, own)
    view = _view(form, a, b)
    lay = rule.visibility(view)
    check = bool(rule.ir.out_filters or ctx.constraints)
    kept, spans, done = [], [], []
    for i, j, env in rule.candidates(view, lay):
        if inner and not _owned(inner, i, j):
            continue
        span = (i, j)
        if any(_overlaps(span, s) for s in spans):
            continue
        pc = _shift(rule.piece(view, i, j, env, lay), a)
        if check or decide is not None:
            single, ch, total = rule.rewrite(form, pc, ctx)
            if check and not _out_ok(rule, ctx, single, a, b + single.n - form.n, i, i + total, env):
                continue
            spans.append(span)
            if decide is not None and ch and not decide():
                continue
        else:
            spans.append(span)
        kept.append(pc)
        done.append((i + a, j + a))
    if not kept:
        return form, (), False
    new, changed = form, False
    for pc in sorted(kept, key=lambda p: (p.i, p.j), reverse=True):
        new, ch, _t = rule.rewrite(new, pc, ctx)
        changed = changed or ch
    return (new if changed else form), tuple(sorted(done)), True


def _first(rule, form: Form, ctx, key, own, ok, decide):
    """The first applicable focus in a domain that ``ok(i, j)`` (window coordinates) admits
    and ``decide()`` keeps: ``(i, j, new_form, changed, span_length, a)`` or ``None``."""
    a, b, inner = _window(form, key, own)
    view = _view(form, a, b)
    lay = rule.visibility(view)
    for i, j, env in rule.candidates(view, lay):
        if inner and not _owned(inner, i, j):
            continue
        if ok is not None and not ok(i, j):
            continue
        pc = _shift(rule.piece(view, i, j, env, lay), a)
        new, ch, total = rule.rewrite(form, pc, ctx)
        if not ch and not rule.weak:
            continue
        if not _out_ok(rule, ctx, new, a, b + new.n - form.n, i, i + total, env):
            continue
        if decide is not None and ch and not decide():
            continue
        return i, j, (new if ch else form), ch, total, a
    return None


def _iterative(rule, form: Form, ctx, key, own, decide):
    """``/*`` inside a domain (spec §8.3; design §13 entry 91). A dropped focus is skipped
    and the search continues after it."""
    cap = ctx.max_iterations
    d = rule.direction
    cursor: Optional[int] = None
    foci: List[Tuple[int, int]] = []
    cur = form
    while True:
        c = cursor
        ok = None if c is None else ((lambda i, j: i >= c) if d > 0 else (lambda i, j: j <= c))
        hit = _first(rule, cur, ctx, key, own, ok, decide)
        if hit is None:
            break
        i, j, cur, _ch, total, a = hit
        if len(foci) >= cap:
            raise rule._error("/* rule applied more than MaxIterations = %d times" % cap,
                              hint="raise it with !set MaxIterations = n or add a context that stops it (spec §8.3)")
        foci.append((i + a, j + a))
        if d > 0:
            cursor = i + 1 if (i == j and total == 0) else i + total
            a2, b2, _ = _window(cur, key, own)
            if cursor > b2 - a2:
                break
        else:
            cursor = i - 1 if (i == j and total == 0) else i
            if cursor < 0:
                break
    return cur, tuple(foci), bool(foci)


def _run(rule, form: Form, ctx, mode: str, key, own, decide) -> Outcome:
    """One application in ``mode`` inside a domain, traced like
    :meth:`yasc.rules.BasicRule.run` (spec §8.3; design §10)."""
    if rule.required and rule.quick_reject(form):
        return Outcome(form, False)
    if mode == "simultaneous":
        new, foci, matched = _simultaneous(rule, form, ctx, key, own, decide)
    elif mode == "once":
        hit = _first(rule, form, ctx, key, own, None, decide)
        new, foci, matched = (form, (), False) if hit is None else \
            (hit[2], ((hit[0] + hit[5], hit[1] + hit[5]),), True)
    else:
        new, foci, matched = _iterative(rule, form, ctx, key, own, decide)
    tier = new.syllables
    if new is not form and tier is not None and tier.persistent:
        new = tier.syllabifier.syllabify(new)
    if new is not form and new.tiers:
        new = _tiers.after_rule(new)  # plan P8: the tiers' OCP setting (spec §5.5)
    applied = new is not form or (rule.weak and matched)
    if applied:
        ctx.add_trace(TraceStep(rule.id, rule.name, rule.loc, form, new, tuple(foci), None, ctx.depth))
    return Outcome(new, applied)


def _apply_in(rule, form: Form, ctx, key, own, decide) -> Outcome:
    """The rule in its mode, with ``/:*`` (spec §8.3; design §13 entry 92), in a domain."""
    if rule.ir.tier:  # plan P8: a /:T rule sees the autosegments of the domain (P8 decision 21)
        return _tiers.apply_tier_rule(rule, form, ctx, _window(form, key, own)[:2])
    if not rule.ir.repeat:
        return _run(rule, form, ctx, rule.mode(ctx), key, own, decide)
    mode = "iterative" if rule.ir.mode == "iterative" else "once"
    cap = ctx.max_iterations
    seen = {form}
    cur, applied, passes = form, False, 0
    while True:
        out = _run(rule, cur, ctx, mode, key, own, decide)
        if not out.applied:
            break
        applied = True
        if out.form == cur:
            break
        passes += 1
        if out.form in seen:
            raise rule._error("/:* rule cycles: the form after pass %d already occurred" % passes,
                              hint="a repeated rule must converge (spec §8.3)")
        if passes >= cap:
            raise rule._error("/:* rule did not converge within MaxIterations = %d passes" % cap)
        seen.add(out.form)
        cur = out.form
    return Outcome(cur, applied)


def _domains(form: Form):
    """``/:C±`` domains of a rule outside cyclic application: every bracket, innermost and
    rightmost first, then the whole form (P9 decision 2)."""
    return list(range(len(form.brackets) - 1, -1, -1)) + [None]


def _body(rule, form: Form, ctx, decide) -> Outcome:
    ir = rule.ir
    cat = ir.restrictions.category
    dom = ctx.domain
    if dom is not None:
        key, label, own = dom
        if cat and not names_ok(cat, label):
            return Outcome(form, False)
        return _apply_in(rule, form, ctx, key, own, decide)
    if cat:
        cur, applied = form, False
        for key in _domains(form):
            label = cur.brackets[key].label if key is not None else None
            if names_ok(cat, label):
                out = _apply_in(rule, cur, ctx, key, True, decide)
                cur, applied = out.form, applied or out.applied
        return Outcome(cur, applied)
    if decide is None and not ctx.constraints:
        if ir.tier:  # plan P8: tier-only rule (spec §6.5)
            return _tiers.apply_tier_rule(rule, form, ctx)
        return rule._repeat(form, ctx) if ir.repeat else rule.run(form, ctx, rule.mode(ctx))
    return _apply_in(rule, form, ctx, None, False, decide)


def apply_basic(rule, form: Form, ctx) -> Outcome:
    """A basic rule with P9 features (called from :meth:`yasc.rules.BasicRule.apply` after
    the date, name and lexical checks): ``/:D±``, ``/%n[:each]``, ``/???[:each]``, ``/:C±``
    or an active domain, and enforced constraints (spec §8.6, §8.7, §8.11, §4.6)."""
    ir = rule.ir
    if ir.restrictions.dialect and not names_ok(ir.restrictions.dialect, ctx.record.dialect):
        return Outcome(form, False)
    st, opt = ir.stochastic, ir.optional
    if st is not None and not st.each and not draw(ctx, st.percent):
        return Outcome(form, False)
    decide = None
    if st is not None and st.each:
        decide = lambda: draw(ctx, st.percent)  # noqa: E731
    if opt is not None and opt.each:
        toks = (rule_token(rule) + ":yes", rule_token(rule) + ":no")
        prev = decide
        decide = lambda: (prev is None or prev()) and choose(ctx, toks, rule) == 0  # noqa: E731
    start = form
    if ir.resyllabify:
        form = resyllabify(form, ctx, rule)
    if opt is None or opt.each:
        return _body(rule, form, ctx, decide)
    n_trace = len(ctx.trace) if ctx.trace is not None else 0
    out = _body(rule, form, ctx, decide)
    if out.form != form and choose(ctx, (rule_token(rule) + ":yes", rule_token(rule) + ":no"), rule) == 1:
        if ctx.trace is not None:
            del ctx.trace[n_trace:]
        return Outcome(start, False)
    return out


# --------------------------------------------------------------------------------------------
# Groups and cyclic application (spec §8.6, §8.8)
# --------------------------------------------------------------------------------------------


class _Domain:
    """Context manager: run with ``ctx.domain = (key, label, own)`` (P9 decision 3)."""

    def __init__(self, ctx, dom) -> None:
        self.ctx, self.dom = ctx, dom

    def __enter__(self):
        self.saved = (self.ctx.domain, self.ctx.p9)
        self.ctx.domain, self.ctx.p9 = self.dom, True
        return self

    def __exit__(self, *exc) -> None:
        self.ctx.domain, self.ctx.p9 = self.saved


def _plain_group(group, form: Form, ctx) -> Outcome:
    """Run ``group`` through the P5 path of :meth:`yasc.rules.Group.apply` (filters, repeat,
    trace depth), skipping the P9 hook once."""
    ctx.p9_bypass = group
    try:
        return group.apply(form, ctx)
    finally:
        ctx.p9_bypass = None


def apply_group(group, form: Form, ctx) -> Outcome:
    """A group with trailing P9 modifiers (called from :meth:`yasc.rules.Group.apply`):
    ``/:D±``, ``/%n`` (one trial per invocation, ``:each`` included), ``/???`` (one fork per
    invocation), ``/:C±`` and ``/:C*`` (spec §8.8, §8.6, §8.7; P9 decision 4)."""
    ir = group.ir
    if ir.restrictions.dialect and not names_ok(ir.restrictions.dialect, ctx.record.dialect):
        return Outcome(form, False)
    if ir.stochastic is not None and not draw(ctx, ir.stochastic.percent):
        return Outcome(form, False)
    if ir.optional is None:
        return _group_body(group, form, ctx)
    n_trace = len(ctx.trace) if ctx.trace is not None else 0
    out = _group_body(group, form, ctx)
    if out.form != form and choose(ctx, (rule_token(group) + ":yes", rule_token(group) + ":no"), group) == 1:
        if ctx.trace is not None:
            del ctx.trace[n_trace:]
        return Outcome(form, False)
    return out


def _group_body(group, form: Form, ctx) -> Outcome:
    ir = group.ir
    if ir.cyclic:
        return run_cyclic(group, form, ctx)
    cat = ir.restrictions.category
    if not cat:
        return _plain_group(group, form, ctx)
    if ctx.domain is not None:
        if not names_ok(cat, ctx.domain[1]):
            return Outcome(form, False)
        return _plain_group(group, form, ctx)
    cur, applied = form, False
    for key in _domains(form):
        label = cur.brackets[key].label if key is not None else None
        if names_ok(cat, label):
            with _Domain(ctx, (key, label, True)):
                out = _plain_group(group, cur, ctx)
            cur, applied = out.form, applied or out.applied
    return Outcome(cur, applied)


def run_cyclic(group, form: Form, ctx) -> Outcome:
    """``/:C*`` (spec §8.6): apply the group once to each innermost bracket (its domain, with
    ``/:C±`` checked against its label), erase those brackets, and repeat; finally apply it
    once to the whole form, unlabelled. Inside another cycle it runs as an ordinary group
    in the current domain (P9 decision 3)."""
    cat = group.ir.restrictions.category
    if ctx.domain is not None:
        if cat and not names_ok(cat, ctx.domain[1]):
            return Outcome(form, False)
        return _plain_group(group, form, ctx)
    cur, applied = form, False
    while cur.brackets:
        doms = innermost(cur.brackets)
        for key in doms:
            label = cur.brackets[key].label
            if not cat or names_ok(cat, label):
                with _Domain(ctx, (key, label, False)):
                    out = _plain_group(group, cur, ctx)
                cur, applied = out.form, applied or out.applied
        gone = set(doms)
        cur = cur.with_brackets(b for k, b in enumerate(cur.brackets) if k not in gone)
    if not cat or names_ok(cat, None):
        with _Domain(ctx, (None, None, False)):
            out = _plain_group(group, cur, ctx)
        cur, applied = out.form, applied or out.applied
    return Outcome(cur, applied)
