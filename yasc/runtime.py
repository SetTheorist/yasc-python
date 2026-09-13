"""Script interpreter: two-stage execution, variables, commands and printing (spec §2, §10;
design §2 ``runtime.py``, plan P6).

**Load** compiles the script once (:func:`yasc.compile.compile_file`). **Run** executes the
top-level statement list (``CompiledScript.statements``) once per input record
(:class:`~yasc.lexicon.Record`):

* the record's form is parsed with the input orthography (``OnUnparsable``) into ``$in``;
* ``$_`` is a list of :class:`Variant` objects, normally one (plan P9 adds more); every
  statement runs on every variant, and each variant has its own variable table;
* ``Rules`` sections and top-level rules run through :func:`yasc.rules.apply_rule`;
  commands and assignments, at top level or nested in groups, reach
  :meth:`Runtime.on_command` through ``ApplyContext.on_command``;
* when no ``!print`` ran, the output is ``input<TAB>output`` per variant (spec §10.5).

Errors while running one record are caught and stored on :attr:`Result.error`, so a caller
(the CLI) can report them and continue (spec §12).
"""

import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import NotImplementedYet, SourceLoc, YascError, YascRuntimeError
from .form import Form
from .ir import IRAssign, IRBasicRule, IRCommand, IRGroup, IRInvoke
from .lexicon import Record, parse_features
from .paradigm import applicable_cells, build_cell
from .rules import DEFAULT_SETTINGS, ApplyContext, BasicRule, Builder, RecordData, TraceStep, apply_rule
from .syllable import Syllabifier
from .variants import choose, constraint_violations, explore, join_label

__all__ = ["Variant", "Result", "Runtime", "format_trace", "merge_variants", "declared_dialects",
           "READONLY_VARIABLES", "INPUT_SETTINGS"]

#: Built-in variables that ``$name := ...`` may not assign (spec §10.1).
READONLY_VARIABLES = ("in", "raw", "NF", "NR", "field")

#: Settings that govern how records are read; top-level ``!set`` lines for them take
#: effect before the first record is read (design §13 entry 104).
INPUT_SETTINGS = ("InputFormat", "OnUnparsable")

_INT_SETTINGS = ("MaxIterations", "MaxVariants", "Seed")

#: Commands that do not read ``$_``: when they lead the top-level statement list they run
#: before the record's form is parsed (design §13 entry 103).
CONFIG_COMMANDS = ("set", "use", "orthography", "only", "skip")

_RE_DIRECTIVE = re.compile(r"%O\[\$([A-Za-z][A-Za-z0-9_]*)\]\{(\d+)\}|%([OSIFL])\{(\d+)\}|%%")


class Variant:
    """One member of ``$_`` (spec §8.11, §11.2): a :class:`~yasc.form.Form`, its derivation
    ``label`` (the optional-rule decisions and paradigm cell, ``None`` when there are none),
    its ``dialect`` (spec §10.4) and its variable table. ``rng_state`` is the variant's own
    random state once there are several variants (P9 decision 7). ``str(variant)`` renders
    the form in ``orthography`` (the output orthography once the record has run)."""

    __slots__ = ("form", "label", "dialect", "vars", "orthography", "rng_state")

    def __init__(self, form: Form, label: Optional[str] = None, dialect: Optional[str] = None,
                 variables: Optional[Dict[str, Any]] = None, orthography: Any = None) -> None:
        self.form = form
        self.label = label
        self.dialect = dialect
        self.vars: Dict[str, Any] = dict(variables or {})
        self.orthography = orthography
        self.rng_state: Any = None

    @property
    def text(self) -> str:
        """The form rendered in :attr:`orthography` (spec §5.6)."""
        return self.orthography.render(self.form) if self.orthography is not None else repr(self.form)

    @property
    def approximate(self) -> List[Dict[str, Any]]:
        """Approximate renderings (spec §5.6 step 4): ``[{"index", "segment", "residual"}]``
        where ``residual`` lists ``{"feature", "value", "rendered"}``."""
        if self.orthography is None:
            return []
        _text, approx = self.orthography.render_ex(self.form)
        out = []
        for i, residual in approx:
            out.append({"index": i, "segment": self.orthography.render_segment(self.form.segs[i])[0],
                        "residual": [{"feature": f, "value": v, "rendered": r} for f, v, r in residual]})
        return out

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return "<Variant %r%s>" % (self.text, " " + self.label if self.label else "")


