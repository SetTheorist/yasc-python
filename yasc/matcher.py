"""Anchored NFA simulation with binding environments (spec §6.3, §6.4, §8.2, §8.5; design §6).

Public API
==========

* :func:`match_anchored` — all matches of an NFA starting exactly at one gap, forward
  (``direction=+1``) or backward (``direction=-1``), as ``[(end_gap, Env), ...]``.
* :func:`find_all` — unanchored search: every ``(start_gap, end_gap, Env)``, start gaps in
  rule-direction order (focus search, filters).
* :func:`match_context` — the Envs under which a context ``C ___ D`` holds around a
  focus ``(i, j)`` (spec §8.2 step 2).
* :class:`Visibility` — a precomputed visibility mask (spec §8.5); every function also
  accepts a plain tuple of bools or ``None``.

How the rule engine (plan P5) combines them (spec §8.2; design §10)
====================================================================

At load time (P4) each basic rule compiles::

    lhs      = compile_pattern(number_lhs(expanded_lhs))     # Capture(0..k)
    C, D     = split_at_locus(context)                        # for every / and /! context
    ctx      = (compile_pattern(C).reversed(), compile_pattern(D))

At run time, for a form and its visibility mask::

    vis = Visibility.of(form, mask)                  # once per rule application
    for i, j, env in find_all(lhs, form, EMPTY, direction=rule_dir, visible=vis,
                              first_specs=lhs.first_specs):
        # positive contexts, conjunctive, with backtracking (bindings thread D -> C -> next)
        for env2 in chain_contexts(pos_ctx, i, j, env):          # P5 recursion over contexts
            # negative contexts: fail if any match exists (bindings discarded)
            if any(True for _ in match_context(c_rev, d, form, i, j, env2, vis)): ...

where ``chain_contexts`` is a small recursion: ``for e1 in match_context(*ctx0, form, i, j,
env, vis): for e2 in chain(rest, e1): yield e2``. ``env.cap(n)`` gives the span of LHS item
``n`` (``$0``: the whole focus), and ``env.alt(id)`` the alternative index for class
correspondence (spec §8.2.4). Filters (spec §8.4) use :func:`find_all` on the input or
output form, or :func:`match_context` when they contain ``___``.

Semantics
=========

* **Threads** are ``(state, env)`` pairs, deduplicated per gap keeping the first
  occurrence, so the preference order of the NFA's edges (greedy loops, textual
  alternatives) is preserved. Every environment a spec yields (e.g. one per preimage of an
  op-variable) becomes its own thread (R3).
* **Result order** (spec §6.3): by end gap, the longest match first (furthest from the start
  gap in the matching direction), then by thread preference.
* **Captures** are stored in the Env as ``(i, j)`` spans with ``i <= j`` in form coordinates,
  whichever direction was used. ``$n`` (BACKREF) matches the visible segments of the
  captured span, segment for segment, with strict equality of the :class:`Segment` objects
  (spec §6.4).
* **Skipping** (spec §6.1): stepping from one segment to the next crosses the gap whatever
  marks it carries; boundaries are checked only where the pattern asserts them.
* **Visibility** (spec §8.5): invisible segments are skipped like gaps. A run of real gaps
  ``lo..hi`` separated only by invisible segments forms one *virtual gap*, whose marks (and
  bracket edges) are the union of those gaps'. Match positions are real gaps: after
  consuming a segment, the position is the gap right next to it (end gaps are tight), and
  :func:`find_all` tries each virtual gap once, at the real gap just before the next
  visible segment (or ``n``).
* **Caching**: variable-free, plain specs are matched once per interned segment; the cache
  lives on the NFA (``nfa.match_cache``) and is shared with its reversal (design §6).
"""

from typing import Iterator, List, Optional, Sequence, Tuple, Union

from .nfa import ALT, ASSERT, BACKREF, CAP_CLOSE, CAP_OPEN, FLOAT, NFA, SEG
from .segment import EMPTY, Env

__all__ = ["Visibility", "match_anchored", "find_all", "match_context"]

