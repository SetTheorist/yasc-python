"""Compiler: :mod:`yasc.syntax` AST -> runtime objects and rule IR (spec §4–§10; design §7.3).

:func:`compile_source` parses and compiles a script and returns a
:class:`~yasc.ir.CompiledScript`:

* ``Phonology`` sections become sealed :class:`~yasc.features.FeatureSystem` objects
  (feature names, aliases and value types checked, every error reported);
* ``Orthography`` sections become sealed :class:`~yasc.orthography.Orthography` objects;
* macros are expanded syntactically (spec §7: scoping, shadowing warnings, cycles,
  refinement); ``[...]`` strings are parsed with the *working* orthography where they are
  used (spec §2, §10.3);
* rules become :class:`~yasc.ir.IRBasicRule` / :class:`~yasc.ir.IRGroup` records with
  NFAs, numbered LHS items, disjunction ids, validated and inherited modifiers, dates and
  static checks (unbound RHS variables, variables shared by incompatible features, ``$n``).

Constructs whose semantics belong to later plan phases raise
:class:`~yasc.errors.NotImplementedYet` (collected like any other error) unless
``allow_unimplemented=True``, in which case they compile to placeholders or records marked
``pending`` and a :class:`~yasc.errors.YascWarning` is recorded.
"""

import difflib
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import (
    NotImplementedYet, SourceLoc, YascDefinitionError, YascError, YascLoadError, YascSyntaxError, YascWarning,
)
from .features import FeatureSystem, FeatureType, TierDecl
from .ir import (
    CompiledScript, ConstraintDef, IRAssign, IRBasicRule, IRCommand, IRContext, IRFilter, IRGroup, IRInvoke,
    IRLexical, IRNames, IROptional, IRPlaceholder, IRRestrictions, IRRhsItem, IRStochastic, IRVisibility,
    MacroInfo, ParadigmCell, ParadigmDef, SyllabificationDef,
)
from .lexer import ConstraintSyntax
from .marks import mark_from_symbol
from .nfa import compile_pattern
from .orthography import Orthography, UnparsableError
from .parser import KEYWORDS, Parser
from .pattern import (
    Alt, AutoFloat, BackRef, Capture, Linked, Locus, Macro, Nothing, Opt,
    Ortho, Pattern, Plus, Seq, Spec, Star, number_lhs, split_at_locus, walk,
)
from .segment import Absent, Cmp, Eq, In, SegmentSpec, Var, WeakVar
from .syllable import declare_role_features
from . import tiers as _tiers  # plan P8: autosegmental notation (spec §6.5)
from .syntax import (
    Arg, Assign, BasicRule, Combine, Command, ConstraintDecl, FeatureDecl, Group, IgnoreLine, ImplicationDecl,
    Invoke, LinkOp, MacroDef, Merge, Modifier, OrthographySection, ParadigmSection, PhonesLine, PhonologySection,
    RawOrtho, RawSpec, RulesSection, Script, SettingLine, SyllabificationSection, SyllableMarkLine,
    DiacriticLine,
)

__all__ = ["compile_source", "compile_file", "compile_script", "SETTINGS", "MOD_CATEGORY"]

#: ``!include "lib:NAME"`` reads ``NAME`` from the standard library directory ``yasc/lib/``
#: (spec §10.5 [Δ]; design §13 entry 163).
LIB_PREFIX = "lib:"
LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")

#: ``!set`` settings (spec §10.5) and a validator for their values (``None``: any).
SETTINGS = {
    "DefaultMode": ("simultaneous", "once"),
    "MaxIterations": int,
    "MaxVariants": int,
    "Seed": int,
    "OnUnparsable": ("error", "skip", "keep"),
    "EnforceConstraints": ("on", "off"),
    "InputFormat": None,
    "Trace": ("on", "off"),
}

#: Modifier key -> category. Contexts, filters and restrictions accumulate; every other
#: category holds one modifier, and a member's own modifier overrides an inherited one of the
#: same category (spec §8.8).
MOD_CATEGORY = {
    "/": "ctx", "/!": "ctx", "/:i+": "filter", "/:i-": "filter", "/:o+": "filter", "/:o-": "filter",
    "/:F+": "vis", "/:F-": "vis", "/:L+": "lex", "/:L-": "lex", "/:D+": "dialect", "/:D-": "dialect",
    "/:C+": "cat", "/:C-": "cat", "/:C*": "cyclic", "/:1": "mode", "/*": "mode", "/:*": "repeat",
    "/:>": "dir", "/:<": "dir", "/:~": "weak", "/:Raw": "raw", "/:$": "resyll", "/:T": "tier",
    "/:@": "date", "/%": "stoch", "/???": "opt", '/"': "name", "/::": "persist",
}
_ADDITIVE = ("ctx", "filter", "lex", "dialect", "cat")

#: Plan phase implementing each modifier whose semantics are not P5/P6.
_MOD_PHASE: Dict[str, str] = {}  # plan P8: /:T compiles for real
_CMD_PHASE: Dict[str, str] = {}  # plan P8: !associate and !ocp run for real

#: Modifiers allowed after a group's ``]]`` (spec §8.8).
_TRAIL_OK = ("/:1", "/:*", "/:C+", "/:C-", "/:C*", "/:L+", "/:L-", "/:D+", "/:D-", "/:@", "/%", "/???", '/"',
             "/:~", "/:i+", "/:i-", "/:o+", "/:o-", "/::")

_RE_DIRECTIVE = re.compile(r"%O\[\$([A-Za-z][A-Za-z0-9_]*)\]\{(\d+)\}|%([OSIFL])\{(\d+)\}|%%|%")


def _flatten(err: YascError) -> List[YascError]:
    return list(err.errors) if isinstance(err, YascLoadError) else [err]


class _Scope:
    """A macro scope: global, or one Rules block/group (spec §7)."""

    def __init__(self, parent: Optional["_Scope"] = None, kind: str = "global") -> None:
        self.parent = parent
        self.kind = kind
        self.macros: Dict[str, Tuple[MacroInfo, "_Scope"]] = {}

    def lookup(self, name: str):
        s: Optional[_Scope] = self
        while s is not None:
            if name in s.macros:
                return s.macros[name]
            s = s.parent
        return None

    def visible(self) -> List[str]:
        out: List[str] = []
        s: Optional[_Scope] = self
        while s is not None:
            out.extend(s.macros)
            s = s.parent
        return out


class _Rule:
    """Per-rule compilation state: alternative ids and the rule's feature system."""

    def __init__(self) -> None:
        self.next_alt = 0

    def alt_id(self) -> int:
        self.next_alt += 1
        return self.next_alt


