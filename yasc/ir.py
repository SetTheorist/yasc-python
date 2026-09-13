"""Rule IR: the data-only output of :mod:`yasc.compile` for the rule engine (plan P5), the
runtime (P6) and the later phases P7–P9 (design §7.3, §10).

Nothing here executes. Every record is a frozen dataclass; rules and groups compare by
identity (``eq=False``) because they hold NFAs. The top-level result is
:class:`CompiledScript`.

Conventions:

* Contexts keep ``left`` as the **reversed** NFA of ``C`` and ``right`` as the forward NFA of
  ``D``; either is ``None`` for an empty side. Pass them to
  :func:`yasc.matcher.match_context` as ``(left, right)`` (spec §8.2 step 2).
* ``IRBasicRule.lhs`` is the NFA of the numbered LHS (captures ``0..n_items``); pass
  ``first_specs`` to :func:`yasc.matcher.find_all` (design §6).
* ``mode`` is ``None`` (use the ``DefaultMode`` setting), ``"once"`` (``/:1``) or
  ``"iterative"`` (``/*``); ``repeat`` is ``/:*`` (spec §8.3).
* ``pending`` lists plan phases whose semantics the record still needs (for example
  ``("P9",)`` for ``/%``). The compiler only produces such records with
  ``allow_unimplemented=True``; the engine must refuse or skip them.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .errors import SourceLoc, YascError

__all__ = [
    "IRContext", "IRFilter", "IRVisibility", "IRLexical", "IRNames", "IRRestrictions", "IRStochastic",
    "IROptional", "IRRhsItem", "IRBasicRule", "IRGroup", "IRInvoke", "IRCommand", "IRAssign", "IRPlaceholder",
    "MacroInfo", "SyllabificationDef", "ParadigmCell", "ParadigmDef", "ConstraintDef", "CompiledScript",
    "RHS_KINDS",
]


def _loc():
    return field(default=None, compare=False, repr=False)


@dataclass(frozen=True, eq=False)
class IRContext:
    """``/ C ___ D`` or ``/! C ___ D`` (spec §8.1, §8.2 steps 2–3). ``left`` is the reversed
    NFA of ``C`` (``None`` if empty), ``right`` the forward NFA of ``D`` (``None`` if empty);
    ``left_pattern``/``right_pattern`` are the compiled patterns (``C`` in textual order)."""

    negative: bool
    left: Any
    right: Any
    left_pattern: Any
    right_pattern: Any
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRFilter:
    """``/:i± E`` or ``/:o± E`` (spec §8.4). Without ``___`` (``has_locus`` false) ``anywhere``
    is the NFA to search for anywhere in the form; with ``___`` the filter is anchored at the
    focus (input: the LHS span; output: the replaced span) like a context: ``left`` is the
    reversed NFA of the part before ``___`` and ``right`` the forward NFA of the part after
    it (``None`` when empty)."""

    stage: str
    positive: bool
    has_locus: bool
    anywhere: Any = None
    left: Any = None
    right: Any = None
    pattern: Any = None
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRVisibility:
    """``/:F+ S`` (segments matching ``S`` are invisible) or ``/:F- S`` (segments *not*
    matching are invisible) (spec §8.5). ``specs``: a segment matches ``S`` iff it matches
    one of them; ``nfa`` is the single-segment NFA of ``S``."""

    positive: bool
    specs: Tuple[Any, ...]
    nfa: Any = None
    source: str = ""
    loc: Optional[SourceLoc] = _loc()

    def hides(self, view, env=None) -> bool:
        """True if the segment ``view`` is invisible to the rule (spec §8.5)."""
        from .segment import EMPTY
        m = any(s.matches(view, env or EMPTY) for s in self.specs)
        return m if self.positive else not m


@dataclass(frozen=True)
class IRLexical:
    """``/:L± {spec}`` (spec §8.6): lexical features are declared by the lexicon, so the
    constraints stay unresolved :class:`~yasc.lexer.ConstraintSyntax` records."""

    positive: bool
    constraints: Tuple[Any, ...]
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True)
class IRNames:
    """``/:D± G``/``/:D± (G1 G2)`` or ``/:C± N``/``/:C± (N V)`` (spec §8.6)."""

    positive: bool
    names: Tuple[str, ...]
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True)
class IRRestrictions:
    """Lexical, dialect and category restrictions of a rule or group (spec §8.6)."""

    lexical: Tuple[IRLexical, ...] = ()
    dialect: Tuple[IRNames, ...] = ()
    category: Tuple[IRNames, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.lexical or self.dialect or self.category)


@dataclass(frozen=True)
class IRStochastic:
    """``/%n`` or ``/%n:each`` (spec §8.7)."""

    percent: float
    each: bool = False


@dataclass(frozen=True)
class IROptional:
    """``/???`` or ``/???:each`` (spec §8.7, §8.11)."""

    each: bool = False


#: :attr:`IRRhsItem.kind` values (spec §8.2.4).
RHS_KINDS = ("spec", "weak", "merge", "replace", "backref", "classcorr", "tier")


@dataclass(frozen=True, eq=False)
class IRRhsItem:
    """One compiled RHS item (spec §8.2.4). ``kind``:

    * ``spec`` — ``{...}``: apply ``spec`` as output (``SegmentSpec.apply``; a strict spec
      ``'{...}`` replaces the segment). Inserted (excess) items build a segment from
      ``fs.empty``;
    * ``weak`` — ``~{...}``: ``spec.weak_apply``;
    * ``replace`` — ``[x]`` (or ``'[x]``): the segment (x with implications applied)
      replaces ``m`` entirely;
    * ``merge`` — ``+[x]``: ``m.merge(segment)`` (x's declared features, no implications);
    * ``backref`` — ``$n``: insert a copy of capture ``n`` (``n``);
    * ``classcorr`` — class correspondence: the output is ``alternatives[k]`` (a tuple of
      items) where ``k`` is the index recorded for LHS disjunction ``alt`` in the Env;
    * ``tier`` — an autosegmental item (``pattern``); plan P8.
    """

    kind: str
    spec: Any = None
    segment: Any = None
    n: Optional[int] = None
    alt: Optional[int] = None
    alternatives: Tuple[Tuple["IRRhsItem", ...], ...] = ()
    pattern: Any = None
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRBasicRule:
    """A compiled basic rule ``LHS --> RHS mods`` with inherited modifiers applied
    (spec §8.1–§8.10; design §10 ``BasicRule``).

    ``id`` numbers basic rules from 1 in file order; ``source`` is the canonical text of the
    rule as written (spec §11.1 ``--list-rules``). ``lhs_pattern`` is the numbered pattern
    (``Capture`` nodes), ``lhs`` its NFA, ``first_specs`` the pre-filter, ``n_items`` the
    number of top-level LHS items (``$1..$n``). ``rhs`` is ``()`` for deletion.
    """

    id: int
    name: Optional[str]
    lhs_pattern: Any
    lhs: Any
    first_specs: Any
    n_items: int
    rhs: Tuple[IRRhsItem, ...]
    contexts: Tuple[IRContext, ...] = ()
    neg_contexts: Tuple[IRContext, ...] = ()
    in_filters: Tuple[IRFilter, ...] = ()
    out_filters: Tuple[IRFilter, ...] = ()
    mode: Optional[str] = None
    repeat: bool = False
    direction: int = 1
    visibility: Optional[IRVisibility] = None
    restrictions: IRRestrictions = IRRestrictions()
    weak: bool = False
    raw: bool = False
    resyllabify: bool = False
    tier: Optional[str] = None
    date: Optional[int] = None
    stochastic: Optional[IRStochastic] = None
    optional: Optional[IROptional] = None
    persistent: bool = False
    cyclic: bool = False
    pending: Tuple[str, ...] = ()
    phonology: Any = None
    source: str = ""
    syntax: Any = None
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRGroup:
    """A rule group or a ``Rules`` section run as a group (spec §8.8; design §10 ``Group``).

    ``kind`` is ``seq``, ``and`` or ``or``. ``members`` are rules, groups, invocations,
    commands, assignments and placeholders in order (inherited modifiers already applied to
    the member rules). The remaining fields come from the modifiers after ``]]``: ``repeat``
    (``/:*``), restrictions, ``date``, ``stochastic``, ``optional``, ``weak``, whole-form
    ``in_filters``/``out_filters`` (``has_locus`` is always false), ``cyclic`` (``/:C*``),
    ``persistent`` (``/::``) and ``name`` (``/"`` or the section variable). ``section`` is
    true for a ``Rules`` section.
    """

    kind: str
    members: Tuple[Any, ...]
    name: Optional[str] = None
    repeat: bool = False
    restrictions: IRRestrictions = IRRestrictions()
    date: Optional[int] = None
    stochastic: Optional[IRStochastic] = None
    optional: Optional[IROptional] = None
    weak: bool = False
    in_filters: Tuple[IRFilter, ...] = ()
    out_filters: Tuple[IRFilter, ...] = ()
    cyclic: bool = False
    persistent: bool = False
    section: bool = False
    pending: Tuple[str, ...] = ()
    source: str = ""
    syntax: Any = None
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRInvoke:
    """``$Name`` inside Rules: run the named section or group ``target`` (spec §8.8)."""

    name: str
    target: Any
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRCommand:
    """A run-time command (spec §10.5). ``args`` are :class:`~yasc.syntax.Arg` records;
    ``refs`` holds resolved definitions (``!use``: ``(kind, obj)``; ``!orthography``:
    ``(input_or_None, output_or_None)``; ``!syllabify``/``!paradigm``: the definition;
    ``!print``: the orthographies named by ``%O[$X]``, by name). ``pending`` is the plan phase
    that implements the command, if it is not P6. Load-time commands (``!include``,
    ``!date``) are not emitted."""

    name: str
    args: Tuple[Any, ...] = ()
    refs: Any = None
    pending: Optional[str] = None
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRAssign:
    """``$name := expr`` (spec §10.1). ``kind`` is ``var`` (``value`` = variable name,
    including ``_``, ``in``, ``raw``, ``NF``, ``NR``), ``field`` (``value`` = n) or ``ortho``
    (``value`` = text, ``form`` = the :class:`~yasc.form.Form` parsed with the working
    orthography at load time)."""

    name: str
    kind: str
    value: Any
    form: Any = None
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class IRPlaceholder:
    """A construct that parsed but cannot be compiled before plan phase ``phase`` (only
    produced with ``allow_unimplemented=True``). ``syntax`` is the AST node; ``date`` and
    ``name`` are kept so rule listings stay complete."""

    phase: str
    what: str
    syntax: Any = None
    name: Optional[str] = None
    date: Optional[int] = None
    source: str = ""
    id: Optional[int] = None
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class MacroInfo:
    """A macro definition (spec §7): ``body`` is the unexpanded syntax pattern; ``scope`` is
    ``global`` or ``block``."""

    name: str
    body: Any
    scope: str = "global"
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class SyllabificationDef:
    """A validated ``Syllabification`` section for plan P7 (spec §5.7). ``settings`` maps
    each written key to its word or canon tuple (defaults are not filled in);
    ``templates`` maps ``Onset``/``Nucleus``/``Coda`` to ``(pattern, nfa)`` (``nfa`` is
    ``None`` for an empty template)."""

    name: Optional[str]
    settings: Dict[str, Any]
    templates: Dict[str, Tuple[Any, Any]]
    phonology: Any = None
    syntax: Any = None
    loc: Optional[SourceLoc] = _loc()
    #: Plan P7: for ``Algorithm Canon``, each canon letter (``C``, ``V``) mapped to the tuple
    #: of single-segment specs its macro expands to (spec §5.7 "the letters are macros").
    letters: Dict[str, Tuple[Any, ...]] = field(default_factory=dict)


@dataclass(frozen=True, eq=False)
class ParadigmCell:
    """One paradigm cell for plan P9 (spec §9). ``items`` are ``(kind, value)`` pairs:
    ``("stem", None)``, ``("ortho", Form)``, ``("boundary", Mark)``, ``("open", label)``,
    ``("close", None)``."""

    label: str
    items: Tuple[Tuple[str, Any], ...]
    restrictions: IRRestrictions = IRRestrictions()
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class ParadigmDef:
    """A validated ``Paradigm`` section for plan P9 (spec §9)."""

    name: Optional[str]
    cells: Tuple[ParadigmCell, ...]
    syntax: Any = None
    loc: Optional[SourceLoc] = _loc()


@dataclass(frozen=True, eq=False)
class ConstraintDef:
    """``Constraint * pattern`` of a phonology (spec §4.6; plan P9)."""

    pattern: Any
    nfa: Any
    phonology: Any = None
    source: str = ""
    loc: Optional[SourceLoc] = _loc()


@dataclass(eq=False)
class CompiledScript:
    """The result of :func:`yasc.compile.compile_source` (design §7.3).

    Definitions by variable name (without ``$``): ``phonologies``, ``orthographies``,
    ``syllabifications``, ``paradigms``, ``rule_sections`` (named ``Rules`` sections as
    :class:`IRGroup`); ``named`` maps ``/"`` names and section names to their rule, group
    or section. ``definitions`` lists every definition in file order as ``(kind, name,
    object)`` (unnamed ones included). ``phonology``, ``orthography``,
    ``input_orthography``, ``output_orthography`` and ``syllabification`` are the active
    definitions at the end of loading. ``statements`` is the ordered top-level list for the
    runtime: :class:`IRGroup` (Rules sections), top-level rule items, :class:`IRCommand` and
    :class:`IRAssign`. ``rules`` lists every compiled basic rule (and placeholder) in file
    order. ``warnings`` holds :class:`~yasc.errors.YascWarning` objects.
    """

    filename: str
    syntax: Any = None
    phonologies: Dict[str, Any] = field(default_factory=dict)
    orthographies: Dict[str, Any] = field(default_factory=dict)
    syllabifications: Dict[str, Any] = field(default_factory=dict)
    paradigms: Dict[str, Any] = field(default_factory=dict)
    rule_sections: Dict[str, Any] = field(default_factory=dict)
    macros: Dict[str, MacroInfo] = field(default_factory=dict)
    named: Dict[str, Any] = field(default_factory=dict)
    definitions: List[Tuple[str, Optional[str], Any]] = field(default_factory=list)
    constraints: List[ConstraintDef] = field(default_factory=list)
    phonology: Any = None
    orthography: Any = None
    input_orthography: Any = None
    output_orthography: Any = None
    syllabification: Any = None
    statements: List[Any] = field(default_factory=list)
    rules: List[Any] = field(default_factory=list)
    included: List[str] = field(default_factory=list)
    warnings: List[YascError] = field(default_factory=list)