# Modes of prepared consuming edges.
_M_ANY = 0      # '...': any segment
_M_CACHED = 1   # variable-free plain spec: memoised per segment
_M_SEG = 2      # plain spec with variables: match on the Segment
_M_VIEW = 3     # spec reading syllable/tier features: match on form.view(k)
_M_BREF = 4     # back-reference


class Visibility:
    """A precomputed visibility mask over a form's segments (spec §8.5; design §10).

    ``mask[k]`` is True if segment ``k`` is visible. For each real gap ``g``:

    * ``nxt[g]`` — the first visible segment index ``>= g``, or ``n``;
    * ``prv[g]`` — the last visible segment index ``< g``, or ``-1``;
    * the virtual gap containing ``g`` spans the real gaps ``prv[g]+1 .. nxt[g]``.

    Build with :meth:`of`, which returns ``None`` when every segment is visible (the fast
    path).
    """

    __slots__ = ("n", "mask", "nxt", "prv")

    def __init__(self, n: int, mask: Sequence[bool]) -> None:
        mask = tuple(bool(x) for x in mask)
        if len(mask) != n:
            raise ValueError("visibility mask has %d entries but the form has %d segments" % (len(mask), n))
        self.n = n
        self.mask = mask
        nxt = [n] * (n + 1)
        k = n
        for g in range(n - 1, -1, -1):
            if mask[g]:
                k = g
            nxt[g] = k
        prv = [-1] * (n + 1)
        k = -1
        for g in range(n + 1):
            prv[g] = k
            if g < n and mask[g]:
                k = g
        self.nxt = nxt
        self.prv = prv

    @classmethod
    def of(cls, form, visible: Union[None, "Visibility", Sequence[bool]]) -> Optional["Visibility"]:
        """Normalise a ``visible`` argument: ``None`` if everything is visible (spec §8.5)."""
        if visible is None:
            return None
        if isinstance(visible, Visibility):
            if visible.n != form.n:
                raise ValueError("visibility was computed for %d segments, the form has %d" % (visible.n, form.n))
            return None if all(visible.mask) else visible
        if len(visible) != form.n:
            raise ValueError("visibility mask has %d entries but the form has %d segments" % (len(visible), form.n))
        if all(visible):
            return None
        return cls(form.n, visible)

    def virtual_gap(self, g: int) -> Tuple[int, int]:
        """The real gaps ``(lo, hi)`` making up the virtual gap that contains gap ``g``."""
        return self.prv[g] + 1, self.nxt[g]

    def start_gaps(self) -> List[int]:
        """One real gap per virtual gap, left to right: the gap before each visible segment,
        then ``n`` (:func:`find_all`)."""
        return sorted(set(self.nxt))


# --------------------------------------------------------------------------------------------
# Core simulation
# --------------------------------------------------------------------------------------------


def _prepare(nfa: NFA):
    """Classify the consuming edges of every state once (design §6 caching)."""
    cache = nfa.match_cache
    cons = []
    bref = []
    for es in nfa.edges:
        lst = []
        br = []
        for kind, payload, v in es:
            if kind == SEG:
                if payload is None:
                    lst.append((SEG, None, v, _M_ANY, None))
                elif not payload.is_plain():
                    lst.append((SEG, payload, v, _M_VIEW, None))
                elif payload.has_vars:
                    lst.append((SEG, payload, v, _M_SEG, None))
                else:
                    table = cache.get(payload)
                    if table is None:
                        table = cache[payload] = {}
                    lst.append((SEG, payload, v, _M_CACHED, table))
            elif kind == BACKREF:
                lst.append((BACKREF, payload, v, _M_BREF, None))
                br.append((payload, v))
        cons.append(tuple(lst))
        bref.append(tuple(br))
    nfa._bref = bref
    nfa._cons = cons
    return cons


def _span_segs(form, span, lay: Optional[Visibility]) -> list:
    """The visible segments of a captured span (spec §6.4, §8.5)."""
    i, j = span
    if lay is None:
        return [form.seg(k) for k in range(i, j)]
    mask = lay.mask
    return [form.seg(k) for k in range(i, j) if mask[k]]


