"""Statement and section AST produced by :mod:`yasc.parser` (spec §3–§10; design §7.2).

Every node is a frozen dataclass whose ``loc`` (a :class:`~yasc.errors.SourceLoc`) takes no
part in equality, and every node has :meth:`canonical`, which returns parseable source text
(spec §11.3): ``parse(canonical(parse(x))) == parse(x)``.

Patterns reuse the P2 pattern nodes of :mod:`yasc.pattern` (``Seq``, ``Alt``, ``Opt``,
``Star``, ``Plus``, ``Anything``, ``Nothing``, ``Boundary``, ``BracketAssert``, ``Locus``,
``BackRef``, ``Macro``, ``AutoFloat``, ``Linked``). Leaves that need a feature system or an
orthography stay unresolved here: :class:`RawSpec` (``{...}``), :class:`RawOrtho`
(``[...]``) and :class:`Combine` (``S1:S2``); :class:`LinkOp` is the right-hand-side
``S^+X`` / ``S^-=h`` form (spec §6.5). Alternatives have ``id=None`` until the compiler
numbers them.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from .errors import SourceLoc
from .lexer import ConstraintSyntax, escape_string
from .pattern import Pattern

__all__ = [
    "RawSpec", "RawOrtho", "Merge", "Combine", "LinkOp",
    "RhsItem", "Rhs", "Modifier", "MOD_ORDER", "sort_mods", "mods_text",
    "BasicRule", "Group", "Invoke", "MacroDef", "AssignExpr", "Assign", "Arg", "Command",
    "FeatureTypeSyntax", "TierSyntax", "AliasLine", "OpLine", "FeatureDecl", "ImplicationDecl",
    "ConstraintDecl", "PhonologySection",
    "PhonesLine", "DiacriticLine", "IgnoreLine", "SettingLine", "SyllableMarkLine", "OrthographySection",
    "SylSetting", "SyllabificationSection", "TplItem", "Cell", "ParadigmSection", "RulesSection",
    "Script", "SECTION_KEYWORDS",
]

#: Section keywords (spec §2); ``Phonetics`` is a synonym of ``Phonology`` (spec §4).
SECTION_KEYWORDS = ("Phonology", "Phonetics", "Orthography", "Syllabification", "Rules", "Paradigm")

INDENT = "  "


def _loc():
    return field(default=None, compare=False, repr=False)


def _indent(text: str, depth: int = 1) -> str:
    pad = INDENT * depth
    return "\n".join(pad + line if line else line for line in text.split("\n"))


# --------------------------------------------------------------------------------------------
# Unresolved pattern leaves
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSpec(Pattern):
    """``{c1 c2 ...}`` or strict ``'{...}`` before feature resolution (spec §6.1, §6.2)."""

    constraints: Tuple[ConstraintSyntax, ...]
    strict: bool = False
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``{+Syll -Voice}`` or ``'{...}`` (spec §6.2)."""
        return ("'" if self.strict else "") + "{" + " ".join(c.canonical() for c in self.constraints) + "}"


@dataclass(frozen=True)
class RawOrtho(Pattern):
    """``[text]`` or ``'[text]``: parsed later with the working orthography (spec §6.1)."""

    text: str
    strict: bool = False
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``[text]`` / ``'[text]`` (spec §6.1)."""
        return ("'" if self.strict else "") + "[" + self.text + "]"


@dataclass(frozen=True)
class Merge(Pattern):
    """Right-hand-side ``+[x]``, ``+Macro`` or ``+<< ... >>``: merge the declared features of
    ``x`` into the matched segment, instead of replacing it as plain ``[x]`` does
    (spec §8.2.4)."""

    item: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``+[x]`` (spec §8.2.4)."""
        return "+" + self.item.canonical()

    def children(self):
        return (self.item,)


@dataclass(frozen=True)
class Combine(Pattern):
    """``S1:S2`` — one segment satisfying both operands (spec §6.1). ``Name:S`` with a macro
    on the left is a :class:`~yasc.pattern.Macro` with ``refine`` instead."""

    left: Pattern
    right: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``left:right`` (spec §6.1)."""
        return self.left.canonical() + ":" + self.right.canonical()

    def children(self):
        return (self.left, self.right)