class Compiler:
    """Compiles one parsed script (design §7.3). Use :func:`compile_source`."""

    def __init__(self, filename: str = "<string>", allow_unimplemented: bool = False) -> None:
        self.filename = filename
        self.allow = allow_unimplemented
        self.errors: List[YascError] = []
        self.warnings: List[YascError] = []
        self.sources: Dict[str, List[str]] = {}
        self.out = CompiledScript(filename)
        self.fs: Optional[FeatureSystem] = None
        self.working: Optional[Orthography] = None
        self.defs: Dict[str, Tuple[str, Any]] = {}
        self.globals = _Scope()
        self.last_date: Optional[Tuple[int, SourceLoc]] = None
        self.current_date: Optional[int] = None
        self.rule_id = 0
        self.file_stack: List[str] = [filename]
        self.included: set = set()

    # -- diagnostics -----------------------------------------------------------------------

    def error(self, err: YascError) -> None:
        """Record an error (flattening load errors)."""
        self.errors.extend(_flatten(err))

    def err(self, cls, message: str, loc: Optional[SourceLoc], hint: Optional[str] = None) -> None:
        """Record a new error of class ``cls``."""
        self.errors.append(cls(message, loc, hint))

    def warn(self, message: str, loc: Optional[SourceLoc], hint: Optional[str] = None,
             phase: Optional[str] = None) -> None:
        """Record a warning (spec §12)."""
        self.warnings.append(YascWarning(message, loc, hint, phase=phase))

    def unimpl(self, phase: Optional[str], what: str, loc: Optional[SourceLoc]) -> bool:
        """A construct of a later plan phase: with ``allow_unimplemented`` record a warning
        and return True (compile a placeholder); otherwise record ``NotImplementedYet``
        and return False."""
        if self.allow:
            self.warn("%s is compiled as a placeholder" % what, loc, phase=phase)
            return True
        self.errors.append(NotImplementedYet("%s is not implemented yet" % what, loc, phase=phase))
        return False

    def guard(self, fn, *args, **kw):
        """Call ``fn``; record any YASC error and return ``None``."""
        try:
            return fn(*args, **kw)
        except NotImplementedYet as e:
            if self.unimpl(e.phase, e.message, e.loc):
                return None
            return None
        except YascError as e:
            self.error(e)
            return None

    def _finish(self, items: List[YascError]) -> List[YascError]:
        out = []
        seen = set()
        for e in items:
            key = (type(e), e.loc, e.message)
            if key in seen:
                continue
            seen.add(key)
            if e.loc is not None and e.source_line is None:
                lines = self.sources.get(e.loc.file)
                if lines and 0 < e.loc.line <= len(lines):
                    e.source_line = lines[e.loc.line - 1]
            out.append(e)
        return out

    # -- driver ----------------------------------------------------------------------------

    def compile(self, script: Script, text: Optional[str] = None) -> CompiledScript:
        """Compile a parsed script; raise :class:`YascLoadError` with every error (spec §12)."""
        if text is not None:
            self.sources[self.filename] = text.split("\n")
        if os.path.exists(self.filename):
            self.included.add(os.path.realpath(self.filename))
        self.out.syntax = script
        for st in script.stmts:
            if isinstance(st, MacroDef):
                self._define_macro(st, self.globals)
        for st in script.stmts:
            self._top(st)
        out = self.out
        out.macros = {k: v[0] for k, v in self.globals.macros.items()}
        out.phonology = self.fs
        out.warnings = self._finish(self.warnings)
        errors = self._finish(self.errors)
        if errors:
            raise YascLoadError(errors)
        return out

    def _top(self, st) -> None:
        if isinstance(st, PhonologySection):
            self.guard(self._phonology, st)
        elif isinstance(st, OrthographySection):
            self.guard(self._orthography, st)
        elif isinstance(st, SyllabificationSection):
            self.guard(self._syllabification, st)
        elif isinstance(st, ParadigmSection):
            self.guard(self._paradigm, st)
        elif isinstance(st, MacroDef):
            pass  # global macros are registered before the main pass (spec §7 "global")
        elif isinstance(st, RulesSection):
            g = self.guard(self._section, st)
            if g is not None:
                self.out.statements.append(g)
        elif isinstance(st, Command) and st.name == "include":
            self._include(st, None, None)
        else:
            for ir in self._item(st, self.globals, ()):
                self.out.statements.append(ir)

    def _define(self, kind: str, name: Optional[str], obj: Any, loc: Optional[SourceLoc]) -> None:
        self.out.definitions.append((kind, name, obj))
        if name is None:
            return
        if name in self.defs and self.defs[name][0] != kind:
            self.warn("$%s is redefined as a %s (it was a %s)" % (name, kind, self.defs[name][0]), loc)
        self.defs[name] = (kind, obj)
        table = {"phonology": self.out.phonologies, "orthography": self.out.orthographies,
                 "syllabification": self.out.syllabifications, "paradigm": self.out.paradigms,
                 "rules": self.out.rule_sections}[kind]
        table[name] = obj

    # -- include ---------------------------------------------------------------------------

    def _include(self, cmd: Command, scope: Optional[_Scope], inherited) -> List[Any]:
        rel = cmd.args[0].value
        base = self.file_stack[-1]
        base_dir = os.path.dirname(base) if os.path.exists(base) or os.path.dirname(base) else os.getcwd()
        if rel.startswith(LIB_PREFIX):          # plan P10: "lib:NAME" is yasc/lib/NAME (entry 163)
            path = os.path.join(LIB_DIR, rel[len(LIB_PREFIX):])
            where = "a lib: path names a file in the standard library (%s)" % LIB_DIR
        else:
            path = rel if os.path.isabs(rel) else os.path.join(base_dir, rel)
            where = "the path is relative to the including file (%s)" % (base_dir or ".")
        real = os.path.realpath(path)
        if real in self.included:
            return []
        if not os.path.isfile(path):
            self.err(YascDefinitionError, "cannot include %r: file not found" % rel, cmd.loc, hint=where)
            return []
        self.included.add(real)
        self.out.included.append(path)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            self.err(YascDefinitionError, "cannot include %r: %s" % (rel, e), cmd.loc)
            return []
        self.sources[path] = text.split("\n")
        try:
            script = Parser(text, path).parse()
        except YascLoadError as e:
            self.error(e)
            return []
        self.file_stack.append(path)
        out: List[Any] = []
        try:
            if scope is None:
                for st in script.stmts:
                    if isinstance(st, MacroDef):
                        self._define_macro(st, self.globals)
                for st in script.stmts:
                    if not isinstance(st, MacroDef):
                        self._top(st)
            else:
                for st in script.stmts:
                    if isinstance(st, (PhonologySection, OrthographySection, SyllabificationSection,
                                       ParadigmSection, RulesSection)):
                        self.err(YascSyntaxError, "a file included inside Rules may contain only rule items",
                                 getattr(st, "loc", None), hint="include it at top level instead")
                        continue
                    out.extend(self._item(st, scope, inherited))
        finally:
            self.file_stack.pop()
        return out

    # -- phonology -------------------------------------------------------------------------

    def _ftype(self, d: FeatureDecl) -> FeatureType:
        t = d.type
        if t.kind == "binary":
            return FeatureType.binary()
        if t.kind == "unary":
            return FeatureType.unary()
        if t.kind == "node":
            return FeatureType.node()
        if t.kind == "scalar":
            try:
                return FeatureType.scalar(*(t.bounds or ()))
            except YascDefinitionError as e:
                e.loc = e.loc or t.loc
                raise
        return FeatureType(ENUM_KIND, t.values, None, t.loc)

    def _phonology(self, sec: PhonologySection) -> None:
        fs = FeatureSystem(sec.name)
        decls: List[Tuple[FeatureDecl, Any]] = []
        for item in sec.items:
            if not isinstance(item, FeatureDecl):
                continue
            try:
                ftype = self._ftype(item)
            except YascDefinitionError as e:
                e.loc = e.loc or item.loc
                self.error(e)
                continue
            for op in item.ops:
                try:
                    ftype = ftype.with_op(op.op, op.results, op.loc)
                except YascDefinitionError as e:
                    e.loc = e.loc or op.loc
                    e.message = "%s: %s" % (item.name, e.message)
                    self.error(e)
            scope = "syllable" if item.scope == "Syllable" else "segment"
            # A refused (not yet implemented) feature is still declared, so that later
            # references to it do not cascade into "unknown feature" errors.
            tier = None
            if item.tier is not None:
                tier = TierDecl(None, item.tier.stray or "float", item.tier.ocp or "off")  # plan P8
            try:
                feat = fs.add_feature(item.name, ftype, children=item.type.children, scope=scope, tier=tier,
                                      loc=item.loc)
            except YascDefinitionError as e:
                e.loc = e.loc or item.loc
                self.error(e)
                continue
            for al in item.aliases:
                self.guard(fs.add_aliases, feat, *al.names, loc=al.loc)
            decls.append((item, feat))
        for item in sec.items:
            if isinstance(item, ImplicationDecl):
                self.guard(self._implication, fs, item)
        for item, feat in decls:
            if item.tier is not None and item.tier.tbu is not None:
                spec = self.guard(self._spec, item.tier.tbu, fs)
                if spec is not None:
                    feat.tier = TierDecl(spec, feat.tier.stray, feat.tier.ocp)
        # Role pseudo-features (spec §5.4): read-only Unary features in every phonology
        # (notes.md §4 C2; design §13 entry 114).
        self.guard(declare_role_features, fs)
        try:
            fs.seal()
        except YascError as e:
            self.error(e)
            return
        self.fs = fs
        self.working_fs_check()
        self._define("phonology", sec.name, fs, sec.loc)
        for item in sec.items:
            if isinstance(item, ConstraintDecl):
                pat = self.guard(self._pattern, item.pattern, self.globals, _Rule())
                if pat is None:
                    continue
                nfa = self.guard(compile_pattern, pat)
                if nfa is not None:
                    self.out.constraints.append(ConstraintDef(pat, nfa, fs, item.canonical(), item.loc))

    def working_fs_check(self) -> None:
        """A new phonology makes an orthography of another phonology unusable for ``[...]``."""
        if self.working is not None and self.working.fs is not self.fs:
            self.working = None

    def _implication(self, fs: FeatureSystem, item: ImplicationDecl) -> None:
        left = self._spec(item.left, fs)
        right = self._spec(item.right, fs)
        # Plan P7 (design §13 entry 16 answered in entry 118): implications act on bare
        # segments, so they may not read or write syllable-scope or role features.
        for spec in (left, right):
            for c in spec.constraints:
                if c.feature.scope in ("syllable", "role"):
                    raise YascDefinitionError(
                        "an implication may not mention %s, which is %s" % (
                            c.feature.name, "a syllable feature (Scope(Syllable))" if c.feature.scope == "syllable"
                            else "a role pseudo-feature"), c.loc or item.loc,
                        hint="implications are properties of segments (spec §4.5); write a rule instead")
        if item.backward is None:
            fs.add_implication(left, right, weak=item.forward == "~~", loc=item.loc, origin=item.canonical())
        else:
            fs.add_bidirectional(left, right, op=item.op, forward_weak=item.forward == "~~",
                                 backward_weak=item.backward == "~~", loc=item.loc)

    # -- specs -----------------------------------------------------------------------------

    def _need_fs(self, loc: Optional[SourceLoc]) -> FeatureSystem:
        if self.fs is None:
            raise YascDefinitionError("no Phonology is defined before this point", loc,
                                      hint="define a Phonology [[ ... ]] section first (spec §2)")
        return self.fs

    def _constraint(self, c: ConstraintSyntax, fs: FeatureSystem):
        feat = fs.feature(c.feature, c.loc)
        k = c.kind
        if k == "value":
            return Eq(feat, c.value, c.loc)
        if k == "bare":
            if feat.type.kind == "unary":
                return Eq(feat, "!", c.loc)
            if feat.is_node:
                raise YascDefinitionError("a bare Node name %r is not a constraint" % feat.name, c.loc,
                                          hint="write !%s (present) or _%s (absent)" % (feat.name, feat.name))
            raise YascDefinitionError(
                "feature %r needs a value (only Unary features may be written bare)" % feat.name, c.loc,
                hint="for example %s%s" % (feat.type.canonical().split()[0] if feat.type.kind == "enum" else
                                           (feat.type.values[0] if feat.type.values else "!"), feat.name))
        if k == "absent":
            return Absent(feat, c.loc)
        if k == "in":
            return In(feat, c.values, c.loc)
        if k == "cmp":
            return Cmp(feat, c.cmp, c.n, c.loc)
        if k == "var":
            return Var(feat, c.var, c.ops, c.loc)
        if k == "weakvar":
            return WeakVar(feat, c.var, c.loc)
        raise YascDefinitionError("'...' is not allowed here", c.loc)

    def _spec(self, raw: RawSpec, fs: Optional[FeatureSystem] = None) -> SegmentSpec:
        """A :class:`SegmentSpec` for ``{...}`` (spec §6.2): names resolved, values checked."""
        fs = fs or self._need_fs(raw.loc)
        cons = []
        errs: List[YascError] = []
        for c in raw.constraints:
            if c.kind == "ellipsis":
                continue
            try:
                cons.append(self._constraint(c, fs))
            except YascError as e:
                errs.extend(_flatten(e))
        if errs:
            if len(errs) == 1:
                raise errs[0]
            raise YascLoadError(errs)
        return SegmentSpec(fs, cons, raw.strict, raw.loc)

    # -- orthography -----------------------------------------------------------------------

    def _orthography(self, sec: OrthographySection) -> None:
        fs = self._need_fs(sec.loc)
        orth = Orthography(fs, sec.name)
        for item in sec.items:
            if isinstance(item, PhonesLine):
                spec = self.guard(self._spec, item.spec, fs)
                if spec is not None:
                    self.guard(orth.add_phones, list(item.graphemes), spec, item.loc)
            elif isinstance(item, DiacriticLine):
                spec = self.guard(self._spec, item.spec, fs)
                if spec is not None:
                    self.guard(orth.add_diacritic, spec, item.template, item.loc)
            elif isinstance(item, IgnoreLine):
                names = []
                for c in item.spec.constraints:
                    if c.feature is None or fs.get(c.feature) is None:
                        self.guard(fs.feature, c.feature or "...", c.loc)
                    else:
                        names.append(c.feature)
                self.guard(orth.ignore, names, item.loc)
            elif isinstance(item, SettingLine):
                self.guard(orth.set_setting, item.name, item.text or None, item.loc)
            elif isinstance(item, SyllableMarkLine):
                spec = self.guard(self._spec, item.spec, fs)
                if spec is not None:
                    bad = [c.feature.name for c in spec.constraints if c.feature.scope != "syllable"]
                    if bad:
                        self.err(YascDefinitionError, "SyllableMark may set only Scope(Syllable) features, not %s"
                                 % ", ".join(bad), item.loc,
                                 hint="declare the feature with Scope(Syllable) (spec §5.4, §5.6)")
                        continue
                    self.guard(orth.add_syllable_mark, spec, item.text, item.loc)
        if self.guard(orth.seal) is None:
            return
        self.working = orth
        self.out.orthography = orth
        self.out.input_orthography = orth
        self.out.output_orthography = orth
        self._define("orthography", sec.name, orth, sec.loc)

    def _ortho_form(self, text: str, loc: Optional[SourceLoc], close: bool = True):
        """Parse ``[text]`` with the working orthography (spec §2, §10.3)."""
        orth = self.working
        if orth is None:
            raise YascDefinitionError("[%s] needs an orthography, but none is active here" % text, loc,
                                      hint="define an Orthography [[ ... ]] section before this line")
        if orth.fs is not self.fs:
            raise YascDefinitionError("the working orthography belongs to another phonology", loc,
                                      hint="select a matching orthography with !use")
        inner = None
        if loc is not None:
            inner = SourceLoc(loc.file, loc.line, loc.col + 1)
        try:
            return orth.parse(text, loc=inner, close=close)
        except UnparsableError as e:
            raise YascSyntaxError(e.message, e.loc, e.hint, e.source_line) from None

    def _ortho_segments(self, text: str, loc: Optional[SourceLoc], close: bool = True):
        form = self._ortho_form(text, loc, close)
        if not form.segs:
            raise YascDefinitionError("[%s] contains no segment" % text, loc)
        return _tiers.segments_with_tiers(form)  # plan P8: [á] in a pattern keeps its tone

    # -- macros ----------------------------------------------------------------------------

    def _define_macro(self, st: MacroDef, scope: _Scope) -> None:
        if st.name in KEYWORDS:
            self.err(YascDefinitionError, "%r is a keyword and cannot name a macro" % st.name, st.loc)
            return
        if st.name in scope.macros:
            prev = scope.macros[st.name][0]
            if prev.body is st.body and prev.loc == st.loc:
                return
            self.err(YascDefinitionError, "macro %s is defined twice in the same scope" % st.name, st.loc,
                     hint="the first definition is at %s" % prev.loc if prev.loc else None)
            return
        outer = scope.parent.lookup(st.name) if scope.parent is not None else None
        if outer is not None:
            self.warn("macro %s shadows an outer definition" % st.name, st.loc,
                      hint="the outer definition is at %s" % outer[0].loc if outer[0].loc else None)
        scope.macros[st.name] = (MacroInfo(st.name, st.body, scope.kind, st.loc), scope)

    def _expand(self, p: Pattern, scope: _Scope, stack: Tuple[str, ...] = ()) -> Pattern:
        """Expand macros syntactically (spec §7); cycles are errors."""
        if isinstance(p, Macro):
            hit = scope.lookup(p.name)
            if hit is None:
                close = difflib.get_close_matches(p.name, scope.visible(), n=1, cutoff=0.6)
                raise YascDefinitionError("unknown macro %r" % p.name, p.loc,
                                          hint=("did you mean %r?" % close[0]) if close else
                                          "a bare name in a pattern is a macro; define it with %s === ..." % p.name)
            info, where = hit
            if p.name in stack:
                chain = " -> ".join(stack[stack.index(p.name):] + (p.name,))
                raise YascDefinitionError("macro cycle: %s" % chain, info.loc or p.loc,
                                          hint="a macro may not use itself, directly or indirectly (spec §7)")
            body = self._expand(info.body, where, stack + (p.name,))
            if p.refine is not None:
                return Combine(body, self._expand(p.refine, scope, stack), p.loc)
            return body
        if isinstance(p, Seq):
            return Seq(tuple(self._expand(x, scope, stack) for x in p.items), p.loc)
        if isinstance(p, Alt):
            return Alt(p.id, tuple(self._expand(x, scope, stack) for x in p.items), p.loc)
        if isinstance(p, (Opt, Star, Plus)):
            return type(p)(self._expand(p.pattern, scope, stack), p.loc)
        if isinstance(p, Combine):
            return Combine(self._expand(p.left, scope, stack), self._expand(p.right, scope, stack), p.loc)
        if isinstance(p, Linked):
            return Linked(self._expand(p.spec, scope, stack), p.tier, p.x, p.exact, p.name, p.loc)
        if isinstance(p, LinkOp):
            return LinkOp(self._expand(p.spec, scope, stack), p.tier, p.op, p.x, p.name, p.loc)
        if isinstance(p, Merge):
            return Merge(self._expand(p.item, scope, stack), p.loc)
        return p

    # -- patterns --------------------------------------------------------------------------

    def _alternatives(self, p: Pattern, rule: _Rule) -> List[SegmentSpec]:
        """The single-segment specs of a combine operand (spec §6.1 ``S1:S2``)."""
        if isinstance(p, RawSpec):
            return [self._spec(p)]
        if isinstance(p, Spec):
            return [p.spec]
        if isinstance(p, RawOrtho):
            words = p.text.split()
            out = []
            for w in words or [p.text]:
                segs = self._ortho_segments(w, p.loc)
                if len(segs) != 1:
                    raise YascDefinitionError(
                        "[%s] in S1:S2 must be one segment (or a list [p t k] read as a disjunction)" % w, p.loc)
                out.append(SegmentSpec.from_segment(segs[0], p.strict, p.loc))
            return out
        if isinstance(p, Combine):
            left = self._alternatives(p.left, rule)
            right = self._alternatives(p.right, rule)
            if len(left) > 1 and len(right) > 1:
                raise YascDefinitionError("both operands of ':' are disjunctions", p.loc,
                                          hint="only one side of S1:S2 may be a list of alternatives")
            return [a.combine(b, p.loc) for a in left for b in right]
        if isinstance(p, Alt):
            out = []
            for x in p.items:
                sub = self._alternatives(x, rule)
                out.extend(sub)
            return out
        raise YascDefinitionError(
            "%s must be a single segment spec or a disjunction of them to be combined or refined" % p.canonical(),
            getattr(p, "loc", None), hint="spec §6.1 (S1:S2) and §7 (Name:{spec})")

    def _leaves(self, p: Pattern, rule: _Rule) -> Pattern:
        """Resolve specs and orthographic strings; number disjunctions (design §7.3)."""
        if isinstance(p, RawSpec):
            return Spec(self._spec(p), p.loc)
        if isinstance(p, RawOrtho):
            segs = self._ortho_segments(p.text, p.loc)
            return Ortho(tuple(SegmentSpec.from_segment(s, p.strict, p.loc) for s in segs), p.text, p.loc)
        if isinstance(p, Combine):
            specs = self._alternatives(p, rule)
            if len(specs) == 1:
                return Spec(specs[0], p.loc)
            return Alt(rule.alt_id(), tuple(Spec(s, p.loc) for s in specs), p.loc)
        if isinstance(p, Seq):
            return Seq(tuple(self._leaves(x, rule) for x in p.items), p.loc)
        if isinstance(p, Alt):
            return Alt(rule.alt_id(), tuple(self._leaves(x, rule) for x in p.items), p.loc)
        if isinstance(p, (Opt, Star, Plus)):
            return type(p)(self._leaves(p.pattern, rule), p.loc)
        if isinstance(p, Linked):  # plan P8: S^X (spec §6.5)
            _tiers.check_element(self._need_fs(p.loc), p)
            return Linked(self._leaves(p.spec, rule), p.tier, p.x, p.exact, p.name, p.loc)
        if isinstance(p, AutoFloat):  # plan P8: ^X (spec §6.5)
            _tiers.check_element(self._need_fs(p.loc), p)
            return p
        if isinstance(p, LinkOp):
            raise YascDefinitionError("%s is only allowed on the right-hand side" % p.canonical(), p.loc,
                                      hint="spec §6.5")
        return p

    def _pattern(self, p: Pattern, scope: _Scope, rule: _Rule) -> Pattern:
        return self._leaves(self._expand(p, scope), rule)

    # -- syllabification and paradigms -----------------------------------------------------

    def _canon_letters(self, sec: SyllabificationSection, canons: Sequence[str]) -> Dict[str, Tuple[Any, ...]]:
        """Plan P7: resolve each canon letter as a global macro that matches one segment
        (spec §5.7 "the letters C and V are macros")."""
        loc = next((st.loc for st in sec.settings if st.key == "Canons"), sec.loc)
        letters: Dict[str, Tuple[Any, ...]] = {}
        for c in canons:
            if "V" not in c:
                self.err(YascDefinitionError, "canon %s has no V, so it has no nucleus" % c, loc,
                         hint="every canon needs at least one V (spec §5.7)")
            for ch in c:
                if ch in letters:
                    continue
                specs = self.guard(lambda x: self._alternatives(self._expand(Macro(x, None, loc), self.globals),
                                                                _Rule()), ch)
                if specs is not None:
                    letters[ch] = tuple(specs)
        return letters

    def _syllabification(self, sec: SyllabificationSection) -> None:
        fs = self._need_fs(sec.loc)
        settings: Dict[str, Any] = {}
        templates: Dict[str, Tuple[Any, Any]] = {}
        for st in sec.settings:
            if st.key in ("Onset", "Nucleus", "Coda"):
                pat = self.guard(self._pattern, st.value, self.globals, _Rule())
                if pat is None:
                    continue
                bad = [x for x in walk(pat) if not isinstance(x, (Spec, Ortho, Seq, Opt, Alt, Nothing))]
                if bad:
                    self.err(YascDefinitionError, "%s is not allowed in a syllable template" % bad[0].canonical(),
                             getattr(bad[0], "loc", None) or st.loc,
                             hint="templates use sequences, optionals, disjunctions and segment specs (spec §5.7)")
                    continue
                nfa = None if isinstance(pat, Nothing) else self.guard(compile_pattern, pat)
                templates[st.key] = (pat, nfa)
            else:
                settings[st.key] = st.value
        letters: Dict[str, Tuple[Any, ...]] = {}
        if settings.get("Algorithm") == "Canon":
            if not settings.get("Canons"):
                self.err(YascDefinitionError, "Algorithm Canon needs a Canons line", sec.loc,
                         hint="for example: Canons CV > CVC > V > VC (spec §5.7)")
            else:
                letters = self._canon_letters(sec, settings["Canons"])
        else:
            if "Canons" in settings:
                self.warn("Canons is used only with Algorithm Canon", sec.loc)
            if templates.get("Nucleus", (None, None))[1] is None:
                self.warn("the Syllabification has no Nucleus template, so MaxOnset finds no syllables", sec.loc)
        sd = SyllabificationDef(sec.name, settings, templates, fs, sec, sec.loc, letters)
        self.out.syllabification = sd
        self._define("syllabification", sec.name, sd, sec.loc)

    def _restrictions(self, mods: Sequence[Modifier]) -> IRRestrictions:
        lex, dia, cat = [], [], []
        for m in mods:
            if m.key in ("/:L+", "/:L-"):
                lex.append(IRLexical(m.key.endswith("+"), m.arg.constraints, m.canonical(), m.loc))
            elif m.key in ("/:D+", "/:D-"):
                dia.append(IRNames(m.key.endswith("+"), m.arg, m.loc))
            elif m.key in ("/:C+", "/:C-"):
                cat.append(IRNames(m.key.endswith("+"), m.arg, m.loc))
        return IRRestrictions(tuple(lex), tuple(dia), tuple(cat))

    def _paradigm(self, sec: ParadigmSection) -> None:
        cells = []
        labels = set()
        for cell in sec.cells:
            if cell.label in labels:
                self.err(YascDefinitionError, "paradigm cell %s is defined twice" % cell.label, cell.loc)
            labels.add(cell.label)
            items = []
            for it in cell.items:
                if it.kind == "ortho":
                    form = self.guard(self._ortho_form, it.value, it.loc)
                    items.append(("ortho", form))
                elif it.kind == "boundary":
                    items.append(("boundary", mark_from_symbol(it.value)))
                else:
                    items.append((it.kind, it.value))
            if not any(k == "stem" for k, _ in items):
                self.warn("paradigm cell %s does not use the stem $_" % cell.label, cell.loc)
            cells.append(ParadigmCell(cell.label, tuple(items), self._restrictions(cell.mods), cell.loc))
        pd = ParadigmDef(sec.name, tuple(cells), sec, sec.loc)
        self._define("paradigm", sec.name, pd, sec.loc)

    # -- rule items ------------------------------------------------------------------------

    def _item(self, st, scope: _Scope, inherited: Tuple[Modifier, ...]) -> List[Any]:
        """Compile one rule item (spec §8.8 members); returns zero or more IR records."""
        if isinstance(st, BasicRule):
            r = self.guard(self._basic, st, scope, inherited)
            return [] if r is None else [r]
        if isinstance(st, Group):
            g = self.guard(self._group, st, scope, inherited)
            return [] if g is None else [g]
        if isinstance(st, Invoke):
            target = self.out.named.get(st.name)
            if target is None:
                kind = self.defs.get(st.name, (None,))[0]
                hint = ("$%s is a %s, not a rule block" % (st.name, kind)) if kind else \
                    "invoke a previously named Rules section or a group named with /\" (spec §8.8)"
                self.err(YascDefinitionError, "unknown rule block $%s" % st.name, st.loc, hint=hint)
                return []
            return [IRInvoke(st.name, target, st.loc)]
        if isinstance(st, MacroDef):
            if scope is not self.globals:
                self._define_macro(st, scope)
            return []
        if isinstance(st, Assign):
            a = self.guard(self._assign, st)
            return [] if a is None else [a]
        if isinstance(st, Command):
            if st.name == "include":
                if scope is self.globals:
                    return self._include(st, None, None)
                return self._include(st, scope, inherited)
            c = self.guard(self._command, st)
            return [] if c is None else [c]
        if isinstance(st, RulesSection):
            g = self.guard(self._section, st)
            return [] if g is None else [g]
        self.err(YascSyntaxError, "unexpected statement", getattr(st, "loc", None))
        return []

    def _assign(self, st: Assign) -> IRAssign:
        e = st.expr
        form = None
        if e.kind == "ortho":
            form = self._ortho_form(e.value, e.loc)
        return IRAssign(st.name, e.kind, e.value, form, st.canonical(), st.loc)

    def _command(self, st: Command) -> Optional[IRCommand]:
        name = st.name
        args = st.args
        refs: Any = None
        pending = _CMD_PHASE.get(name)
        if pending is not None and not self.unimpl(pending, "!%s" % name, st.loc):
            return None
        if name == "date":
            self._set_date(args[0].value, args[0].loc)
            self.current_date = args[0].value
            return None
        if name == "use":
            kind, obj = self._lookup_def(args[0])
            refs = (kind, obj)
            if kind == "phonology":
                self.fs = obj
                self.working_fs_check()
            elif kind == "orthography":
                self.working = obj
                self.out.orthography = self.out.input_orthography = self.out.output_orthography = obj
            elif kind == "syllabification":
                self.out.syllabification = obj
            else:
                raise YascDefinitionError("!use expects a Phonology, Orthography or Syllabification, "
                                          "but $%s is a %s" % (args[0].value[0], kind), args[0].loc)
        elif name == "orthography":
            inp = outp = None
            for w, v in zip(args[::2], args[1::2]):
                kind, obj = self._lookup_def(v, "orthography")
                if w.value == "input":
                    inp = obj
                else:
                    outp = obj
            if inp is not None:
                self.working = inp
                self.out.input_orthography = inp
            if outp is not None:
                self.out.output_orthography = outp
            refs = (inp, outp)
        elif name in ("syllabify", "paradigm") and args:
            refs = self._lookup_def(args[0], "syllabification" if name == "syllabify" else "paradigm")[1]
        elif name == "print":
            refs = self._check_print(st)
        elif name == "set":
            key, value = args[0].value, args[1].value
            if key not in SETTINGS:
                close = difflib.get_close_matches(key, list(SETTINGS), n=1, cutoff=0.6)
                raise YascDefinitionError("unknown setting %r" % key, args[0].loc,
                                          hint=("did you mean %r?" % close[0]) if close else
                                          "settings: " + ", ".join(SETTINGS))
            check = SETTINGS[key]
            if check is int:
                try:
                    int(value)
                except ValueError:
                    raise YascDefinitionError("!set %s needs an integer, got %r" % (key, value), args[1].loc) from None
            elif isinstance(check, tuple) and value not in check:
                raise YascDefinitionError("!set %s must be one of %s, got %r" % (key, ", ".join(check), value),
                                          args[1].loc)
            elif key == "InputFormat" and not (value in ("tsv", "csv", "lines") or value.startswith("regex:")):
                raise YascDefinitionError("!set InputFormat must be tsv, csv, lines or regex:<pattern>", args[1].loc)
            elif key == "InputFormat" and value.startswith("regex:"):
                try:
                    re.compile(value[6:])
                except re.error as e:
                    raise YascDefinitionError("invalid InputFormat regex: %s" % e, args[1].loc) from None
        return IRCommand(name, args, refs, pending, st.canonical(), st.loc)

    def _lookup_def(self, arg, want: Optional[str] = None) -> Tuple[str, Any]:
        name = arg.value[0]
        hit = self.defs.get(name)
        if hit is None:
            close = difflib.get_close_matches(name, list(self.defs), n=1, cutoff=0.6)
            raise YascDefinitionError("unknown definition $%s" % name, arg.loc,
                                      hint=("did you mean $%s?" % close[0]) if close else
                                      "definitions are named with $%s := ... before use" % name)
        if want is not None and hit[0] != want:
            raise YascDefinitionError("$%s is a %s, not a %s" % (name, hit[0], want), arg.loc)
        return hit

    def _check_print(self, st: Command) -> Dict[str, Any]:
        fmt = st.args[0].value
        nargs = len(st.args) - 1
        orths: Dict[str, Any] = {}
        for m in _RE_DIRECTIVE.finditer(fmt):
            if m.group(0) == "%%":
                continue
            if m.group(0) == "%":
                raise YascDefinitionError("unknown !print directive at %r" % fmt[m.start():m.start() + 3],
                                          st.args[0].loc, hint="directives: %O{i} %O[$Orth]{i} %S{i} %I{i} %F{i} "
                                          "%L{i} %% (spec §10.5)")
            idx = int(m.group(2) or m.group(4))
            if idx >= nargs:
                raise YascDefinitionError("!print directive %s refers to argument %d, but only %d argument%s given"
                                          % (m.group(0), idx, nargs, "" if nargs == 1 else "s"), st.args[0].loc,
                                          hint="arguments are numbered from 0 (spec §10.5)")
            if m.group(1):
                orths[m.group(1)] = self._lookup_def(Arg("var", (m.group(1), None), st.args[0].loc),
                                                     "orthography")[1]
        return orths

    def _set_date(self, n: int, loc: Optional[SourceLoc]) -> None:
        if self.last_date is not None and n < self.last_date[0]:
            prev, ploc = self.last_date
            raise YascDefinitionError("date %d is earlier than the previous date %d%s" %
                                      (n, prev, " (%s)" % ploc if ploc else ""), loc,
                                      hint="dates must be non-decreasing in file order (spec §8.10)")
        self.last_date = (n, loc)

    # -- modifiers -------------------------------------------------------------------------

    @staticmethod
    def _merge(inherited: Sequence[Modifier], own: Sequence[Modifier]) -> Tuple[Modifier, ...]:
        """Own modifiers plus inherited ones of categories the member does not set; contexts
        and filters are added together (spec §8.8)."""
        cats = {MOD_CATEGORY[m.key] for m in own}
        out = list(own)
        for m in inherited:
            c = MOD_CATEGORY[m.key]
            if c in ("ctx", "filter") or c not in cats:
                out.append(m)
        return tuple(out)

    def _check_dups(self, mods: Sequence[Modifier], what: str) -> bool:
        seen: Dict[str, Modifier] = {}
        ok = True
        for m in mods:
            c = MOD_CATEGORY[m.key]
            if c in _ADDITIVE:
                continue
            if c in seen:
                prev = seen[c]
                if prev.key == m.key:
                    self.err(YascSyntaxError, "modifier %s is given twice on this %s" % (m.key, what), m.loc)
                else:
                    self.err(YascSyntaxError, "modifiers %s and %s conflict on this %s" % (prev.key, m.key, what),
                             m.loc, hint="a %s has one %s" % (what, {"mode": "mode (/:1 or /*)", "dir": "direction",
                                                                   "vis": "visibility (/:F±)"}.get(c, c)))
                ok = False
            seen[c] = m
        return ok

    def _date_of(self, mods: Sequence[Modifier]) -> Optional[int]:
        for m in mods:
            if m.key == "/:@":
                self._set_date(m.arg, m.loc)
                return m.arg
        return self.current_date

    # -- contexts, filters, visibility -----------------------------------------------------

    def _anchored(self, pat: Pattern):
        left, right = split_at_locus(pat)
        lnfa = None if isinstance(left, Nothing) else compile_pattern(left).reversed()
        rnfa = None if isinstance(right, Nothing) else compile_pattern(right)
        return left, right, lnfa, rnfa

    def _check_backrefs(self, pat: Pattern, n_items: int, where: str) -> None:
        for x in walk(pat):
            if isinstance(x, BackRef) and x.n > n_items:
                raise YascDefinitionError("$%d in %s refers to LHS item %d, but the LHS has %d item%s"
                                          % (x.n, where, x.n, n_items, "" if n_items == 1 else "s"), x.loc,
                                          hint="$n numbers the top-level LHS items from 1; $0 is the whole match "
                                          "(spec §6.4)")

    def _filter(self, m: Modifier, scope: _Scope, rule: _Rule, n_items: Optional[int],
                group: bool = False) -> IRFilter:
        pat = self._pattern(m.arg, scope, rule)
        has_locus = any(isinstance(x, Locus) for x in walk(pat))
        stage, positive = m.key[2], m.key[3] == "+"
        if has_locus and group:
            raise YascDefinitionError("a group filter after ]] cannot use ___", m.loc,
                                      hint="group filters test the whole form (spec §8.8)")
        if n_items is not None:
            self._check_backrefs(pat, n_items, "a filter")
        elif any(isinstance(x, BackRef) for x in walk(pat)):
            raise YascDefinitionError("$n cannot be used in a group filter", m.loc)
        if has_locus:
            _l, _r, lnfa, rnfa = self._anchored(pat)
            return IRFilter(stage, positive, True, None, lnfa, rnfa, pat, m.canonical(), m.loc)
        return IRFilter(stage, positive, False, compile_pattern(pat), None, None, pat, m.canonical(), m.loc)

    def _visibility(self, m: Modifier, scope: _Scope, rule: _Rule) -> IRVisibility:
        pat = self._pattern(m.arg, scope, rule)

        def single(p) -> List[SegmentSpec]:
            if isinstance(p, Spec):
                return [p.spec]
            if isinstance(p, Ortho) and len(p.specs) == 1:
                return [p.specs[0]]
            if isinstance(p, Alt):
                return [s for x in p.items for s in single(x)]
            raise YascDefinitionError("/:F± takes a single-segment pattern (a spec, a macro for one, or a "
                                      "disjunction of them), not %s" % p.canonical(), m.loc, hint="spec §8.5")
        specs = single(pat)
        return IRVisibility(m.key == "/:F+", tuple(specs), compile_pattern(pat), m.canonical(), m.loc)

    # -- basic rules -----------------------------------------------------------------------

    @staticmethod
    def _has_tier(nodes) -> Optional[Pattern]:
        for n in nodes:
            for x in walk(n):
                if isinstance(x, (Linked, AutoFloat, LinkOp)):
                    return x
        return None

    def _basic(self, r: BasicRule, scope: _Scope, inherited: Tuple[Modifier, ...]):
        own_ok = self._check_dups(r.mods, "rule")
        mods = self._merge(inherited, r.mods)
        self.rule_id += 1
        rid = self.rule_id
        by = {}
        for m in mods:
            by.setdefault(m.key, []).append(m)
        name = by['/"'][0].arg if '/"' in by else None
        date = self._date_of(mods)
        if not own_ok:
            return None
        pending = []
        for m in mods:
            ph = _MOD_PHASE.get(m.key)
            if ph and ph not in pending:
                if not self.unimpl(ph, "modifier %s" % m.key, m.loc):
                    return None
                pending.append(ph)
        rule = _Rule()
        fs = self._need_fs(r.loc)
        if "/:T" in by:  # plan P8: a tier-only rule is a rule over the projected tier (spec §6.5)
            tfeat = _tiers.resolve_tier(fs, by["/:T"][0].arg, by["/:T"][0].loc)
            r = _tiers.tier_rule_syntax(r, tfeat)
            mods = _tiers.tier_mods(mods, tfeat)
        lhs_syn = self._pattern(r.lhs, scope, rule)
        lhs_num = number_lhs(lhs_syn)
        n_items = self._count_items(lhs_num)
        lhs_nfa = compile_pattern(lhs_num)
        items = self._lhs_items(lhs_num)
        rhs: List[IRRhsItem] = []
        for pos, it in enumerate(r.rhs.items):
            node = self._expand(it.pattern, scope)
            rhs.extend(self._rhs(node, it.weak, items, pos, rule, n_items))
        contexts, negs, ins, outs = [], [], [], []
        vis = None
        mode = None
        repeat = direction_rev = weak = raw = resyll = persistent = cyclic = False
        tier = None
        stoch = opt = None
        for m in mods:
            k = m.key
            if k in ("/", "/!"):
                pat = self._pattern(m.arg, scope, rule)
                self._check_backrefs(pat, n_items, "a context")
                left, right, lnfa, rnfa = self._anchored(pat)
                (contexts if k == "/" else negs).append(
                    IRContext(k == "/!", lnfa, rnfa, left, right, m.canonical(), m.loc))
            elif k in ("/:i+", "/:i-", "/:o+", "/:o-"):
                f = self._filter(m, scope, rule, n_items)
                (ins if f.stage == "i" else outs).append(f)
            elif k in ("/:F+", "/:F-"):
                vis = self._visibility(m, scope, rule)
            elif k == "/:1":
                mode = "once"
            elif k == "/*":
                mode = "iterative"
            elif k == "/:*":
                repeat = True
            elif k == "/:<":
                direction_rev = True
            elif k == "/:~":
                weak = True
            elif k == "/:Raw":
                raw = True
            elif k == "/:$":
                resyll = True
            elif k == "/:T":
                tier = m.arg
            elif k == "/%":
                stoch = IRStochastic(m.arg[0], m.arg[1])
                if not 0 <= m.arg[0] <= 100:
                    raise YascDefinitionError("/%% takes a probability between 0 and 100, got %s" % m.arg[0], m.loc)
            elif k == "/???":
                opt = IROptional(m.arg)
            elif k == "/::":
                persistent = True
            elif k == "/:C*":
                cyclic = True
        self._static_checks(fs, lhs_num, contexts, ins, outs, negs, rhs, r)
        ir = IRBasicRule(
            rid, name, lhs_num, lhs_nfa, lhs_nfa.first_specs, n_items, tuple(rhs), tuple(contexts), tuple(negs),
            tuple(ins), tuple(outs), mode, repeat, -1 if direction_rev else 1, vis, self._restrictions(mods),
            weak, raw, resyll, tier, date, stoch, opt, persistent, cyclic, tuple(pending), fs, r.canonical(), r,
            r.loc)
        self.out.rules.append(ir)
        if name:
            self.out.named[name] = ir
        if cyclic:
            return IRGroup("seq", (ir,), None, cyclic=True, pending=(), source=r.canonical(), syntax=r,
                           loc=r.loc)
        return ir

    @staticmethod
    def _count_items(lhs_num: Pattern) -> int:
        body = lhs_num.pattern if isinstance(lhs_num, Capture) and lhs_num.n == 0 else lhs_num
        items = body.items if isinstance(body, Seq) else (body,)
        return sum(1 for x in items if isinstance(x, Capture))

    @staticmethod
    def _lhs_items(lhs_num: Pattern) -> List[Pattern]:
        body = lhs_num.pattern if isinstance(lhs_num, Capture) and lhs_num.n == 0 else lhs_num
        items = body.items if isinstance(body, Seq) else (body,)
        return [x.pattern for x in items if isinstance(x, Capture)]

    def _rhs(self, node: Pattern, weak: bool, lhs_items: List[Pattern], pos: int, rule: _Rule,
             n_items: int, merge: bool = False) -> List[IRRhsItem]:
        """Compile one (macro-expanded) RHS item (spec §8.2.4). ``[x]`` replaces the matched
        segment; ``merge`` is set under ``+[x]`` / ``+Macro`` / ``+<< >>``, where ``[x]``
        merges its declared features into it instead."""
        if isinstance(node, Merge):
            return self._rhs(node.item, weak, lhs_items, pos, rule, n_items, merge=True)
        src = node.canonical()
        loc = getattr(node, "loc", None)
        if merge and not isinstance(node, (RawOrtho, Alt, Seq)):
            raise YascDefinitionError("'+' (merge) applies only to [x], or to a macro or << | >> of them",
                                      loc, hint="a spec {...} already modifies the matched segment; write it "
                                      "without '+' (spec §8.2.4)")
        if isinstance(node, RawSpec):
            spec = self._spec(node).for_output(node.loc)
            return [IRRhsItem("weak" if weak else "spec", spec=spec, source=src, loc=loc)]
        if weak:
            raise YascDefinitionError("'~' (weak modification) applies only to a spec {...}", loc)
        if isinstance(node, RawOrtho):
            # [x] (and '[x]) replaces the matched segment with x, implications applied;
            # +[x] merges x's declared features only (spec §8.2.4).
            kind = "merge" if merge else "replace"
            segs = self._ortho_segments(node.text, node.loc, close=not merge)
            return [IRRhsItem(kind, segment=s, source=src, loc=loc) for s in segs]
        if isinstance(node, BackRef):
            if node.n > n_items:
                raise YascDefinitionError("$%d on the right-hand side refers to LHS item %d, but the LHS has %d "
                                          "item%s" % (node.n, node.n, n_items, "" if n_items == 1 else "s"), loc,
                                          hint="$n numbers the top-level LHS items from 1 (spec §6.4)")
            return [IRRhsItem("backref", n=node.n, source=src, loc=loc)]
        if isinstance(node, Combine):
            specs = self._alternatives(node, rule)
            if len(specs) == 1:
                return [IRRhsItem("spec", spec=specs[0].for_output(loc), source=src, loc=loc)]
            node = Alt(None, tuple(Spec(s, loc) for s in specs), loc)
        if isinstance(node, Alt):
            target = lhs_items[pos] if pos < len(lhs_items) else None
            if not isinstance(target, Alt):
                raise YascDefinitionError(
                    "a disjunction on the right-hand side needs an aligned LHS disjunction (item %d)" % (pos + 1), loc,
                    hint="class correspondence maps << a | b >> --> << c | d >> by index (spec §8.2.4)")
            if len(target.items) != len(node.items):
                raise YascDefinitionError(
                    "class correspondence: the RHS disjunction has %d alternatives but the aligned LHS disjunction "
                    "has %d" % (len(node.items), len(target.items)), loc,
                    hint="both disjunctions need the same number of alternatives (spec §8.2.4)")
            alts = []
            for x in node.items:
                if isinstance(x, Spec):
                    alts.append((IRRhsItem("spec", spec=x.spec.for_output(loc), source=x.canonical(), loc=loc),))
                else:
                    alts.append(tuple(self._rhs(x, False, lhs_items, pos, rule, n_items, merge=merge)))
            return [IRRhsItem("classcorr", alt=target.id, alternatives=tuple(alts), source=src, loc=loc)]
        if isinstance(node, Seq):
            out: List[IRRhsItem] = []
            for x in node.items:
                out.extend(self._rhs(x, False, lhs_items, pos, rule, n_items, merge=merge))
            return out
        if isinstance(node, Nothing):
            return []
        if isinstance(node, Spec):
            return [IRRhsItem("spec", spec=node.spec.for_output(loc), source=src, loc=loc)]
        if isinstance(node, (Linked, LinkOp, AutoFloat)):  # plan P8: spec §6.5 RHS table
            return _tiers.compile_rhs(self, node, lhs_items, pos, rule, n_items)
        raise YascDefinitionError("%s is not allowed on the right-hand side" % src, loc,
                                  hint="RHS items are specs, [x], '[x], $n, 0 and disjunctions (spec §8.2.4)")

    def _static_checks(self, fs, lhs, contexts, ins, outs, negs, rhs, r: BasicRule) -> None:
        """Unbound RHS variables and variables shared by incompatible features (spec §6.2;
        design §13, decision 13)."""
        bound = set()
        uses: Dict[str, List[Tuple[Any, Optional[SourceLoc]]]] = {}

        def specs_of(p):
            for x in walk(p):
                if isinstance(x, Spec):
                    yield x.spec
                elif isinstance(x, Ortho):
                    yield from x.specs

        def note(spec):
            for c in spec.constraints:
                if c.is_var:
                    uses.setdefault(c.name, []).append((c.feature, c.loc))

        binders = [lhs] + [p for c in contexts for p in (c.left_pattern, c.right_pattern)] + \
                  [f.pattern for f in ins if f.positive]
        for p in binders:
            for s in specs_of(p):
                bound |= s.var_names()
                note(s)
        for p in [p for c in negs for p in (c.left_pattern, c.right_pattern)] + \
                 [f.pattern for f in outs] + [f.pattern for f in ins if not f.positive]:
            for s in specs_of(p):
                note(s)

        def rhs_specs(items):
            for it in items:
                if it.spec is not None:
                    yield it.spec, it.loc
                for alt in it.alternatives:
                    yield from rhs_specs(alt)
        for spec, loc in rhs_specs(rhs):
            note(spec)
            for c in spec.constraints:
                if c.feature.scope == "role":
                    self.err(YascDefinitionError,
                             "%s is a read-only role pseudo-feature and cannot be written" % c.feature.name,
                             c.loc or loc, hint="SylOnset, SylNucleus, SylCoda and Syllabified are derived from "
                             "the syllable tier (spec §5.4)")
            for c in spec.constraints:
                if c.is_var and c.name not in bound:
                    self.err(YascDefinitionError,
                             "variable %r on the right-hand side is not bound by the LHS or a positive context"
                             % c.name, c.loc or loc,
                             hint="bind it in the LHS or a context, e.g. {(%s)%s} (spec §6.2)"
                             % (c.name, c.feature.name))
        for var, lst in uses.items():
            first = lst[0][0]
            for feat, loc in lst[1:]:
                if feat is first:
                    continue
                if not first.type.shares_values_with(feat.type):
                    self.err(YascDefinitionError,
                             "variable %r is used on %s (%s) and %s (%s), whose values differ"
                             % (var, first.name, first.type.canonical(), feat.name, feat.type.canonical()), loc,
                             hint="a variable may be shared only by features with identical value sets, or Binary "
                             "ones (spec §6.2)")
                    break

    # -- groups and sections ---------------------------------------------------------------

    def _check_lead(self, lead: Sequence[Modifier]) -> None:
        self._check_dups(lead, "group")
        for m in lead:
            if m.key == '/"':
                raise YascSyntaxError('a group is named after its ]], not after [[', m.loc,
                                      hint='write ]] /" Name')

    def _group_body(self, body, scope: _Scope, inherited) -> List[Any]:
        inner = _Scope(scope, "block")
        members: List[Any] = []
        for st in body:
            members.extend(self._item(st, inner, inherited))
        return members

    def _group_trail(self, trail: Sequence[Modifier], scope: _Scope) -> Dict[str, Any]:
        self._check_dups(trail, "group")
        info: Dict[str, Any] = {"repeat": False, "weak": False, "cyclic": False, "persistent": False,
                                "name": None, "stoch": None, "opt": None, "in": [], "out": [], "pending": []}
        for m in trail:
            if m.key not in _TRAIL_OK:
                raise YascSyntaxError("modifier %s is not allowed after ]]" % m.key, m.loc,
                                      hint="put contexts, /*, /:F, /:T and other rule modifiers after [[ so the "
                                      "members inherit them (spec §8.8)")
        for m in trail:
            k = m.key
            ph = _MOD_PHASE.get(k)
            if ph and ph not in info["pending"]:
                if not self.unimpl(ph, "modifier %s" % k, m.loc):
                    raise _Abort()
                info["pending"].append(ph)
            if k == "/:*":
                info["repeat"] = True
            elif k == "/:~":
                info["weak"] = True
            elif k == "/:C*":
                info["cyclic"] = True
            elif k == "/::":
                info["persistent"] = True
            elif k == '/"':
                info["name"] = m.arg
            elif k == "/%":
                info["stoch"] = IRStochastic(m.arg[0], m.arg[1])
            elif k == "/???":
                info["opt"] = IROptional(m.arg)
            elif k in ("/:i+", "/:i-", "/:o+", "/:o-"):
                f = self._filter(m, scope, _Rule(), None, group=True)
                info["in" if f.stage == "i" else "out"].append(f)
        info["date"] = None
        for m in trail:
            if m.key == "/:@":
                self._set_date(m.arg, m.loc)
                info["date"] = m.arg
        info["restr"] = self._restrictions(trail)
        return info

    def _group(self, g: Group, scope: _Scope, inherited: Tuple[Modifier, ...]) -> Optional[IRGroup]:
        self._check_lead(g.lead)
        eff = self._merge(inherited, g.lead)
        members = self._group_body(g.body, scope, eff)
        try:
            info = self._group_trail(g.trail, scope)
        except _Abort:
            return None
        ir = IRGroup(g.kind, tuple(members), info["name"], info["repeat"], info["restr"], info["date"],
                     info["stoch"], info["opt"], info["weak"], tuple(info["in"]), tuple(info["out"]), info["cyclic"],
                     info["persistent"], False, tuple(info["pending"]), g.canonical().split("\n")[0], g, g.loc)
        if ir.name:
            self.out.named[ir.name] = ir
        return ir

    def _section(self, sec: RulesSection) -> Optional[IRGroup]:
        self._check_lead(sec.lead)
        members = self._group_body(sec.body, self.globals, tuple(sec.lead))
        try:
            info = self._group_trail(sec.trail, self.globals)
        except _Abort:
            return None
        name = info["name"] or sec.name
        ir = IRGroup("seq", tuple(members), name, info["repeat"], info["restr"], info["date"], info["stoch"],
                     info["opt"], info["weak"], tuple(info["in"]), tuple(info["out"]), info["cyclic"],
                     info["persistent"], True, tuple(info["pending"]), sec.canonical().split("\n")[0], sec, sec.loc)
        if sec.name:
            self._define("rules", sec.name, ir, sec.loc)
            self.out.named[sec.name] = ir
        if info["name"]:
            self.out.named[info["name"]] = ir
        return ir