def _add(nfa: NFA, state: int, env: Env, g: int, out: list, seen: set, form, lay) -> None:
    """Add the ε-closure of ``state`` at gap ``g`` to the thread list ``out`` (design §6 step 1).

    Runs the closure's ops (assertions, captures, disjunction indices) against gap ``g``;
    deduplicates by ``(state, env)`` keeping the first; follows zero-length
    back-references immediately.
    """
    for tgt, ops in nfa.closure[state]:
        e = env
        if ops:
            for n_op, (kind, payload) in enumerate(ops):
                if kind == ASSERT:
                    if lay is None:
                        ok = payload.holds(form, g, g)
                    else:
                        ok = payload.holds(form, lay.prv[g] + 1, lay.nxt[g])
                    if not ok:
                        e = None
                        break
                elif kind == CAP_OPEN:
                    e = e.bind_cap(payload, (g, None))
                elif kind == CAP_CLOSE:
                    v = e.cap(payload)
                    a = g if v is None else v[0]
                    e = e.bind_cap(payload, (a, g) if a <= g else (g, a))
                elif kind == FLOAT:
                    # A floating autosegment (spec §6.5) may match in several ways: branch.
                    for e2 in _ops_envs(ops, n_op, e, g, form, lay):
                        _push(nfa, tgt, e2, g, out, seen, form, lay)
                    e = None
                    break
                else:  # ALT
                    e = e.bind_alt(payload[0], payload[1])
            if e is None:
                continue
        key = (tgt, e)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        br = nfa._bref[tgt]
        if br:
            for ref, btgt in br:
                span = e.cap(ref)
                if span is not None and span[1] is not None and not _span_segs(form, span, lay):
                    _add(nfa, btgt, e, g, out, seen, form, lay)


def _push(nfa: NFA, tgt: int, e: Env, g: int, out: list, seen: set, form, lay) -> None:
    """Add thread ``(tgt, e)`` after its closure ops ran (the tail of :func:`_add`)."""
    key = (tgt, e)
    if key in seen:
        return
    seen.add(key)
    out.append(key)
    br = nfa._bref[tgt]
    if br:
        for ref, btgt in br:
            span = e.cap(ref)
            if span is not None and span[1] is not None and not _span_segs(form, span, lay):
                _add(nfa, btgt, e, g, out, seen, form, lay)


def _ops_envs(ops: tuple, start: int, env: Env, g: int, form, lay) -> List[Env]:
    """Run closure ops ``ops[start:]`` at gap ``g`` from ``env``; FLOAT ops (spec §6.5)
    branch into one environment per matching floating autosegment (plan P8)."""
    envs = [env]
    if lay is None:
        lo = hi = g
    else:
        lo, hi = lay.prv[g] + 1, lay.nxt[g]
    for kind, payload in ops[start:]:
        nxt: List[Env] = []
        for e in envs:
            if kind == ASSERT:
                if payload.holds(form, lo, hi):
                    nxt.append(e)
            elif kind == CAP_OPEN:
                nxt.append(e.bind_cap(payload, (g, None)))
            elif kind == CAP_CLOSE:
                v = e.cap(payload)
                a = g if v is None else v[0]
                nxt.append(e.bind_cap(payload, (a, g) if a <= g else (g, a)))
            elif kind == FLOAT:
                nxt.extend(payload.match(form, lo, hi, e))
            else:  # ALT
                nxt.append(e.bind_alt(payload[0], payload[1]))
        envs = nxt
        if not envs:
            break
    return envs