@dataclass(frozen=True)
class LinkOp(Pattern):
    """RHS ``S^+[L]``, ``S^+=h`` (add a link) or ``S^-=h`` (delink ``h``) (spec §6.5)."""

    spec: Pattern
    tier: Optional[str]
    op: str
    x: str = ""
    name: Optional[str] = None
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``V^+[L]``, ``V^-=h`` (spec §6.5)."""
        return (self.spec.canonical() + "^" + self.op + (self.tier + "." if self.tier else "") + self.x
                + ("=" + self.name if self.name else ""))

    def children(self):
        return (self.spec,)


# --------------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RhsItem:
    """One right-hand-side item (spec §8.2.4); ``weak`` for ``~{...}``."""

    pattern: Pattern
    weak: bool = False
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``{...}``, ``~{...}``, ``[x]``, ``'[x]``, ``$n``, ``V^=h`` ... (spec §8.2.4)."""
        return ("~" if self.weak else "") + self.pattern.canonical()


@dataclass(frozen=True)
class Rhs:
    """A right-hand side: a sequence of items; ``()`` is ``0`` (deletion) (spec §8.1)."""

    items: Tuple[RhsItem, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """Items separated by spaces, or ``0`` (spec §8.1)."""
        return " ".join(i.canonical() for i in self.items) if self.items else "0"


#: Canonical modifier order (spec §8.7 "any order", §11.3 "fixed order").
MOD_ORDER = (
    "/", "/!", "/:i+", "/:i-", "/:o+", "/:o-", "/:F+", "/:F-", "/:L+", "/:L-", "/:D+", "/:D-",
    "/:C+", "/:C-", "/:C*", "/:1", "/*", "/:*", "/:>", "/:<", "/:~", "/:Raw", "/:$", "/:T",
    "/:@", "/%", "/???", '/"', "/::",
)
_MOD_RANK = {k: n for n, k in enumerate(MOD_ORDER)}

#: Modifier keys whose argument is a pattern (spec §8.1, §8.4, §8.5).
PATTERN_MODS = ("/", "/!", "/:i+", "/:i-", "/:o+", "/:o-", "/:F+", "/:F-")


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


@dataclass(frozen=True)
class Modifier:
    """A rule or group modifier (spec §8.1–§8.8).

    ``key`` is the normalised modifier (``/~`` is stored as ``/:~``); ``arg`` depends on it:
    a pattern for contexts, filters and ``/:F±``; a :class:`RawSpec` for ``/:L±``; a tuple
    of names for ``/:D±`` and ``/:C±``; a name for ``/:T`` and ``/"``; an int for ``/:@``;
    ``(percent, each)`` for ``/%``; ``each`` (bool) for ``/???``; ``None`` otherwise.
    """

    key: str
    arg: Any = None
    loc: Optional[SourceLoc] = _loc()

    @property
    def rank(self) -> int:
        """Position in :data:`MOD_ORDER`."""
        return _MOD_RANK[self.key]

    def canonical(self) -> str:
        """The modifier as written (spec §8)."""
        k, a = self.key, self.arg
        if k in PATTERN_MODS:
            return "%s %s" % (k, a.canonical())
        if k in ("/:L+", "/:L-"):
            return "%s %s" % (k, a.canonical())
        if k in ("/:D+", "/:D-", "/:C+", "/:C-"):
            return "%s %s" % (k, a[0] if len(a) == 1 else "(" + " ".join(a) + ")")
        if k == "/:T":
            return "/:T " + a
        if k == '/"':
            return '/" ' + a
        if k == "/:@":
            return "/:@%d" % a
        if k == "/%":
            return "/%" + _fmt_num(a[0]) + (":each" if a[1] else "")
        if k == "/???":
            return "/???" + (":each" if a else "")
        return k


def sort_mods(mods) -> Tuple[Modifier, ...]:
    """Modifiers in canonical order; equal keys keep their written order (spec §8.7)."""
    return tuple(sorted(mods, key=lambda m: m.rank))


def mods_text(mods) -> str:
    """Canonical text of a modifier list (with a leading space when non-empty)."""
    return "".join(" " + m.canonical() for m in mods)


@dataclass(frozen=True)
class BasicRule:
    """``LHS --> RHS modifier*`` (spec §8.1). Modifiers are sorted on construction."""

    lhs: Pattern
    rhs: Rhs
    mods: Tuple[Modifier, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "mods", sort_mods(self.mods))

    def canonical(self) -> str:
        """``lhs --> rhs mods`` (spec §8.1, §11.3)."""
        return "%s --> %s%s" % (self.lhs.canonical(), self.rhs.canonical(), mods_text(self.mods))


@dataclass(frozen=True)
class Group:
    """``[[ ... ]]`` (``seq``), ``&&[[ ... ]]`` (``and``) or ``||[[ ... ]]`` (``or``) with
    leading (inherited) and trailing (group) modifiers (spec §8.8)."""

    kind: str
    lead: Tuple[Modifier, ...]
    body: Tuple[Any, ...]
    trail: Tuple[Modifier, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "lead", sort_mods(self.lead))
        object.__setattr__(self, "trail", sort_mods(self.trail))
        object.__setattr__(self, "body", tuple(self.body))

    def canonical(self) -> str:
        """The group over several lines (spec §8.8)."""
        opener = {"seq": "[[", "and": "&&[[", "or": "||[[", }[self.kind]
        lines = [opener + mods_text(self.lead)]
        lines += [_indent(s.canonical()) for s in self.body]
        lines.append("]]" + mods_text(self.trail))
        return "\n".join(lines)


@dataclass(frozen=True)
class Invoke:
    """``$Name`` on its own line inside Rules: run a named Rules section or group (spec §8.8)."""

    name: str
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``$Name`` (spec §8.8)."""
        return "$" + self.name


@dataclass(frozen=True)
class MacroDef:
    """``Name === pattern`` (spec §7)."""

    name: str
    body: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Name === pattern`` (spec §7)."""
        return "%s === %s" % (self.name, self.body.canonical())


@dataclass(frozen=True)
class AssignExpr:
    """The right side of ``$x := expr`` (spec §10.1): ``kind`` is ``var`` (``$_``, ``$in``,
    ``$raw``, ``$NF``, ``$NR``, ``$name``; ``value`` is the name), ``field`` (``$field[n]``;
    ``value`` is n) or ``ortho`` (``[text]``; ``value`` is the text)."""

    kind: str
    value: Any
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``$name``, ``$field[n]`` or ``[text]`` (spec §10.1)."""
        if self.kind == "field":
            return "$field[%d]" % self.value
        if self.kind == "ortho":
            return "[%s]" % self.value
        return "$" + self.value


@dataclass(frozen=True)
class Assign:
    """``$name := expr`` (``::=`` is a synonym) (spec §10.1)."""

    name: str
    expr: AssignExpr
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``$name := expr`` (spec §10.1)."""
        return "$%s := %s" % (self.name, self.expr.canonical())


@dataclass(frozen=True)
class Arg:
    """A command argument (spec §10.5): ``kind`` is ``str`` (a string literal), ``var``
    (``(name, index or None)``), ``int``, ``word``, ``list`` (a tuple of names) or ``kv``
    (``(key, value)``)."""

    kind: str
    value: Any
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """The argument as written."""
        k, v = self.kind, self.value
        if k == "str":
            return escape_string(v)
        if k == "var":
            return "$%s%s" % (v[0], "" if v[1] is None else "[%d]" % v[1])
        if k == "int":
            return str(v)
        if k == "list":
            return "(" + " ".join(v) + ")"
        if k == "kv":
            return "%s=%s" % v
        return v


@dataclass(frozen=True)
class Command:
    """``!name args...`` (spec §10.5)."""

    name: str
    args: Tuple[Arg, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``!name arg ...``; ``!set Name = value`` (spec §10.5)."""
        if self.name == "set":
            return "!set %s = %s" % (self.args[0].value, self.args[1].value)
        return " ".join(["!" + self.name] + [a.canonical() for a in self.args])


# --------------------------------------------------------------------------------------------
# Phonology
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureTypeSyntax:
    """A feature type as written (spec §4.1): ``kind`` is ``binary``, ``unary``,
    ``scalar`` (``bounds`` ``(lo, hi)`` or ``None``), ``values`` (an ad-hoc or bracketed value
    list, stored as written, e.g. ``('+', '-')`` or ``('[H]', '[L]')``) or ``node``
    (``children``)."""

    kind: str
    values: Tuple[str, ...] = ()
    bounds: Optional[Tuple[int, int]] = None
    children: Tuple[str, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Binary``, ``Unary``, ``Scalar(0,2)``, ``[H] [L]``, ``Node(A B)`` (spec §4.1)."""
        if self.kind == "binary":
            return "Binary"
        if self.kind == "unary":
            return "Unary"
        if self.kind == "scalar":
            return "Scalar" if self.bounds is None else "Scalar(%d,%d)" % self.bounds
        if self.kind == "node":
            return "Node(%s)" % " ".join(self.children)
        return " ".join(self.values)


@dataclass(frozen=True)
class TierSyntax:
    """``Tier(TBU={...}, Stray=float|delete, OCP=off|merge|delete)`` (spec §5.5); omitted
    settings are ``None``."""

    tbu: Optional[RawSpec] = None
    stray: Optional[str] = None
    ocp: Optional[str] = None
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Tier(TBU={+Syll}, Stray=float)`` (spec §5.5)."""
        parts = []
        if self.tbu is not None:
            parts.append("TBU=" + self.tbu.canonical())
        if self.stray is not None:
            parts.append("Stray=" + self.stray)
        if self.ocp is not None:
            parts.append("OCP=" + self.ocp)
        return "Tier(%s)" % ", ".join(parts)


@dataclass(frozen=True)
class AliasLine:
    """``== alias1 alias2`` after a feature (spec §4.1)."""

    names: Tuple[str, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``== a b`` (spec §4.1)."""
        return "== " + " ".join(self.names)


@dataclass(frozen=True)
class OpLine:
    """``(op) r1 ... rN`` after a feature; ``_`` = undefined (spec §4.1)."""

    op: str
    results: Tuple[str, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``(op) r1 r2`` (spec §4.1)."""
        return "(%s) %s" % (self.op, " ".join(self.results))


@dataclass(frozen=True)
class FeatureDecl:
    """A feature declaration with its alias and operation lines (spec §4.1)."""

    name: str
    type: FeatureTypeSyntax
    scope: Optional[str] = None
    tier: Optional[TierSyntax] = None
    aliases: Tuple[AliasLine, ...] = ()
    ops: Tuple[OpLine, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """The declaration line and its indented ``==`` / ``(op)`` lines (spec §4.1)."""
        head = "%s %s" % (self.name, self.type.canonical())
        if self.scope is not None:
            head += " Scope(%s)" % self.scope
        if self.tier is not None:
            head += " " + self.tier.canonical()
        lines = [head] + [INDENT + a.canonical() for a in self.aliases] + [INDENT + o.canonical() for o in self.ops]
        return "\n".join(lines)


@dataclass(frozen=True)
class ImplicationDecl:
    """``S --> T``, ``S ~~> T`` or a bidirectional ``S <L(op)R> T`` (spec §4.5).

    ``forward`` is ``--`` or ``~~`` (the right half, ``S → T``); ``backward`` is ``None`` for
    a one-way implication, else ``--`` or ``~~`` (the left half, ``T → S``); ``op`` is the
    operation of ``<--(op)-->`` or ``None``."""

    left: RawSpec
    right: RawSpec
    forward: str = "--"
    backward: Optional[str] = None
    op: Optional[str] = None
    loc: Optional[SourceLoc] = _loc()

    @property
    def arrow(self) -> str:
        """The arrow in canonical form (spec §4.5)."""
        if self.backward is None:
            return self.forward + ">"
        return "<" + self.backward + ("(%s)" % self.op if self.op is not None else "") + self.forward + ">"

    def canonical(self) -> str:
        """``S arrow T`` (spec §4.5)."""
        return "%s %s %s" % (self.left.canonical(), self.arrow, self.right.canonical())


@dataclass(frozen=True)
class ConstraintDecl:
    """``Constraint * <pattern>`` (spec §4.6)."""

    pattern: Pattern
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Constraint * pattern`` (spec §4.6)."""
        return "Constraint * " + self.pattern.canonical()


def _section(header: str, name: Optional[str], items, trail: str = "", lead: str = "") -> str:
    prefix = "$%s := " % name if name else ""
    lines = [prefix + header + " [[" + lead]
    lines += [_indent(i.canonical()) for i in items]
    lines.append("]]" + trail)
    return "\n".join(lines)


@dataclass(frozen=True)
class PhonologySection:
    """``[$Name :=] Phonology [[ ... ]]`` (spec §4). ``items`` holds :class:`FeatureDecl`,
    :class:`ImplicationDecl` and :class:`ConstraintDecl` in order."""

    name: Optional[str]
    items: Tuple[Any, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """The section over several lines (spec §4)."""
        return _section("Phonology", self.name, self.items)


# --------------------------------------------------------------------------------------------
# Orthography
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PhonesLine:
    """``[g1 g2 ...] {F}`` (spec §5.6)."""

    graphemes: Tuple[str, ...]
    spec: RawSpec
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``[g1 g2] {F}`` (spec §5.6)."""
        return "[%s] %s" % (" ".join(self.graphemes), self.spec.canonical())


@dataclass(frozen=True)
class DiacriticLine:
    """``{F} ==> [pre#post]`` (spec §5.6)."""

    spec: RawSpec
    template: str
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``{F} ==> [pre#post]`` (spec §5.6)."""
        return "%s ==> [%s]" % (self.spec.canonical(), self.template)


@dataclass(frozen=True)
class IgnoreLine:
    """``*{F1 F2}`` — features ignored when rendering (spec §5.6)."""

    spec: RawSpec
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``*{F1 F2}`` (spec §5.6)."""
        return "*" + self.spec.canonical()


@dataclass(frozen=True)
class SettingLine:
    """``Name == [text]`` (spec §5.6); ``[]`` gives ``text=''`` (disabled)."""

    name: str
    text: str
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Name == [text]`` (spec §5.6)."""
        return "%s == [%s]" % (self.name, self.text)


@dataclass(frozen=True)
class SyllableMarkLine:
    """``SyllableMark {F} == [text]`` (spec §5.6)."""

    spec: RawSpec
    text: str
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``SyllableMark {F} == [text]`` (spec §5.6)."""
        return "SyllableMark %s == [%s]" % (self.spec.canonical(), self.text)


@dataclass(frozen=True)
class OrthographySection:
    """``[$Name :=] Orthography [[ ... ]]`` (spec §5.6)."""

    name: Optional[str]
    items: Tuple[Any, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """The section over several lines (spec §5.6)."""
        return _section("Orthography", self.name, self.items)


# --------------------------------------------------------------------------------------------
# Syllabification, paradigms, Rules sections, scripts
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SylSetting:
    """One line of a Syllabification section (spec §5.7). ``value`` is a pattern for
    ``Onset``/``Nucleus``/``Coda``, a tuple of canon strings for ``Canons`` and a word
    otherwise."""

    key: str
    value: Any
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``Key value`` (spec §5.7)."""
        v = self.value
        if isinstance(v, Pattern):
            return "%s %s" % (self.key, v.canonical())
        if isinstance(v, tuple):
            return "%s %s" % (self.key, " > ".join(v))
        return "%s %s" % (self.key, v)


@dataclass(frozen=True)
class SyllabificationSection:
    """``[$Name :=] Syllabification [[ ... ]]`` (spec §5.7)."""

    name: Optional[str]
    settings: Tuple[SylSetting, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """The section over several lines (spec §5.7)."""
        return _section("Syllabification", self.name, self.settings)


@dataclass(frozen=True)
class TplItem:
    """A paradigm template item (spec §9): ``kind`` is ``stem`` (``$_``), ``ortho``
    (``value`` = text), ``boundary`` (``value`` = symbol), ``open`` (``value`` = label) or
    ``close``."""

    kind: str
    value: Any = None
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """``$_``, ``[is]``, ``-``, ``<N:``, ``>`` (spec §9)."""
        return {"stem": "$_", "close": ">"}.get(self.kind) or (
            "[%s]" % self.value if self.kind == "ortho" else "<%s:" % self.value if self.kind == "open" else self.value)


@dataclass(frozen=True)
class Cell:
    """``Label : template [/:L± ...] [/:D± ...]`` (spec §9)."""

    label: str
    items: Tuple[TplItem, ...]
    mods: Tuple[Modifier, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "mods", sort_mods(self.mods))

    def canonical(self) -> str:
        """``Label : items mods`` (spec §9)."""
        return "%s : %s%s" % (self.label, " ".join(i.canonical() for i in self.items), mods_text(self.mods))


@dataclass(frozen=True)
class ParadigmSection:
    """``[$Name :=] Paradigm [[ ... ]]`` (spec §9)."""

    name: Optional[str]
    cells: Tuple[Cell, ...]
    loc: Optional[SourceLoc] = _loc()

    def canonical(self) -> str:
        """The section over several lines (spec §9)."""
        return _section("Paradigm", self.name, self.cells)


@dataclass(frozen=True)
class RulesSection:
    """``[$Name :=] Rules [[ mods ... ]] mods`` (spec §8). ``body`` holds rule items,
    commands, assignments and macro definitions."""

    name: Optional[str]
    lead: Tuple[Modifier, ...]
    body: Tuple[Any, ...]
    trail: Tuple[Modifier, ...] = ()
    loc: Optional[SourceLoc] = _loc()

    def __post_init__(self):
        object.__setattr__(self, "lead", sort_mods(self.lead))
        object.__setattr__(self, "trail", sort_mods(self.trail))
        object.__setattr__(self, "body", tuple(self.body))

    def canonical(self) -> str:
        """The section over several lines (spec §8)."""
        return _section("Rules", self.name, self.body, mods_text(self.trail), mods_text(self.lead))


@dataclass(frozen=True)
class Script:
    """A parsed script: top-level statements in file order (spec §2)."""

    stmts: Tuple[Any, ...]
    file: str = field(default="<string>", compare=False)

    def canonical(self) -> str:
        """The whole script in canonical form (spec §11.3)."""
        return "\n".join(s.canonical() for s in self.stmts) + ("\n" if self.stmts else "")