class Result:
    """The outcome of running the script on one record (spec §10, §11.2).

    ``outputs`` is the final ``$_`` (a list of :class:`Variant`), ``trace`` the
    :class:`~yasc.rules.TraceStep` list (empty unless tracing), ``printed`` the text written
    by ``!print`` (``None`` if no ``!print`` ran), ``text`` what the CLI writes for the
    record (``printed``, or the default ``input<TAB>output`` lines), ``warnings`` message
    strings, and ``error`` the :class:`~yasc.errors.YascError` that stopped the record, if
    any."""

    def __init__(self, record: Record) -> None:
        self.record = record
        self.input = record.form
        self.outputs: List[Variant] = []
        self.trace: List[TraceStep] = []
        self.printed: Optional[str] = None
        self.text = ""
        self.warnings: List[str] = []
        self.error: Optional[YascError] = None
        self.orthography: Any = None

    @property
    def ok(self) -> bool:
        """True when the record ran without error."""
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        """The ``--json`` object for this record (spec §11.1). The schema is stable::

            {"input":    str,              # the record's form text
             "nr":       int,              # record number ($NR)
             "source":   str | null,       # "file:line" of the record
             "outputs":  [{"form": str,    # rendered in the output orthography
                           "label": str | null, "dialect": str | null,
                           "approximate": [{"index": int, "segment": str,
                                            "residual": [{"feature": str, "value": str | null,
                                                          "rendered": str | null}]}]}],
             "printed":  str | null,       # text written by !print (null: none ran)
             "trace":    [{"rule_id": int | null, "name": str | null, "line": int | null,
                           "before": str, "after": str, "foci": [[int, int]],
                           "note": str | null}],   # empty unless tracing
             "warnings": [str],
             "error":    str}              # present only when the record failed

        Keys are always present except ``error``; new keys may be added, none removed."""
        rec = self.record
        d: Dict[str, Any] = {
            "input": self.input,
            "nr": rec.nr,
            "source": "%s:%d" % (rec.source, rec.line) if rec.line else None,
            "outputs": [{"form": v.text, "label": v.label, "dialect": v.dialect, "approximate": v.approximate}
                        for v in self.outputs],
            "printed": self.printed,
            "trace": [trace_dict(s, self.orthography) for s in self.trace],
            "warnings": list(self.warnings),
        }
        if self.error is not None:
            d["error"] = self.error.format()
        return d

    def wide(self, columns: Sequence[str]) -> str:
        """The ``--wide`` row (spec §10.4): the input, then for each dialect in ``columns``
        the distinct forms of that dialect's variants joined with ``", "`` (empty when the
        record has none in that dialect)."""
        cells = []
        for d in columns:
            seen: List[str] = []
            for v in self.outputs:
                if v.dialect == d and v.text not in seen:
                    seen.append(v.text)
            cells.append(", ".join(seen))
        return "\t".join([self.input] + cells) + "\n"


def _render(orth, form: Form) -> str:
    return orth.render(form) if orth is not None else repr(form)


def trace_dict(step: TraceStep, orth) -> Dict[str, Any]:
    """A trace step as a JSON-ready dict (spec §11.1 ``--json``)."""
    return {"rule_id": step.rule_id, "name": step.name, "line": step.loc.line if step.loc else None,
            "before": _render(orth, step.before), "after": _render(orth, step.after),
            "foci": [list(f) for f in step.foci], "note": step.note}


def format_trace(step: TraceStep, orth) -> str:
    """One ``--trace`` line (spec §11.1):
    ``rule-id  name  line  before → after  (focus i..j)``; a skipped pending construct shows
    its note instead of the change."""
    head = "%s  %s  %s" % (step.rule_id if step.rule_id is not None else "-", step.name or "-",
                           step.loc.line if step.loc else "-")
    if step.note:
        return "%s  %s  (%s)" % (head, _render(orth, step.before), step.note)
    foci = ", ".join("%d..%d" % f for f in step.foci)
    return "%s  %s → %s  (focus %s)" % (head, _render(orth, step.before), _render(orth, step.after), foci)


def _walk(items, seen=None):
    """Every IR record reachable from ``items`` through groups and invocations (plan P9)."""
    seen = set() if seen is None else seen
    for it in items:
        if id(it) in seen:
            continue
        seen.add(id(it))
        yield it
        if isinstance(it, IRGroup):
            yield from _walk(it.members, seen)
        elif isinstance(it, IRInvoke):
            yield from _walk((it.target,), seen)


def declared_dialects(compiled) -> List[str]:
    """The dialect names of every ``!dialects`` command of a script, in order (spec §10.4)."""
    out: List[str] = []
    for it in _walk(compiled.statements):
        if isinstance(it, IRCommand) and it.name == "dialects":
            for name in it.args[0].value:
                if name not in out:
                    out.append(name)
    return out


def merge_variants(variants: Sequence[Variant]) -> List[Variant]:
    """Merge identical variants (same form, dialect and variables), joining their labels
    with ``|`` (spec §8.11); the first occurrence keeps its place (P9 decision 6)."""
    index: Dict[Any, Variant] = {}
    out: List[Variant] = []
    for v in variants:
        try:
            key: Any = (v.form, v.dialect, tuple(sorted(v.vars.items())))
            hash(key)
        except TypeError:
            key = None
        hit = index.get(key) if key is not None else None
        if hit is None:
            if key is not None:
                index[key] = v
            out.append(v)
        elif v.label != hit.label:
            hit.label = "%s|%s" % (hit.label or "", v.label or "")
    return out


class _Drop(Exception):
    """Internal: the current variant has no dialect left and is dropped (spec §10.4)."""