def _run(nfa: NFA, form, g0: int, env: Env, d: int, lay: Optional[Visibility]) -> List[Tuple[int, Env]]:
    """Pike-VM simulation of ``nfa`` from gap ``g0`` in direction ``d`` (design §6)."""
    n = form.n
    if not 0 <= g0 <= n:
        raise ValueError("start gap %d outside 0..%d" % (g0, n))
    cons = nfa._cons
    if cons is None:
        cons = _prepare(nfa)
    accept = nfa.accept
    threads: list = []
    _add(nfa, nfa.start, env, g0, threads, set(), form, lay)
    steps = []
    g = g0
    seg_at = form.seg
    while threads:
        acc = [e for s, e in threads if s == accept]
        if acc:
            steps.append((g, acc))
        if lay is None:
            k = g if d > 0 else g - 1
        else:
            k = lay.nxt[g] if d > 0 else lay.prv[g]
        if k < 0 or k >= n:
            break
        g2 = k + 1 if d > 0 else k
        seg = seg_at(k)
        view = None
        new: list = []
        seen: set = set()
        for st, e in threads:
            if type(st) is tuple:
                # A back-reference in progress: (target, capture number, segments matched).
                tgt, ref, idx = st
                segs = _span_segs(form, e.cap(ref), lay)
                want = segs[idx] if d > 0 else segs[len(segs) - 1 - idx]
                if want is seg or want == seg:
                    if idx + 1 == len(segs):
                        _add(nfa, tgt, e, g2, new, seen, form, lay)
                    else:
                        key = ((tgt, ref, idx + 1), e)
                        if key not in seen:
                            seen.add(key)
                            new.append(key)
                continue
            for kind, spec, tgt, mode, table in cons[st]:
                if mode == _M_CACHED:
                    r = table.get(seg)
                    if r is None:
                        r = table[seg] = bool(spec.match(seg, EMPTY))
                    if r:
                        _add(nfa, tgt, e, g2, new, seen, form, lay)
                elif mode == _M_SEG:
                    for e2 in spec.match(seg, e):
                        _add(nfa, tgt, e2, g2, new, seen, form, lay)
                elif mode == _M_ANY:
                    _add(nfa, tgt, e, g2, new, seen, form, lay)
                elif mode == _M_VIEW:
                    if view is None:
                        view = form.view(k)
                    for e2 in spec.match(view, e):
                        _add(nfa, tgt, e2, g2, new, seen, form, lay)
                else:  # back-reference: spec holds the capture number
                    span = e.cap(spec)
                    if span is None or span[1] is None:
                        continue
                    segs = _span_segs(form, span, lay)
                    if not segs:
                        continue  # zero-length: followed in _add
                    want = segs[0] if d > 0 else segs[-1]
                    if want is seg or want == seg:
                        if len(segs) == 1:
                            _add(nfa, tgt, e, g2, new, seen, form, lay)
                        else:
                            key = ((tgt, spec, 1), e)
                            if key not in seen:
                                seen.add(key)
                                new.append(key)
        threads = new
        g = g2
    out: List[Tuple[int, Env]] = []
    for g, acc in reversed(steps):
        for e in acc:
            out.append((g, e))
    return out


def _oriented(nfa: NFA, direction: int) -> NFA:
    if direction == 1:
        return nfa.reversed() if nfa.is_reversed else nfa
    if direction == -1:
        return nfa if nfa.is_reversed else nfa.reversed()
    raise ValueError("direction must be +1 or -1, got %r" % (direction,))


def _spec_accepts(nfa: NFA, spec, form, k: int, env: Env) -> bool:
    """Pre-filter test of one spec on segment ``k`` (design §6), using the NFA's cache."""
    seg = form.seg(k)
    if spec.has_vars or not spec.is_plain():
        return bool(spec.match(seg if spec.is_plain() else form.view(k), env))
    table = nfa.match_cache.get(spec)
    if table is None:
        table = nfa.match_cache[spec] = {}
    r = table.get(seg)
    if r is None:
        r = table[seg] = bool(spec.match(seg, EMPTY))
    return r


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