class _Abort(Exception):
    """Internal: stop compiling a group whose error was already recorded."""


ENUM_KIND = "enum"


def compile_script(script: Script, filename: str = "<string>", *, allow_unimplemented: bool = False,
                   text: Optional[str] = None) -> CompiledScript:
    """Compile a parsed :class:`~yasc.syntax.Script` (design §7.3). Raises
    :class:`YascLoadError` holding every error (spec §12)."""
    return Compiler(filename, allow_unimplemented).compile(script, text)


def compile_source(text: str, filename: str = "<string>", *, allow_unimplemented: bool = False) -> CompiledScript:
    """Parse and compile a script (design §7.3; spec §2, §12).

    ``filename`` is used in locations and to resolve ``!include`` paths. Syntax errors are
    raised before compilation starts; compile errors are all collected. With
    ``allow_unimplemented=True``, constructs of later plan phases compile to placeholders
    and warnings instead of :class:`~yasc.errors.NotImplementedYet` errors.
    """
    script = Parser(text, filename).parse()
    return compile_script(script, filename, allow_unimplemented=allow_unimplemented, text=text)


def compile_file(path: str, *, allow_unimplemented: bool = False) -> CompiledScript:
    """Read, parse and compile a script file (design §7.3)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return compile_source(text, path, allow_unimplemented=allow_unimplemented)