class _State:
    """Per-record interpreter state (spec §10): the record, the variants of ``$_``, the
    current input/output orthographies, the ``!print`` buffer and the warnings."""

    def __init__(self, record: Record, result: Result, in_orth, out_orth) -> None:
        self.record = record
        self.result = result
        self.input_orth = in_orth
        self.output_orth = out_orth
        self.in_form: Optional[Form] = None
        self.variants: List[Variant] = []
        self.current: Optional[Variant] = None
        self.printed: List[str] = []
        self.did_print = False
        self.ctx: Optional[ApplyContext] = None


class Runtime:
    """Runs a :class:`~yasc.ir.CompiledScript` on records (spec §2 run time, §10).

    Options: ``date_window`` (``--from``/``--to``, spec §8.10), ``only``/``skip`` (rule
    names), ``allow_pending`` (skip constructs of plan phases P7–P9 with a warning instead of
    raising :class:`~yasc.errors.NotImplementedYet`), ``trace`` (collect
    :class:`~yasc.rules.TraceStep` objects) and ``settings`` (overrides of the ``!set``
    defaults). Plan P9: ``dialect`` (``--dialect D``: produce only dialect ``D``, spec §10.4)
    and ``paradigm`` (``--paradigm NAME``: the paradigm of records without a ``paradigm``
    column, spec §9; P9 decision 8). One :class:`~yasc.rules.Builder` is shared by all
    records (design §1)."""

    def __init__(self, compiled, *, date_window: Optional[Tuple[Optional[int], Optional[int]]] = None,
                 only: Sequence[str] = (), skip: Sequence[str] = (), allow_pending: bool = False,
                 trace: bool = False, settings: Optional[Dict[str, Any]] = None, dialect: Optional[str] = None,
                 paradigm: Optional[str] = None) -> None:
        self.compiled = compiled
        self.date_window = date_window if date_window and date_window != (None, None) else None
        self.only = tuple(only)
        self.skip = tuple(skip)
        self.allow_pending = allow_pending
        self.trace = trace
        self.builder = Builder()
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        for st in compiled.statements:
            if isinstance(st, IRCommand) and st.name == "set" and st.args[0].value in INPUT_SETTINGS:
                self.settings[st.args[0].value] = str(st.args[1].value).strip()
        if settings:
            self.settings.update(settings)
        self.undated = [r for r in compiled.rules if getattr(r, "date", None) is None]
        # Run time starts from the most recently *defined* orthography; !use and
        # !orthography then re-select in statement order (design §13 entry 103).
        orths = [obj for kind, _name, obj in compiled.definitions if kind == "orthography"]
        self.initial_orthography = orths[-1] if orths else compiled.orthography
        # Plan P7: likewise the most recently defined Syllabification is active at the start
        # of a record; !use re-selects (design §13 entry 121).
        syls = [obj for kind, _name, obj in compiled.definitions if kind == "syllabification"]
        self._syllabifiers: Dict[int, Syllabifier] = {}
        self.initial_syllabification = syls[-1] if syls else compiled.syllabification
        self.profile: Optional[Dict[int, List[float]]] = None
        self._warned: set = set()
        self._sources: Dict[str, List[str]] = {}
        # Plan P9: dialects (spec §10.4), paradigms (spec §9) and derivation variants (§8.11).
        self.dialect = dialect
        self.paradigm = paradigm
        self.dialect_names: List[str] = declared_dialects(compiled)
        self.has_paradigm_command = any(isinstance(x, IRCommand) and x.name == "paradigm"
                                        for x in _walk(compiled.statements))
        self._fork_memo: Dict[int, bool] = {}

    # -- configuration ---------------------------------------------------------------------

    @property
    def input_format(self) -> str:
        """``InputFormat`` for reading records (spec §10.2)."""
        return str(self.settings.get("InputFormat", "tsv"))

    def enable_profile(self) -> Dict[int, List[float]]:
        """Time every basic rule (CLI ``--profile``, design §10): returns the table
        ``{rule id: [calls, applications, seconds]}`` that later runs fill in."""
        from .rules import build_rules
        self.profile = {}
        for rid, rule in build_rules(self.compiled, self.builder).items():
            if isinstance(rule, BasicRule):
                self.profile[rid] = [0, 0, 0.0]
                rule.apply = self._timed(rule, self.profile[rid])  # type: ignore[method-assign]
        return self.profile

    @staticmethod
    def _timed(rule: BasicRule, cell: List[float]):
        inner = rule.apply

        def apply(form, ctx):
            t0 = time.perf_counter()
            out = inner(form, ctx)
            cell[2] += time.perf_counter() - t0
            cell[0] += 1
            if out.applied:
                cell[1] += 1
            return out
        return apply

    def source_line(self, loc: Optional[SourceLoc]) -> Optional[str]:
        """The text of the script line ``loc`` points into, for caret excerpts (spec §12)."""
        if loc is None:
            return None
        lines = self._sources.get(loc.file)
        if lines is None:
            try:
                with open(loc.file, encoding="utf-8") as fh:
                    lines = fh.read().split("\n")
            except OSError:
                lines = []
            self._sources[loc.file] = lines
        return lines[loc.line - 1] if 0 < loc.line <= len(lines) else None

    def warn_once(self, key: Any, message: str, result: Result) -> None:
        """Record a warning on ``result`` the first time ``key`` is seen in this run."""
        if key not in self._warned:
            self._warned.add(key)
            result.warnings.append(message)

    # -- running records -------------------------------------------------------------------

    def run(self, record: Record) -> Result:
        """Run the statement list on one record (spec §2, §10). Errors are caught into
        :attr:`Result.error` (with the record named and the script line filled in)."""
        res = Result(record)
        st = _State(record, res, self.initial_orthography, self.initial_orthography)
        try:
            if record.error is not None:
                raise record.error
            self._execute(st)
        except YascError as e:
            if isinstance(e, YascRuntimeError) and e.record is None:
                e.record = record.label
            if e.source_line is None and e.loc is not None:
                e.source_line = self.source_line(e.loc)
            res.error = e
        if st.ctx is not None:
            if st.ctx.trace is not None:
                res.trace = list(st.ctx.trace)
            res.warnings.extend(st.ctx.warnings)  # e.g. no-op syllable writes (spec §5.4, §12)
            for rid, name, phase in st.ctx.skipped:
                self.warn_once(("pending", rid, name, phase), "skipped %s (needs plan phase %s)"
                               % ("rule %s%s" % (rid, " %r" % name if name else "") if rid is not None or name
                                  else "a command", phase), res)
        res.orthography = st.output_orth
        for v in st.variants:
            v.orthography = st.output_orth
        if res.error is None:
            res.outputs = list(st.variants)
            res.printed = "".join(st.printed) if st.did_print else None
            res.text = res.printed if st.did_print else self._default_output(st)
            for v in res.outputs:
                for a in v.approximate:
                    res.warnings.append("approximate rendering of segment %d (%s) in %r: %s" % (
                        a["index"], a["segment"], v.text,
                        ", ".join("%s is %s, rendered %s" % (r["feature"], r["value"], r["rendered"])
                                  for r in a["residual"])))
        return res

    def _default_output(self, st: _State) -> str:
        """``input<TAB>output`` per variant (spec §10.5), then a dialect column when the
        variant has a dialect and a label column when it has a label (spec §8.11, §10.4;
        P9 decision 10)."""
        lines = []
        for v in st.variants:
            cols = [st.record.form, v.text]
            if v.dialect is not None:
                cols.append(v.dialect)
            if v.label is not None:
                cols.append(v.label)
            lines.append("\t".join(cols) + "\n")
        return "".join(lines)

    def _context(self, st: _State) -> ApplyContext:
        rec = st.record
        ctx = ApplyContext(self.settings, trace=[] if self.trace else None,
                           record=RecordData(rec.lexical, rec.date, rec.dialect, rec.fields),
                           date_window=self.date_window, only=self.only, skip=self.skip,
                           skip_pending=self.allow_pending, on_command=self.on_command, builder=self.builder)
        ctx.syllabifier = self.syllabifier(self.initial_syllabification)
        self._set_constraints(ctx)
        return ctx

    def _set_constraints(self, ctx: ApplyContext) -> None:
        """``EnforceConstraints = on``: every ``Constraint *`` acts as an output filter of
        every basic rule (spec §4.6; P9 decision 9)."""
        on = str(ctx.settings.get("EnforceConstraints", "off")) == "on"
        ctx.constraints = tuple(self.compiled.constraints) if on else ()
        ctx.p9 = bool(ctx.constraints) or ctx.domain is not None

    # -- variants, dialects and paradigms (plan P9; spec §8.11, §9, §10.4) -----------------

    def _initial_variants(self, st: _State, v: Variant) -> List[Variant]:
        """The variants a record starts with (P9 decisions 8 and 11). Without ``!dialects``
        in the script, one per name in the record's ``dialect`` column (``--dialect D``
        keeps only ``D``). A paradigm-tagged record (or any record with ``--paradigm``) is
        expanded into its cells when the script has no ``!paradigm`` command."""
        rec = st.record
        out = [v]
        if not self.dialect_names:
            names = rec.dialects
            if self.dialect is not None:
                if names and self.dialect not in names:
                    return []
                names = (self.dialect,)
            if names:
                out = [Variant(v.form, None, d) for d in names]
        tag = rec.paradigm or self.paradigm
        if tag and not self.has_paradigm_command:
            pd = self._paradigm_def(st, tag)
            out = [Variant(self._cell_form(cell, var.form), join_label(var.label, cell.label), var.dialect)
                   for var in out for cell in applicable_cells(pd, rec.lexical, var.dialect)]
        limit = int(st.ctx.settings.get("MaxVariants", 64))
        if len(out) > limit:
            raise self._error("the record starts with %d variants, more than MaxVariants = %d" % (len(out), limit),
                              None, st, hint="raise the cap with !set MaxVariants = n (spec §8.11)")
        return out

    def _paradigm_def(self, st: _State, name: str):
        pd = self.compiled.paradigms.get(name)
        if pd is None:
            raise self._error("the record names paradigm $%s, but the script defines no such Paradigm" % name,
                              st.record.loc(), st, hint="defined paradigms: %s (spec §9)"
                              % (", ".join("$" + p for p in self.compiled.paradigms) or "none"))
        return pd

    @staticmethod
    def _cell_form(cell, form: Form) -> Form:
        """A paradigm cell built on ``form``, re-syllabified if ``form`` was syllabified."""
        new = build_cell(cell, form)
        tier = form.syllables
        if tier is not None and getattr(tier, "syllabifier", None) is not None:
            new = tier.syllabifier.syllabify(new)
        return new

    def _forks(self, stmt) -> bool:
        """True if ``stmt`` can fork a variant: an optional rule or group, ``!paradigm`` or
        ``!dialects`` anywhere inside it (spec §8.11). Memoised per statement."""
        hit = self._fork_memo.get(id(stmt))
        if hit is None:
            hit = self._fork_memo[id(stmt)] = any(
                getattr(x, "optional", None) is not None
                or (isinstance(x, IRCommand) and x.name in ("paradigm", "dialects")) for x in _walk((stmt,)))
        return hit

    def _run_statement(self, st: _State, stmt) -> List[Variant]:
        """Run one top-level statement on every variant of ``$_`` (spec §8.11). A statement
        that can fork is explored path by path; afterwards identical variants merge. More
        than ``MaxVariants`` variants is an error (P9 decisions 5–7 and 12)."""
        ctx = st.ctx
        fork = self._forks(stmt)
        multi = len(st.variants) > 1
        limit = int(ctx.settings.get("MaxVariants", 64))
        out: List[Variant] = []
        for v in st.variants:
            st.current = v
            if ctx.record.dialect != v.dialect:
                ctx.record = ctx.record._replace(dialect=v.dialect)
            if multi and v.rng_state is not None:
                ctx.rng.setstate(v.rng_state)
            if fork:
                out.extend(self._explore(st, stmt, v, max(limit - len(out), 1)))
                continue
            try:
                v.form = apply_rule(stmt, v.form, ctx).form
            except _Drop:
                continue
            if multi:
                v.rng_state = ctx.rng.getstate()
            out.append(v)
        if fork or multi:
            out = merge_variants(out)
        limit = int(ctx.settings.get("MaxVariants", 64))  # the statement may have set it
        if len(out) > limit:
            raise self._error("more than MaxVariants = %d variants" % limit, getattr(stmt, "loc", None), st,
                              hint="raise the cap with !set MaxVariants = n (spec §8.11)")
        return out

    def _explore(self, st: _State, stmt, v: Variant, limit: int) -> List[Variant]:
        """Every derivation path of ``stmt`` from variant ``v`` (spec §8.11; P9 decision 5).
        Each path restarts from the same state (form, variables, settings, RNG, orthographies,
        dialect) and replays the earlier choices; what was printed or traced before a path
        leaves its parent is not repeated."""
        ctx = st.ctx
        snap = (dict(ctx.settings), ctx.rng.getstate(), ctx.only, ctx.skip, ctx.syllabifier, st.input_orth,
                st.output_orth, ctx.record, ctx.constraints, ctx.p9)

        def marker():
            return len(st.printed), (len(ctx.trace) if ctx.trace is not None else 0)

        def path(ex):
            settings, rng, ctx.only, ctx.skip, ctx.syllabifier, st.input_orth, st.output_orth, ctx.record, \
                ctx.constraints, ctx.p9 = snap
            ctx.settings = dict(settings)
            ctx.rng.setstate(rng)
            var = Variant(v.form, v.label, v.dialect, v.vars)
            st.current = var

            def on_token(tok):
                var.label = join_label(var.label, tok)
            ex.marker, ex.on_token = marker, on_token
            start = marker()
            ctx.explorer = ex
            try:
                var.form = apply_rule(stmt, var.form, ctx).form
                result: Optional[Variant] = var
            except _Drop:
                result = None
            finally:
                ctx.explorer = None
            if ex.mark is not None:
                del st.printed[start[0]:ex.mark[0]]
                if ctx.trace is not None:
                    del ctx.trace[start[1]:ex.mark[1]]
            if result is not None:
                result.rng_state = ctx.rng.getstate()
            return result
        return [var for _ex, var in explore(path, limit) if var is not None]

    def _execute(self, st: _State) -> None:
        """Run the leading configuration commands (``!set``, ``!use``, ``!orthography``,
        ``!only``, ``!skip``), parse the input into ``$in``, then run every remaining
        top-level statement on every variant (spec §2, §10; design §13 entry 103)."""
        rec = st.record
        ctx = st.ctx = self._context(st)
        ctx.state = st  # type: ignore[attr-defined]
        if rec.date is not None and self.undated:
            self.warn_once("undated", "records are dated, so the %d undated rule%s never apply to them "
                           "(spec §8.10)" % (len(self.undated), "" if len(self.undated) == 1 else "s"), st.result)
        stmts = self.compiled.statements
        k = 0
        while k < len(stmts) and isinstance(stmts[k], IRCommand) and stmts[k].name in CONFIG_COMMANDS \
                and not stmts[k].pending:
            self.on_command(stmts[k], None, ctx)  # type: ignore[arg-type]
            k += 1
        st.in_form = self.parse_input(st, rec.form, rec.loc(rec.form_col), rec.raw or None)
        st.variants = self._initial_variants(st, Variant(st.in_form, None, None))
        for stmt in stmts[k:]:
            st.variants = self._run_statement(st, stmt)

    def parse_input(self, st: _State, text: str, loc: Optional[SourceLoc] = None,
                    source_line: Optional[str] = None) -> Form:
        """Parse ``text`` with the current input orthography and ``OnUnparsable``
        (spec §5.6, §10.2)."""
        orth = st.input_orth
        if orth is None:
            raise YascRuntimeError("the script defines no orthography, so input cannot be parsed",
                                   hint="add an Orthography section (spec §5.6)")
        mode = st.ctx.settings.get("OnUnparsable", "error") if st.ctx is not None else "error"
        return orth.parse(text, on_unparsable=mode, loc=loc, source_line=source_line)

    # -- commands and assignments (spec §10.1, §10.5) --------------------------------------

    def on_command(self, ir, form: Form, ctx: ApplyContext) -> Optional[Form]:
        """``ApplyContext.on_command`` hook: execute an ``IRCommand`` or ``IRAssign`` on the
        current variant, whose form is ``form``; return the new ``$_`` or ``None``
        (spec §10.1, §10.5; design §13 entry 94)."""
        st: _State = ctx.state  # type: ignore[attr-defined]
        if isinstance(ir, IRAssign):
            return self._assign(st, ir, form)
        handler = getattr(self, "_cmd_" + ir.name, None)
        if handler is None:  # pragma: no cover - the compiler emits no other commands
            raise NotImplementedYet("!%s is not supported by the runtime" % ir.name, ir.loc)
        return handler(st, ir, form)

    def _error(self, message: str, loc: Optional[SourceLoc], st: _State, hint: Optional[str] = None):
        return YascRuntimeError(message, loc, hint=hint, source_line=self.source_line(loc), record=st.record.label)

    def value(self, st: _State, name: str, index: Optional[int], form: Form, loc: Optional[SourceLoc]) -> Any:
        """The value of ``$name`` (or ``$field[index]``): a :class:`Form`, a text (``$raw``,
        fields) or an int (``$NF``, ``$NR``) (spec §10.1)."""
        rec = st.record
        if name == "_":
            return form
        if name == "in":
            return st.in_form
        if name == "raw":
            return rec.form
        if name == "NF":
            return len(rec.fields)
        if name == "NR":
            return rec.nr
        if name == "field":
            if index is None or index < 1:
                raise self._error("fields are numbered from 1: $field[%s] does not exist" % index, loc, st)
            return rec.field(index)
        vars_ = st.current.vars if st.current is not None else {}
        if name not in vars_:
            raise self._error("variable $%s is not set" % name, loc, st,
                              hint="assign it first with $%s := ... (spec §10.1)" % name)
        return vars_[name]

    def as_form(self, st: _State, value: Any, loc: Optional[SourceLoc]) -> Form:
        """A value as a form: texts are parsed with the input orthography (spec §10.1)."""
        if isinstance(value, Form):
            return value
        return self.parse_input(st, str(value))

    def _assign(self, st: _State, ir: IRAssign, form: Form) -> Optional[Form]:
        """``$name := expr`` (spec §10.1); assigning ``$_`` replaces the current form."""
        if ir.kind == "ortho":
            value: Any = ir.form
        elif ir.kind == "field":
            value = self.value(st, "field", ir.value, form, ir.loc)
        else:
            value = self.value(st, ir.value, None, form, ir.loc)
        if ir.name == "_":
            return self.as_form(st, value, ir.loc)
        if ir.name in READONLY_VARIABLES:
            raise self._error("$%s is read-only" % ir.name, ir.loc, st)
        st.current.vars[ir.name] = value
        return None

    def render(self, st: _State, orth, value: Any) -> str:
        """A value in orthography ``orth``: forms are rendered, texts and numbers are
        written as they are (spec §10.5 ``%O``)."""
        if isinstance(value, Form):
            return orth.render(value) if orth is not None else repr(value)
        return str(value)

    def _cmd_print(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!print "fmt" x0 x1 ...`` (spec §10.5): ``%O{i}``, ``%O[$Orth]{i}``, ``%S{i}``,
        ``%I{i}``, ``%F{i}``, ``%L{i}`` and ``%%``; ``\\n`` and ``\\t`` are decoded by the
        lexer."""
        fmt = ir.args[0].value
        args = ir.args[1:]
        vals = [self.value(st, a.value[0], a.value[1], form, a.loc or ir.loc) for a in args]
        orths = ir.refs or {}

        def sub(m) -> str:
            if m.group(0) == "%%":
                return "%"
            if m.group(1):
                return self.render(st, orths[m.group(1)], vals[int(m.group(2))])
            kind, v = m.group(3), vals[int(m.group(4))]
            if kind == "O":
                return self.render(st, st.output_orth, v)
            if kind == "S":
                return " ".join(s.canonical() for s in self.as_form(st, v, ir.loc).segs)
            if kind == "I":
                if v is st.in_form:
                    return st.record.form
                return self.render(st, st.input_orth, v)
            if kind == "F":
                return self.render(st, st.output_orth, v)
            return (st.current.label or "") if st.current is not None else ""  # %L

        st.printed.append(_RE_DIRECTIVE.sub(sub, fmt))
        st.did_print = True
        return None

    def _cmd_set(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!set Name = value`` (spec §10.5) for the rest of the record. ``Seed`` reseeds the
        RNG; ``EnforceConstraints`` is stored for plan P9; ``InputFormat`` and
        ``OnUnparsable`` at top level also govern reading (design §13 entry 104)."""
        key, value = ir.args[0].value, str(ir.args[1].value).strip()
        ctx = st.ctx
        ctx.settings[key] = int(value) if key in _INT_SETTINGS else value
        if key == "Seed":
            ctx.rng.seed(int(value))
        if key == "Trace" and value == "on" and ctx.trace is None:
            ctx.trace = []
        if key == "EnforceConstraints":
            self._set_constraints(ctx)  # plan P9 (spec §4.6)
        return None

    def _cmd_dialects(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!dialects (A B C)`` (spec §10.4): fork the current variant into one variant per
        dialect, restricted by the record's ``dialect`` column and by ``--dialect``. A
        variant with no dialect left is dropped (P9 decision 11)."""
        names = list(ir.args[0].value)
        rec = st.record.dialects
        if rec:
            unknown = [d for d in rec if d not in names]
            if unknown:
                self.warn_once(("dialect", tuple(unknown), tuple(names)), "record dialect%s %s not declared by "
                               "!dialects (%s) (spec §10.4)" % ("s" if len(unknown) > 1 else "",
                                                               " ".join(unknown), " ".join(names)), st.result)
            names = [d for d in names if d in rec]
        if self.dialect is not None:
            names = [d for d in names if d == self.dialect]
        if not names:
            raise _Drop()
        d = names[choose(st.ctx, [None] * len(names), ir)]
        st.current.dialect = d
        st.ctx.record = st.ctx.record._replace(dialect=d)
        return None

    def _cmd_paradigm(self, st: _State, ir: IRCommand, form: Form) -> Form:
        """``!paradigm $X`` (spec §9): fork the current variant into one variant per cell of
        ``$X`` that applies, labelled with the cell name. A record tagged with another
        paradigm (its ``paradigm`` column, or ``--paradigm``) is left alone (P9 decision 8)."""
        pd = ir.refs
        tag = st.record.paradigm or self.paradigm
        if tag is not None and tag != pd.name:
            return form
        cells = applicable_cells(pd, st.record.lexical, st.current.dialect)
        if not cells:
            raise self._error("no cell of paradigm $%s applies to this record" % pd.name, ir.loc, st,
                              hint="check the cells' /:L± and /:D± restrictions (spec §9)")
        return self._cell_form(cells[choose(st.ctx, [c.label for c in cells], ir)], form)

    def _cmd_use(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!use $X`` (spec §10.3): an orthography becomes both input and output; a
        Syllabification becomes the active one for ``!syllabify`` and ``/:$`` (plan P7)."""
        kind, obj = ir.refs
        if kind == "orthography":
            st.input_orth = st.output_orth = obj
        elif kind == "syllabification":
            st.ctx.syllabifier = self.syllabifier(obj)
        return None

    def syllabifier(self, definition) -> Optional[Syllabifier]:
        """The compiled :class:`~yasc.syllable.Syllabifier` of a ``SyllabificationDef``,
        built once per run (plan P7)."""
        if definition is None:
            return None
        syl = self._syllabifiers.get(id(definition))
        if syl is None:
            syl = self._syllabifiers[id(definition)] = Syllabifier(definition)
        return syl

    def _cmd_associate(self, st: _State, ir: IRCommand, form: Form) -> Form:
        """``!associate Tier [dir=>|<] [mode=one-to-one] [spread=last|none]`` (spec §5.5,
        §10.5; plan P8)."""
        from .tiers import command
        return command(ir, form)

    def _cmd_ocp(self, st: _State, ir: IRCommand, form: Form) -> Form:
        """``!ocp Tier [merge|delete]`` (spec §5.5, §10.5; plan P8)."""
        from .tiers import command
        return command(ir, form)

    def _cmd_syllabify(self, st: _State, ir: IRCommand, form: Form) -> Form:
        """``!syllabify [$S]`` (spec §10.3): syllabify the current form now with ``$S`` or
        the active syllabification (spec §5.7)."""
        syl = self.syllabifier(ir.refs) if ir.refs is not None else st.ctx.syllabifier
        if syl is None:
            raise self._error("!syllabify needs a Syllabification, but none is defined", ir.loc, st,
                              hint="add a Syllabification [[ ... ]] section (spec §5.7)")
        return syl.syllabify(form)

    def _cmd_orthography(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!orthography input $A output $B`` (spec §10.3)."""
        inp, outp = ir.refs
        if inp is not None:
            st.input_orth = inp
        if outp is not None:
            st.output_orth = outp
        return None

    def _cmd_only(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!only Name...`` (spec §10.5): from here on run only these rules or groups."""
        st.ctx.only = st.ctx.only | frozenset(a.value for a in ir.args)
        return None

    def _cmd_skip(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!skip Name...`` (spec §10.5): from here on skip these rules or groups."""
        st.ctx.skip = st.ctx.skip | frozenset(a.value for a in ir.args)
        return None

    def _cmd_assert(self, st: _State, ir: IRCommand, form: Form) -> None:
        """``!assert "expected"`` (spec §10.5): the current form in the output orthography
        must equal ``expected``; a mismatch is a run-time error for the record."""
        expected = ir.args[0].value
        got = self.render(st, st.output_orth, form)
        if got != expected:
            raise self._error("assertion failed: expected %r, got %r" % (expected, got), ir.loc, st,
                              hint="the form is rendered in the output orthography")
        return None


# ------------------------------------------------------------------------------------------
# Public API (spec §11.2)
# ------------------------------------------------------------------------------------------


class Script:
    """A loaded script (spec §11.2): ``apply`` runs it on one form. ``rules`` lists the
    compiled basic rules (and placeholders) in file order, each with ``id``, ``name``,
    ``date``, ``loc`` and a canonical ``source``; ``phonology`` is the active
    :class:`~yasc.features.FeatureSystem`; ``orthography`` the output orthography."""

    def __init__(self, compiled, *, allow_pending: bool = False,
                 date_window: Optional[Tuple[Optional[int], Optional[int]]] = None,
                 only: Sequence[str] = (), skip: Sequence[str] = (), dialect: Optional[str] = None,
                 paradigm: Optional[str] = None) -> None:
        self.compiled = compiled
        self.runtime = Runtime(compiled, date_window=date_window, only=only, skip=skip, allow_pending=allow_pending,
                               dialect=dialect, paradigm=paradigm)

    @property
    def constraints(self) -> List[Any]:
        """The ``Constraint *`` declarations (spec §4.6), each with ``pattern``, ``nfa``,
        ``phonology`` and ``source``. Rules obey them only under
        ``!set EnforceConstraints = on``; word generators can use them directly (plan P9)."""
        return list(self.compiled.constraints)

    def violations(self, form_text: str) -> List[Tuple[str, int, int]]:
        """Every match of a constraint in ``form_text`` (parsed with the input orthography)
        as ``(constraint source, start gap, end gap)`` (spec §4.6; plan P9)."""
        orth = self.runtime.initial_orthography
        if orth is None:
            raise YascRuntimeError("the script defines no orthography, so input cannot be parsed")
        return [(c.source, s, e) for c, s, e in constraint_violations(self.compiled.constraints, orth.parse(form_text))]

    def well_formed(self, form_text: str) -> bool:
        """True if no constraint matches ``form_text`` (spec §4.6; plan P9)."""
        return not self.violations(form_text)

    @property
    def rules(self) -> List[Any]:
        """Every compiled rule in file order (spec §11.1 ``--list-rules``, §11.2)."""
        return list(self.compiled.rules)

    @property
    def phonology(self):
        """The active feature system (spec §11.2)."""
        return self.compiled.phonology

    @property
    def orthography(self):
        """The output orthography at the end of loading (spec §10.3, §11.2)."""
        return self.compiled.output_orthography

    @property
    def input_orthography(self):
        """The input orthography at the end of loading (spec §10.3)."""
        return self.compiled.input_orthography

    @property
    def warnings(self) -> List[YascError]:
        """Load-time warnings (spec §12)."""
        return list(self.compiled.warnings)

    def apply(self, form_text: str, *, features: Any = None, date: Optional[int] = None,
              dialect: Optional[str] = None, trace: bool = False) -> Result:
        """Run the script on one form (spec §11.2). ``features`` is a lexical feature string
        (``"+Romance !N"``) or a mapping; ``date`` the record date (spec §8.10);
        ``dialect`` the record's dialect column, e.g. ``"A"`` or ``"A B"`` (spec §10.2,
        §10.4). Raises the record's error, if any."""
        if isinstance(features, str):
            lexical = parse_features(features)
        else:
            lexical = {k: str(v) for k, v in (features or {}).items()}
        rec = Record(form_text.strip(), (form_text,), lexical=lexical, date=date, dialect=dialect, raw=form_text,
                     source="<api>", nr=1)
        rt = self.runtime
        old, rt.trace = rt.trace, trace
        try:
            res = rt.run(rec)
        finally:
            rt.trace = old
        if res.error is not None:
            raise res.error
        return res


def loads(text: str, filename: str = "<string>", *, allow_pending: bool = False, **options: Any) -> Script:
    """Compile script text into a :class:`Script` (spec §2 load time, §11.2).
    ``allow_pending`` compiles and runs constructs of plan phases P7–P9 as skipped
    placeholders; other ``options`` go to :class:`Script`."""
    from .compile import compile_source
    return Script(compile_source(text, filename, allow_unimplemented=allow_pending), allow_pending=allow_pending,
                  **options)


def load(path: str, *, allow_pending: bool = False, **options: Any) -> Script:
    """Compile the script file ``path`` into a :class:`Script` (spec §11.2)."""
    from .compile import compile_file
    return Script(compile_file(path, allow_unimplemented=allow_pending), allow_pending=allow_pending, **options)