def match_anchored(nfa: NFA, form, start_gap: int, env: Env = EMPTY, *, direction: int = +1,
                   visible=None) -> List[Tuple[int, Env]]:
    """All matches of ``nfa`` anchored at ``start_gap`` (spec §6.3; design §6).

    Returns ``[(end_gap, env), ...]`` ordered by end gap, longest match first, then by
    thread preference (greedy loops, textual alternatives). With ``direction=+1`` the match
    extends rightward (``end_gap >= start_gap``); with ``direction=-1`` it extends leftward
    (``end_gap <= start_gap``) and the reversed NFA is used: pass either a forward NFA (its
    cached :meth:`NFA.reversed` is used) or an already reversed one. The right end of a
    forward match (left end of a backward one) is open: every end gap is reported.

    ``env`` is the starting environment; bindings only grow. ``visible`` is ``None``, a
    tuple of bools (one per segment) or a :class:`Visibility` (spec §8.5).
    ``form`` implements :class:`yasc.marks.FormLike`.
    """
    return _run(_oriented(nfa, direction), form, start_gap, env, direction, Visibility.of(form, visible))


def find_all(nfa: NFA, form, env: Env = EMPTY, *, direction: int = +1, visible=None,
             first_specs=None) -> Iterator[Tuple[int, int, Env]]:
    """Unanchored search: every match as ``(start_gap, end_gap, env)`` (spec §6.3, §8.2 step 1).

    Start gaps are tried in the direction's order: ``0..n`` for ``+1``, ``n..0`` for ``-1``.
    In both cases the match extends rightward from the start gap (R→L mode changes only the
    enumeration order), so ``start_gap <= end_gap``. For each start gap the matches come in
    :func:`match_anchored` order. Under visibility each virtual gap is tried once, at the real
    gap just before its next visible segment (or ``n``).

    ``first_specs`` (usually ``nfa.first_specs``): if not ``None``, start gaps whose next
    visible segment matches none of these specs under ``env`` are skipped (design §6
    pre-filter); ``None`` disables the pre-filter.
    """
    fwd = _oriented(nfa, 1)
    lay = Visibility.of(form, visible)
    n = form.n
    if lay is None:
        starts = list(range(n + 1))
    else:
        starts = lay.start_gaps()
    if direction == -1:
        starts.reverse()
    elif direction != 1:
        raise ValueError("direction must be +1 or -1, got %r" % (direction,))
    for g in starts:
        if first_specs is not None:
            k = g if lay is None else lay.nxt[g]
            if k >= n:
                continue
            for spec in first_specs:
                if _spec_accepts(fwd, spec, form, k, env):
                    break
            else:
                continue
        for end, e in _run(fwd, form, g, env, 1, lay):
            yield g, end, e


def match_context(left_rev_nfa: Optional[NFA], right_nfa: Optional[NFA], form, i: int, j: int,
                  env: Env = EMPTY, visible=None) -> Iterator[Env]:
    """The environments under which the context ``C ___ D`` holds around the focus ``(i, j)``
    (spec §8.2 step 2).

    ``D`` (``right_nfa``) is matched forward from gap ``j`` with an open right end; for each
    of its environments, in order, ``C`` (``left_rev_nfa``) is matched backward from gap
    ``i`` (a forward NFA is reversed automatically). Bindings thread D then C, so a variable
    bound in D is checked in C, and ``$n`` refers to the LHS captures already in ``env``
    (spec §6.4). Either side may be ``None`` (empty context side). Each distinct
    environment is yielded once, in order (D-major); the generator is lazy, so a negative
    context can stop at the first result.
    """
    lay = Visibility.of(form, visible)
    if right_nfa is None:
        d_envs = [env]
    else:
        d_envs = []
        seen_d = set()
        for _, e in _run(_oriented(right_nfa, 1), form, j, env, 1, lay):
            if e not in seen_d:
                seen_d.add(e)
                d_envs.append(e)
    left = None if left_rev_nfa is None else _oriented(left_rev_nfa, -1)
    seen = set()
    for e1 in d_envs:
        if left is None:
            cands = (e1,)
        else:
            cands = [e for _, e in _run(left, form, i, e1, -1, lay)]
        for e2 in cands:
            if e2 not in seen:
                seen.add(e2)
                yield e2
