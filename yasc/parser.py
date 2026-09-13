"""Hand-written recursive-descent parser: logical lines -> :mod:`yasc.syntax` AST
(spec §3–§10; design §7.2).

Use :func:`parse` (or :class:`Parser`). Errors are collected line by line: after an error the
parser skips to the next logical line, and when the bad line opened a ``[[`` block it skips
to the matching ``]]`` (a depth counter keeps block structure intact). All errors are raised
together as one :class:`~yasc.errors.YascLoadError`.
"""

import difflib
import re
from typing import Any, List, Optional, Tuple

from .errors import YascError, YascLoadError, YascSyntaxError
from .lexer import Lexer, LogicalLine, Token, split_lines
from .marks import mark_from_symbol
from .pattern import (
    CLOSE, OPEN, Alt, Anything, AutoFloat, BackRef, Boundary, BracketAssert, Linked, Locus, Macro,
    Nothing, Opt, Pattern, Plus, Seq, Star, walk,
)
from .syntax import (
    PATTERN_MODS, SECTION_KEYWORDS, AliasLine, Arg, Assign, AssignExpr, BasicRule, Cell, Combine, Command,
    ConstraintDecl, DiacriticLine, FeatureDecl, FeatureTypeSyntax, Group, IgnoreLine, ImplicationDecl, Invoke,
    LinkOp, MacroDef, Merge, Modifier, OpLine, OrthographySection, ParadigmSection, PhonesLine, PhonologySection,
    RawOrtho, RawSpec, Rhs, RhsItem, RulesSection, Script, SettingLine, SylSetting, SyllabificationSection,
    SyllableMarkLine, TierSyntax, TplItem,
)

__all__ = ["Parser", "parse", "COMMANDS", "ORTHO_SETTINGS", "SYL_KEYS", "KEYWORDS"]

#: Commands of spec §10.5 (and §5.5 tier helpers).
COMMANDS = ("include", "print", "date", "set", "use", "orthography", "syllabify", "dialects", "paradigm",
            "associate", "ocp", "only", "skip", "assert")

#: ``Name == [x]`` settings of spec §5.6.
ORTHO_SETTINGS = ("PhoneSeparator", "SyllableSeparator", "MorphemeSeparator", "CliticSeparator", "WordSeparator",
                  "PhraseSeparator", "BracketOpen", "BracketClose", "BracketLabelEnd", "FloatingPrefix", "Escape")

#: Syllabification keys (spec §5.7) and their allowed words (``None``: a pattern or canons).
SYL_KEYS = {
    "Onset": None, "Nucleus": None, "Coda": None, "Canons": None,
    "OnsetRequired": ("yes", "no"), "Algorithm": ("MaxOnset", "Canon"),
    "NucleusPreference": ("first", "last"), "Persistent": ("yes", "no"), "Domain": ("word", "phrase"),
    "AllowUnsyllabified": ("yes", "no"),
}

#: Names that cannot be macros (spec §7).
KEYWORDS = SECTION_KEYWORDS + ("Constraint", "Binary", "Unary", "Scalar", "Node", "Scope", "Tier",
                               "SyllableMark")

_RE_ASSIGN = re.compile(r"\$(_|[A-Za-z][A-Za-z0-9_]*)\s*(::=|:=)")
_RE_SECTION = re.compile(r"(%s)(?![A-Za-z0-9_])" % "|".join(SECTION_KEYWORDS))
_RE_MACRO = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*===")
_RE_GROUP = re.compile(r"(&&|\|\|)?\[\[")
_RE_INVOKE = re.compile(r"\$([A-Za-z][A-Za-z0-9_]*)\s*\Z")
_RE_CLOSE = re.compile(r"\]\]")
_RE_BLOCK_OPEN = re.compile(r"\[\[")
_RE_OPLINE = re.compile(r"\(\s*([^\s()]+)\s*\)")
_RE_LABEL = re.compile(r"([A-Za-z][A-Za-z0-9_.]*)\s*:(?!=)")
_RE_KV = re.compile(r"([A-Za-z]+)\s*=\s*(\S+)")
_RE_CANON = re.compile(r"[A-Za-z]+\Z")

_GROUP_KIND = {None: "seq", "&&": "and", "||": "or"}
_STOP = ("ARROW", "MOD", "EOL", "BAR", "RALT", "RPAREN")


def _suggest(word: str, options) -> Optional[str]:
    close = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return ("did you mean %r?" % close[0]) if close else None


class Parser:
    """Parses one script text into a :class:`~yasc.syntax.Script` (spec §2; design §7.2).

    ``errors`` collects every :class:`YascSyntaxError`; :meth:`parse` raises them together as
    a :class:`YascLoadError` at the end.
    """

    def __init__(self, text: str, file: str = "<string>") -> None:
        self.file = file
        self.lines: List[LogicalLine] = split_lines(text, file)
        self.i = 0
        self.errors: List[YascError] = []

    # -- driver ----------------------------------------------------------------------------

    def parse(self) -> Script:
        """Parse the whole text; raise :class:`YascLoadError` if any line failed (spec §12)."""
        stmts, _ = self._body("top", None)
        phys = self.lines[0].phys if self.lines else []
        for err in self.errors:
            if err.loc is not None and err.source_line is None and 0 < err.loc.line <= len(phys):
                err.source_line = phys[err.loc.line - 1]
        if self.errors:
            raise YascLoadError(self.errors)
        return Script(tuple(stmts), self.file)

    def _body(self, kind: str, opener: Optional[LogicalLine]) -> Tuple[List[Any], Optional[LogicalLine]]:
        """Parse lines until the ``]]`` closing this block (or EOF for ``top``)."""
        items: List[Any] = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            self.i += 1
            if line.closes:
                if kind == "top":
                    self.errors.append(line.error("']]' without a matching '[['", 0, 2))
                    continue
                return items, line
            try:
                item = self._line(kind, line, items)
            except YascSyntaxError as err:
                self.errors.append(err)
                if line.opens:
                    self._skip_block()
                continue
            if item is not None:
                items.append(item)
        if opener is not None:
            k = opener.text.find("[[")
            self.errors.append(opener.error("'[[' is never closed", max(k, 0), max(k, 0) + 2,
                                            hint="close the block with ]] on a line of its own"))
        return items, None

    def _skip_block(self) -> None:
        """Skip to the ``]]`` matching a block opened by a line that failed (design §7.2)."""
        depth = 1
        while self.i < len(self.lines):
            line = self.lines[self.i]
            self.i += 1
            if line.closes:
                depth -= 1
                if depth == 0:
                    return
            if line.opens:
                depth += 1

    def _try(self, fn, default):
        """Run ``fn``; on a syntax error record it and return ``default``."""
        try:
            return fn()
        except YascSyntaxError as err:
            self.errors.append(err)
            return default

    # -- line dispatch ---------------------------------------------------------------------

    def _line(self, kind: str, line: LogicalLine, items: List[Any]):
        if kind == "phonology":
            return self._phon_line(line, items)
        if kind == "orthography":
            return self._ortho_line(line)
        if kind == "syllabification":
            return self._syl_line(line, items)
        if kind == "paradigm":
            return self._cell_line(line)
        return self._stmt_line(line, top=(kind == "top"))

    def _stmt_line(self, line: LogicalLine, top: bool):
        text = line.text
        if text.startswith("!"):
            return self._command(line)
        m = _RE_ASSIGN.match(text)
        if m:
            rest = text[m.end():].lstrip()
            if _RE_SECTION.match(rest):
                if not top:
                    raise line.error("sections cannot be defined inside Rules", 0, m.end(),
                                     hint="define the section at top level and invoke it with $%s" % m.group(1))
                return self._section(line)
            return self._assign(line)
        if _RE_SECTION.match(text):
            if not top:
                raise line.error("sections cannot be defined inside Rules", 0, len(text.split()[0]))
            return self._section(line)
        m = _RE_MACRO.match(text)
        if m:
            return self._macro(line, m)
        m = _RE_GROUP.match(text)
        if m:
            return self._group(line, m)
        m = _RE_INVOKE.match(text)
        if m:
            return Invoke(m.group(1), line.loc(0, m.end(1)))
        return self._rule(line)

    # -- sections --------------------------------------------------------------------------

    def _section(self, line: LogicalLine):
        lex = Lexer(line)
        name = None
        m = lex.match(_RE_ASSIGN)
        if m:
            name = m.value.group(1)
            if name == "_":
                raise line.error("a section cannot be assigned to $_", 0, 2)
        kw = lex.next("top")
        if kw.kind != "IDENT" or kw.value not in SECTION_KEYWORDS:
            raise lex.error("expected a section keyword", kw.start, kw.end)
        if lex.match(_RE_BLOCK_OPEN) is None:
            raise lex.error("expected '[[' after %s" % kw.value, hint="write %s [[ on this line" % kw.value)
        keyword = "Phonology" if kw.value == "Phonetics" else kw.value
        loc = line.loc(0, kw.end)
        if keyword == "Rules":
            lead = self._mods(lex)
            body, close = self._body("rules", line)
            trail = self._trail(close)
            return RulesSection(name, tuple(lead), tuple(body), tuple(trail), loc)
        if not lex.at_end():
            raise lex.error("unexpected text after '[[' in a %s section" % keyword, lex.pos, len(line.text),
                            hint="put each declaration on its own line")
        body, close = self._body(keyword.lower(), line)
        if close is not None:
            self._try(lambda: self._plain_close(close), None)
        if keyword == "Phonology":
            return PhonologySection(name, tuple(body), loc)
        if keyword == "Orthography":
            return OrthographySection(name, tuple(body), loc)
        if keyword == "Syllabification":
            return SyllabificationSection(name, tuple(body), loc)
        return ParadigmSection(name, tuple(body), loc)

    def _plain_close(self, close: LogicalLine) -> None:
        if close.text.strip() != "]]":
            raise close.error("only Rules sections and groups take modifiers after ']]'", 2, len(close.text))

    def _trail(self, close: Optional[LogicalLine]) -> List[Modifier]:
        if close is None:
            return []
        lex = Lexer(close)
        lex.match(_RE_CLOSE)
        return self._try(lambda: self._mods(lex), [])

    # -- phonology -------------------------------------------------------------------------

    def _phon_line(self, line: LogicalLine, items: List[Any]):
        lex = Lexer(line)
        first = lex.peek("phon")
        if line.opens:
            raise line.error("blocks cannot be nested inside a Phonology section", line.text.find("[["))
        if first.kind == "ALIAS":
            lex.next("phon")
            names = []
            while True:
                t = lex.next("phon")
                if t.kind == "EOL":
                    break
                if t.kind != "IDENT":
                    raise lex.error("expected an alias name", t.start, t.end)
                names.append(t.value)
            if not names:
                raise line.error("'==' must be followed by at least one alias", 0, 2)
            return self._attach(items, line, aliases=AliasLine(tuple(names), line.loc(0, len(line.text))))
        if first.kind == "LPAREN":
            m = lex.match(_RE_OPLINE)
            if m is None:
                raise line.error("expected an operation name in parentheses, e.g. (-) or (<Max>)", 0, 1)
            results = []
            while True:
                t = lex.next("phon")
                if t.kind == "EOL":
                    break
                if t.kind != "VALUE":
                    raise lex.error("expected an operation result (a value or _)", t.start, t.end)
                self._no_ellipsis(lex, t)
                results.append(self._value_text(t))
            return self._attach(items, line, ops=OpLine(m.value.group(1), tuple(results), line.loc(0, len(line.text))))
        if first.kind in ("SPEC", "SSPEC"):
            left = self._raw_spec(lex.next("phon"))
            arrow = lex.next("phon")
            if arrow.kind != "IARROW":
                raise lex.error("expected an implication arrow (-->, ~~>, <-->, <--(op)-->, ...)", arrow.start,
                                arrow.end)
            rt = lex.next("phon")
            if rt.kind not in ("SPEC", "SSPEC"):
                raise lex.error("expected a segment spec {...} after the arrow", rt.start, rt.end)
            end = lex.next("phon")
            if end.kind != "EOL":
                raise lex.error("unexpected text after the implication", end.start)
            m = arrow.value
            if m.group(0) in ("-->", "~~>"):
                fwd, bwd, op = m.group(0)[:2], None, None
            else:
                bwd, op, fwd = m.group(1), m.group(2), m.group(3) or m.group(1)
            return ImplicationDecl(left, self._raw_spec(rt), fwd, bwd, op, line.loc(0, len(line.text)))
        if first.kind == "IDENT" and first.value == "Constraint":
            lex.next("phon")
            star = lex.next("phon")
            if star.text != "*":
                raise lex.error("expected '*' after Constraint", star.start, star.end,
                                hint="write Constraint * <pattern> (spec §4.6)")
            pat = self._pattern(lex)
            self._expect_eol(lex, "pattern")
            return ConstraintDecl(pat, line.loc(0, len(line.text)))
        if first.kind == "IDENT":
            return self._feature(lex, line)
        raise lex.error("expected a feature declaration, '== aliases', '(op) results', an implication or "
                        "Constraint *", first.start, first.end)

    def _no_ellipsis(self, lex: Lexer, t: Token) -> None:
        if t.text == "...":
            raise lex.error("'...' is not allowed in value or operation lists", t.start, t.end,
                            hint="list every value and result in full (spec §4.1, A9)")

    @staticmethod
    def _value_text(t: Token) -> str:
        return "[%s]" % t.value if t.text.startswith("[") else t.value

    def _attach(self, items: List[Any], line: LogicalLine, aliases=None, ops=None):
        for k in range(len(items) - 1, -1, -1):
            if isinstance(items[k], FeatureDecl):
                d = items[k]
                if aliases is not None:
                    items[k] = FeatureDecl(d.name, d.type, d.scope, d.tier, d.aliases + (aliases,), d.ops, d.loc)
                else:
                    items[k] = FeatureDecl(d.name, d.type, d.scope, d.tier, d.aliases, d.ops + (ops,), d.loc)
                return None
        raise line.error("%s line with no feature declared before it" % ("'=='" if aliases else "operation"), 0,
                         len(line.text), hint="alias and operation lines follow a feature declaration (spec §4.1)")

    def _feature(self, lex: Lexer, line: LogicalLine) -> FeatureDecl:
        name_tok = lex.next("phon")
        name = name_tok.value
        t = lex.peek("phon")
        ftype = None
        tstart = t.start
        if t.kind == "IDENT" and t.value in ("Binary", "Unary", "Scalar", "Node"):
            lex.next("phon")
            if t.value == "Binary":
                ftype = FeatureTypeSyntax("binary", loc=t.loc)
            elif t.value == "Unary":
                ftype = FeatureTypeSyntax("unary", loc=t.loc)
            elif t.value == "Scalar":
                bounds = None
                if lex.peek("phon").kind == "LPAREN":
                    m = lex.match(re.compile(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)"))
                    if m is None:
                        raise lex.error("expected Scalar(min,max) with two integers", lex.pos)
                    bounds = (int(m.value.group(1)), int(m.value.group(2)))
                ftype = FeatureTypeSyntax("scalar", bounds=bounds, loc=line.loc(tstart, lex.pos))
            else:
                if lex.peek("phon").kind != "LPAREN":
                    bad = lex.peek("phon")
                    raise lex.error("expected '(' after Node", bad.start, bad.end,
                                    hint="write Node(Daughter1 Daughter2 ...) (spec §4.3)")
                lex.next("phon")
                kids = []
                while True:
                    k = lex.next("phon")
                    if k.kind == "RPAREN":
                        break
                    if k.kind == "VALUE" and k.text == "...":
                        self._no_ellipsis(lex, k)
                    if k.kind != "IDENT":
                        raise lex.error("expected a daughter feature name or ')'", k.start, k.end)
                    kids.append(k.value)
                ftype = FeatureTypeSyntax("node", children=tuple(kids), loc=line.loc(tstart, lex.pos))
        else:
            values = []
            while True:
                t = lex.peek("phon")
                if t.kind != "VALUE":
                    break
                lex.next("phon")
                self._no_ellipsis(lex, t)
                values.append(self._value_text(t))
            if not values:
                raise lex.error("expected a feature type (Binary, Unary, Scalar, Node(...)) or a value list after %r"
                                % name, t.start, t.end)
            if values == ["!"]:
                ftype = FeatureTypeSyntax("unary", loc=line.loc(tstart, lex.pos))
            else:
                ftype = FeatureTypeSyntax("values", values=tuple(values), loc=line.loc(tstart, lex.pos))
        scope = tier = None
        aliases: Tuple[AliasLine, ...] = ()
        while True:
            t = lex.next("phon")
            if t.kind == "EOL":
                break
            if t.kind == "IDENT" and t.value == "Scope":
                m = lex.match(re.compile(r"\(\s*([A-Za-z]+)\s*\)"))
                if m is None or m.value.group(1) not in ("Syllable", "Segment"):
                    raise lex.error("expected Scope(Syllable) or Scope(Segment)", t.start, lex.pos)
                scope = m.value.group(1)
            elif t.kind == "IDENT" and t.value == "Tier":
                tier = self._tier(lex, t)
            elif t.kind == "ALIAS":
                names = []
                while lex.peek("phon").kind == "IDENT":
                    names.append(lex.next("phon").value)
                if not names:
                    raise lex.error("'==' must be followed by at least one alias", t.start, t.end)
                aliases = (AliasLine(tuple(names), line.loc(t.start, lex.pos)),)
            elif t.kind == "VALUE" and ftype.kind != "values":
                raise lex.error("%s takes no value list" % ftype.canonical().split("(")[0], t.start, t.end,
                                hint="either name a type or list the values, not both (spec §4.1)")
            else:
                raise lex.error("unexpected %r in a feature declaration" % t.text, t.start, t.end,
                                hint="after the type only Scope(...), Tier(...) and == aliases may follow")
        return FeatureDecl(name, ftype, scope, tier, aliases, (), line.loc(name_tok.start, name_tok.end))

    def _tier(self, lex: Lexer, kw: Token) -> TierSyntax:
        if lex.next("phon").kind != "LPAREN":
            raise lex.error("expected '(' after Tier", kw.start, kw.end)
        tbu = stray = ocp = None
        while True:
            t = lex.next("phon")
            if t.kind == "RPAREN":
                break
            if t.kind == "COMMA":
                continue
            if t.kind != "IDENT" or t.value not in ("TBU", "Stray", "OCP"):
                raise lex.error("expected TBU=, Stray= or OCP= inside Tier(...)", t.start, t.end, hint="spec §5.5")
            if lex.next("phon").kind != "EQUALS":
                raise lex.error("expected '=' after %s" % t.value, t.start, t.end)
            v = lex.next("phon")
            if t.value == "TBU":
                if v.kind != "SPEC":
                    raise lex.error("TBU= takes a segment spec, e.g. TBU={+Syll}", v.start, v.end)
                tbu = self._raw_spec(v)
            else:
                allowed = ("float", "delete") if t.value == "Stray" else ("off", "merge", "delete")
                if v.kind != "IDENT" or v.value not in allowed:
                    raise lex.error("%s must be one of %s" % (t.value, ", ".join(allowed)), v.start, v.end)
                if t.value == "Stray":
                    stray = v.value
                else:
                    ocp = v.value
        return TierSyntax(tbu, stray, ocp, lex.line.loc(kw.start, lex.pos))

    def _raw_spec(self, t: Token, strict: bool = False, allow_ellipsis: bool = False) -> RawSpec:
        cons = t.value
        if not allow_ellipsis:
            for c in cons:
                if c.kind == "ellipsis":
                    raise YascSyntaxError("'...' inside {} is only allowed in orthography grapheme lines", c.loc,
                                          hint="remove it (spec §5.6)", source_line=None)
        return RawSpec(tuple(c for c in cons if c.kind != "ellipsis"), strict or t.kind == "SSPEC", t.loc)

    # -- orthography -----------------------------------------------------------------------

    def _ortho_line(self, line: LogicalLine):
        lex = Lexer(line)
        if line.opens:
            raise line.error("blocks cannot be nested inside an Orthography section", line.text.find("[["))
        t = lex.next("ortho")
        loc = line.loc(0, len(line.text))
        if t.kind == "ORTHO":
            graphemes = tuple(t.value.split())
            if not graphemes:
                raise lex.error("an empty grapheme list", t.start, t.end)
            s = self._expect(lex, "ortho", "SPEC", "a segment spec {...} after the graphemes")
            self._expect_eol(lex, "ortho")
            return PhonesLine(graphemes, self._raw_spec(s, allow_ellipsis=True), loc)
        if t.kind == "SPEC":
            self._expect(lex, "ortho", "DARROW", "'==>' after the spec of a diacritic")
            tpl = self._expect(lex, "ortho", "ORTHO", "a template [pre#post] after '==>'")
            self._expect_eol(lex, "ortho")
            if tpl.value.count("#") != 1:
                raise lex.error("a diacritic template must contain exactly one '#' (the base)", tpl.start, tpl.end,
                                hint="for example [#_h], [~#] or [(#)] (spec §5.6)")
            return DiacriticLine(self._raw_spec(t), tpl.value, loc)
        if t.kind == "STAR":
            s = self._expect(lex, "ortho", "SPEC", "a spec {F1 F2} after '*'")
            self._expect_eol(lex, "ortho")
            return IgnoreLine(self._raw_spec(s), loc)
        if t.kind == "IDENT" and t.value == "SyllableMark":
            s = self._expect(lex, "ortho", "SPEC", "a spec after SyllableMark")
            self._expect(lex, "ortho", "ALIAS", "'==' in SyllableMark {F} == [text]")
            txt = self._expect(lex, "ortho", "ORTHO", "[text] after '=='")
            self._expect_eol(lex, "ortho")
            return SyllableMarkLine(self._raw_spec(s), txt.value, loc)
        if t.kind == "IDENT":
            if t.value not in ORTHO_SETTINGS:
                raise lex.error("unknown orthography setting %r" % t.value, t.start, t.end,
                                hint=_suggest(t.value, ORTHO_SETTINGS) or "settings: " + ", ".join(ORTHO_SETTINGS))
            self._expect(lex, "ortho", "ALIAS", "'==' after %s" % t.value)
            txt = self._expect(lex, "ortho", "ORTHO", "[text] after '=='")
            self._expect_eol(lex, "ortho")
            return SettingLine(t.value, txt.value.strip(), loc)
        raise lex.error("expected [graphemes] {F}, {F} ==> [pre#post], *{F}, a setting or SyllableMark", t.start,
                        t.end)

    # -- syllabification -------------------------------------------------------------------

    def _syl_line(self, line: LogicalLine, items: List[Any]):
        lex = Lexer(line)
        t = lex.next("top")
        if t.kind != "IDENT" or t.value not in SYL_KEYS:
            raise lex.error("unknown syllabification setting %r" % t.text, t.start, t.end,
                            hint=_suggest(t.text, SYL_KEYS) or "settings: " + ", ".join(SYL_KEYS))
        for it in items:
            if it.key == t.value:
                raise lex.error("%s is given twice" % t.value, t.start, t.end)
        loc = line.loc(0, len(line.text))
        allowed = SYL_KEYS[t.value]
        if t.value in ("Onset", "Nucleus", "Coda"):
            pat = self._pattern(lex)
            self._expect_eol(lex, "pattern")
            return SylSetting(t.value, pat, loc)
        if t.value == "Canons":
            parts = [p.strip() for p in lex.rest().split(">")]
            if not parts or any(not _RE_CANON.match(p) for p in parts):
                raise lex.error("Canons expects canon shapes separated by '>', e.g. CV > CVC > V", lex.pos,
                                len(line.text))
            return SylSetting("Canons", tuple(parts), loc)
        v = lex.next("top")
        if v.kind != "IDENT" or v.value not in allowed:
            raise lex.error("%s must be one of: %s" % (t.value, ", ".join(allowed)), v.start, v.end)
        self._expect_eol(lex, "top")
        return SylSetting(t.value, v.value, loc)

    # -- paradigms -------------------------------------------------------------------------

    def _cell_line(self, line: LogicalLine) -> Cell:
        lex = Lexer(line)
        m = lex.match(_RE_LABEL)
        if m is None:
            raise line.error("expected a paradigm cell 'Label : template'", 0, len(line.text),
                             hint="for example Gen.Sg : $_ - [is] (spec §9)")
        items: List[TplItem] = []
        depth = 0
        while True:
            t = lex.peek("template")
            if t.kind in ("MOD", "EOL"):
                break
            lex.next("template")
            if t.kind == "STEM":
                items.append(TplItem("stem", None, t.loc))
            elif t.kind == "ORTHO":
                items.append(TplItem("ortho", t.value, t.loc))
            elif t.kind == "BOUNDARY":
                items.append(TplItem("boundary", t.value, t.loc))
            elif t.kind == "TOPEN":
                depth += 1
                items.append(TplItem("open", t.value, t.loc))
            elif t.kind == "TCLOSE":
                if depth == 0:
                    raise lex.error("'>' without a matching '<Label:'", t.start, t.end)
                depth -= 1
                items.append(TplItem("close", None, t.loc))
            else:
                raise lex.error("unexpected %r in a paradigm template" % t.text, t.start, t.end,
                                hint="a template holds $_, [text], boundaries and <Label: ... > (spec §9)")
        if depth:
            raise line.error("unclosed '<Label:' bracket in the template", m.end, len(line.text))
        if not items:
            raise line.error("empty paradigm template", m.end, len(line.text))
        mods = self._mods(lex)
        for mod in mods:
            if mod.key not in ("/:L+", "/:L-", "/:D+", "/:D-"):
                raise YascSyntaxError("only /:L± and /:D± restrictions are allowed on a paradigm cell", mod.loc,
                                      source_line=line.source_line(0))
        return Cell(m.value.group(1), tuple(items), tuple(mods), line.loc(0, m.value.end(1)))

    # -- statements ------------------------------------------------------------------------

    def _assign(self, line: LogicalLine) -> Assign:
        lex = Lexer(line)
        m = lex.match(_RE_ASSIGN)
        name = m.value.group(1)
        t = lex.next("top")
        if t.kind == "VAR":
            vname, idx = t.value
            if idx is not None:
                if vname != "field":
                    raise lex.error("only $field takes an index", t.start, t.end, hint="write $field[n] (spec §10.1)")
                expr = AssignExpr("field", idx, t.loc)
            else:
                expr = AssignExpr("var", vname, t.loc)
        elif t.kind == "ORTHO":
            expr = AssignExpr("ortho", t.value, t.loc)
        else:
            raise lex.error("expected $_, $name, $field[n] or [text] after ':='", t.start, t.end,
                            hint="spec §10.1")
        self._expect_eol(lex, "top")
        return Assign(name, expr, line.loc(0, m.value.end(1)))

    def _macro(self, line: LogicalLine, m) -> MacroDef:
        name = m.group(1)
        if name in KEYWORDS:
            raise line.error("%r is a keyword and cannot name a macro" % name, 0, len(name))
        lex = Lexer(line)
        lex.pos = m.end()
        body = self._pattern(lex)
        if isinstance(body, Nothing) and body.loc is None:
            raise line.error("macro %s has an empty body" % name, m.end(), len(line.text),
                             hint="write 0 for an empty macro")
        self._expect_eol(lex, "pattern")
        return MacroDef(name, body, line.loc(0, len(name)))

    def _group(self, line: LogicalLine, m) -> Group:
        lex = Lexer(line)
        lex.pos = m.end()
        lead = self._mods(lex)
        body, close = self._body("rules", line)
        trail = self._trail(close)
        return Group(_GROUP_KIND[m.group(1)], tuple(lead), tuple(body), tuple(trail), line.loc(0, m.end()))

    def _command(self, line: LogicalLine) -> Command:
        lex = Lexer(line)
        t = lex.next("top")
        if t.kind != "COMMAND":
            raise lex.error("expected a command name after '!'", 0, 1)
        name = t.value
        if name not in COMMANDS:
            raise lex.error("unknown command !%s" % name, t.start, t.end,
                            hint=_suggest(name, COMMANDS) or "commands: " + " ".join("!" + c for c in COMMANDS))
        args: List[Arg] = []
        loc = line.loc(t.start, t.end)

        def tok(kinds, what):
            x = lex.next("top")
            if x.kind not in kinds:
                raise lex.error("!%s expects %s" % (name, what), x.start, x.end if x.end > x.start else None)
            return x

        if name in ("include", "assert"):
            s = tok(("STRING",), "a quoted string")
            args.append(Arg("str", s.value, s.loc))
        elif name == "print":
            s = tok(("STRING",), "a quoted format string")
            args.append(Arg("str", s.value, s.loc))
            while lex.peek("top").kind != "EOL":
                v = tok(("VAR",), "variables after the format ($_, $name, $field[n])")
                args.append(Arg("var", v.value, v.loc))
        elif name == "date":
            n = tok(("NUM",), "an integer date")
            args.append(Arg("int", n.value, n.loc))
        elif name == "set":
            k = tok(("IDENT",), "a setting name: !set Name = value")
            tok(("EQUALS",), "'=' : !set Name = value")
            value = lex.rest()
            if not value:
                raise lex.error("!set %s needs a value" % k.value, lex.pos)
            args += [Arg("word", k.value, k.loc), Arg("word", value, line.loc(lex.pos, len(line.text)))]
            lex.pos = len(line.text)
        elif name in ("use", "paradigm"):
            v = tok(("VAR",), "a definition variable such as $X")
            args.append(Arg("var", v.value, v.loc))
        elif name == "syllabify":
            if lex.peek("top").kind != "EOL":
                v = tok(("VAR",), "an optional syllabification variable $S")
                args.append(Arg("var", v.value, v.loc))
        elif name == "orthography":
            seen = set()
            while lex.peek("top").kind != "EOL":
                w = tok(("IDENT",), "'input $A' and/or 'output $B'")
                if w.value not in ("input", "output") or w.value in seen:
                    raise lex.error("!orthography expects 'input $A' and/or 'output $B'", w.start, w.end)
                seen.add(w.value)
                v = tok(("VAR",), "an orthography variable after %s" % w.value)
                args += [Arg("word", w.value, w.loc), Arg("var", v.value, v.loc)]
            if not args:
                raise lex.error("!orthography expects 'input $A' and/or 'output $B'", t.start, t.end)
        elif name == "dialects":
            names = []
            paren = lex.peek("top").kind == "LPAREN"
            if paren:
                lex.next("top")
            while lex.peek("top").kind not in ("EOL", "RPAREN"):
                names.append(tok(("IDENT",), "dialect names").value)
            if paren:
                tok(("RPAREN",), "')' after the dialect names")
            if not names:
                raise lex.error("!dialects needs at least one name, e.g. !dialects (A B C)", t.start, t.end)
            args.append(Arg("list", tuple(names), line.loc(t.end, lex.pos)))
        elif name in ("associate", "ocp"):
            tier = tok(("IDENT",), "a tier feature name")
            args.append(Arg("word", tier.value, tier.loc))
            rest = lex.rest()
            lex.pos = len(line.text)
            if name == "ocp":
                if rest:
                    if rest not in ("merge", "delete"):
                        raise line.error("!ocp takes merge or delete", len(line.text) - len(rest), len(line.text))
                    args.append(Arg("word", rest))
            else:
                allowed = {"dir": (">", "<"), "mode": ("one-to-one",), "spread": ("last", "none")}
                pos = 0
                while rest[pos:].strip():
                    mm = _RE_KV.match(rest, len(rest[:pos]) + (len(rest[pos:]) - len(rest[pos:].lstrip())))
                    if not mm or mm.group(1) not in allowed or mm.group(2) not in allowed[mm.group(1)]:
                        raise line.error("!associate options are dir=>|<, mode=one-to-one, spread=last|none",
                                         len(line.text) - len(rest), len(line.text), hint="spec §5.5")
                    args.append(Arg("kv", (mm.group(1), mm.group(2))))
                    pos = mm.end()
        elif name in ("only", "skip"):
            while lex.peek("top").kind != "EOL":
                w = tok(("IDENT", "WORD"), "rule names")
                args.append(Arg("word", w.value, w.loc))
            if not args:
                raise lex.error("!%s needs at least one rule name" % name, t.start, t.end)
        self._expect_eol(lex, "top")
        return Command(name, tuple(args), loc)

    # -- rules -----------------------------------------------------------------------------

    def _rule(self, line: LogicalLine) -> BasicRule:
        lex = Lexer(line)
        if "-->" not in line.text:
            first = lex.peek("pattern")
            raise line.error("expected a rule 'LHS --> RHS', a command, an assignment or a definition", first.start,
                             max(first.end, first.start + 1),
                             hint="a rule needs '-->' (spec §8.1); a comment is %% or a line starting with '# '")
        lhs = self._pattern(lex)
        arrow = lex.next("pattern")
        if arrow.kind != "ARROW":
            raise lex.error("unexpected %r in the left-hand side; expected '-->'" % arrow.text, arrow.start,
                            arrow.end)
        if isinstance(lhs, Nothing) and lhs.loc is None:
            raise lex.error("empty left-hand side", arrow.start, arrow.end, hint="write 0 for epenthesis (spec §8.1)")
        rhs = self._rhs(lex, arrow)
        mods = self._mods(lex)
        return BasicRule(lhs, rhs, tuple(mods), line.loc(0, arrow.end))

    def _rhs(self, lex: Lexer, arrow: Token) -> Rhs:
        # On the right-hand side '+' is the merge prefix (+[x]), never the postfix Plus,
        # which the RHS does not allow anyway (spec §8.2.4).
        self._rhs_mode = True
        try:
            return self._rhs_items(lex, arrow)
        finally:
            self._rhs_mode = False

    def _rhs_items(self, lex: Lexer, arrow: Token) -> Rhs:
        items: List[RhsItem] = []
        zero = None
        while True:
            t = lex.peek("pattern")
            if t.kind in ("MOD", "EOL"):
                break
            if t.kind == "NOTHING":
                lex.next("pattern")
                zero = t
                continue
            weak = False
            if t.kind == "TILDE":
                lex.next("pattern")
                weak = True
            plus = lex.peek("pattern")
            merge = plus.kind == "PLUS"
            if merge:  # +[x]: merge instead of replace (spec §8.2.4)
                lex.next("pattern")
                if weak:
                    raise lex.error("'~' and '+' cannot be combined", t.start, plus.end,
                                    hint="+[x] already keeps the segment's other features (spec §8.2.4)")
                if lex.peek("pattern").start != plus.end:
                    raise lex.error("'+' (merge) must be followed directly by [x], a macro or << | >>",
                                    plus.start, plus.end, hint="spec §8.2.4")
            node = self._item(lex, rhs=True)
            if merge:
                if not (isinstance(node, (RawOrtho, Alt)) or (isinstance(node, Macro) and node.refine is None)) \
                        or (isinstance(node, RawOrtho) and node.strict):
                    raise YascSyntaxError("'+' (merge) applies only to [x], a macro or << | >>, not to %s"
                                          % node.canonical(), plus.loc,
                                          hint="a spec {...} already modifies the segment (spec §8.2.4)",
                                          source_line=lex.line.source_line(plus.start))
                node = Merge(node, plus.loc)
            self._check_rhs_node(lex, node, weak, t)
            items.append(RhsItem(node, weak, t.loc))
        if zero is not None and items:
            raise lex.error("0 (deletion) must be the whole right-hand side", zero.start, zero.end)
        if zero is None and not items:
            raise lex.error("empty right-hand side", arrow.start, arrow.end, hint="write 0 for deletion (spec §8.1)")
        return Rhs(tuple(items), arrow.loc)

    def _check_rhs_node(self, lex: Lexer, node: Pattern, weak: bool, t: Token) -> None:
        ok = (RawSpec, RawOrtho, Merge, Combine, Macro, BackRef, Alt, Linked, AutoFloat, LinkOp)
        # Plan P8: a bare (a) is a tier value variable in a /:T rule (spec §6.5); the compiler
        # rejects it in other rules.
        tier_var = isinstance(node, Opt) and isinstance(node.pattern, Macro) and node.pattern.refine is None
        if not isinstance(node, ok) and not tier_var:
            raise YascSyntaxError("%s is not allowed on the right-hand side" % node.canonical(), t.loc,
                                  hint="RHS items are {...}, ~{...}, [x], +[x], '{...}, $n, 0, macros, << | >> "
                                  "and S^... (spec §8.2.4)", source_line=lex.line.source_line(t.start))
        if weak and not isinstance(node, (RawSpec, Macro)):
            raise YascSyntaxError("'~' (weak modification) applies only to a spec {...}", t.loc,
                                  source_line=lex.line.source_line(t.start))

    def _mods(self, lex: Lexer) -> List[Modifier]:
        """Parse modifiers up to the end of the line (spec §8.1–§8.8)."""
        mods: List[Modifier] = []
        while True:
            t = lex.next("pattern")
            if t.kind == "EOL":
                return mods
            if t.kind != "MOD":
                raise lex.error("unexpected %r; expected a modifier (/, /!, /:..., /*, ...)" % t.text, t.start,
                                t.end, hint="a comment after code starts with %% (spec §3.2, A1)"
                                if t.text == "#" else None)
            key, val = t.value
            arg: Any = val
            if key in PATTERN_MODS:
                arg = self._pattern(lex)
                if isinstance(arg, Nothing) and arg.loc is None:
                    raise lex.error("%s needs a pattern" % key, t.start, t.end)
                loci = [p for p in walk(arg) if isinstance(p, Locus)]
                if key in ("/", "/!") and len(loci) != 1:
                    raise lex.error("a context must contain the locus ___ exactly once (found %d)" % len(loci),
                                    t.start, lex.pos, hint="write the focus position as ___, e.g. / V ___ #")
                if len(loci) > 1:
                    raise lex.error("a filter may contain the locus ___ at most once", t.start, lex.pos)
                top = arg.items if isinstance(arg, Seq) else (arg,)
                if loci and not any(isinstance(x, Locus) for x in top):
                    raise lex.error("the locus ___ must be a top-level item, not inside a group or repetition",
                                    t.start, lex.pos)
            elif key in ("/:L+", "/:L-"):
                s = lex.next("pattern")
                if s.kind != "SPEC":
                    raise lex.error("%s needs a lexical feature spec, e.g. %s {!N}" % (key, key), s.start, s.end)
                arg = self._raw_spec(s)
            elif key in ("/:D+", "/:D-", "/:C+", "/:C-"):
                s = lex.next("pattern")
                if s.kind == "IDENT":
                    arg = (s.value,)
                elif s.kind == "LPAREN":
                    names = []
                    while True:
                        x = lex.next("pattern")
                        if x.kind == "RPAREN":
                            break
                        if x.kind != "IDENT":
                            raise lex.error("expected a name or ')'", x.start, x.end)
                        names.append(x.value)
                    if not names:
                        raise lex.error("%s needs at least one name" % key, s.start, x.end)
                    arg = tuple(names)
                else:
                    raise lex.error("%s needs a name or a list (N1 N2)" % key, s.start, s.end)
            elif key == "/:T":
                s = lex.next("pattern")
                if s.kind != "IDENT":
                    raise lex.error("/:T needs a tier feature name, e.g. /:T Tone", s.start, s.end)
                arg = s.value
            mods.append(Modifier(key, arg, t.loc))

    # -- patterns --------------------------------------------------------------------------

    def _pattern(self, lex: Lexer, rhs: bool = False) -> Pattern:
        """A sequence up to a stop token (``-->``, a modifier, ``|``, ``>>``, ``)`` or the end);
        an empty sequence is ``Nothing()`` with no location (spec §6.1)."""
        items: List[Pattern] = []
        start = lex.peek("pattern")
        while lex.peek("pattern").kind not in _STOP:
            items.append(self._item(lex, rhs))
        if not items:
            return Nothing()
        if len(items) == 1:
            return items[0]
        return Seq(tuple(items), start.loc)

    def _item(self, lex: Lexer, rhs: bool = False) -> Pattern:
        t = lex.peek("pattern")
        k = t.kind
        if k == "LPAREN":
            lex.next("pattern")
            inner = self._pattern(lex, rhs)
            close = lex.next("pattern")
            if close.kind != "RPAREN":
                raise lex.error("expected ')'", close.start, close.end if close.end > close.start else None)
            if isinstance(inner, Nothing) and inner.loc is None:
                raise lex.error("empty group ()", t.start, close.end)
            post = lex.peek("pattern")
            if post.kind == "STAR":
                lex.next("pattern")
                return Star(inner, t.loc)
            if post.kind == "PLUS":
                lex.next("pattern")
                return Plus(inner, t.loc)
            if post.kind == "QMARK":
                lex.next("pattern")
            return Opt(inner, t.loc)
        if k == "LALT":
            lex.next("pattern")
            alts = [self._pattern(lex, rhs)]
            while True:
                x = lex.next("pattern")
                if x.kind == "RALT":
                    break
                if x.kind != "BAR":
                    raise lex.error("expected '|' or '>>' in a disjunction", x.start, x.end if x.end > x.start else None)
                alts.append(self._pattern(lex, rhs))
            return self._postfix(lex, Alt(None, tuple(alts), t.loc))
        lex.next("pattern")
        if k == "ANYTHING":
            return Anything(t.loc)
        if k == "BOUNDARY":
            return Boundary(mark_from_symbol(t.value), t.loc)
        if k in ("BRACKET_OPEN", "BRACKET_CLOSE"):
            return BracketAssert(OPEN if k == "BRACKET_OPEN" else CLOSE, t.value, t.loc)
        if k == "NOTHING":
            return Nothing(t.loc)
        if k == "LOCUS":
            return Locus(t.loc)
        if k == "BACKREF":
            return self._postfix(lex, BackRef(t.value, t.loc))
        if k == "TIER":
            return self._tier_elem(lex, t, None, rhs)
        if k in ("SPEC", "ORTHO", "STRICT", "IDENT"):
            leaf = self._leaf(lex, t)
            while lex.peek("pattern").kind == "COLON":
                lex.next("pattern")
                nt = lex.next("pattern")
                if nt.kind not in ("SPEC", "ORTHO", "STRICT", "IDENT"):
                    raise lex.error("expected a spec, [x] or a macro after ':'", nt.start, nt.end)
                right = self._leaf(lex, nt)
                if isinstance(leaf, Macro) and leaf.refine is None:
                    leaf = Macro(leaf.name, right, leaf.loc)
                else:
                    leaf = Combine(leaf, right, leaf.loc)
            nxt = lex.peek("pattern")
            if nxt.kind == "TIER" and nxt.start == lex.pos:
                lex.next("pattern")
                return self._tier_elem(lex, nxt, leaf, rhs)
            if isinstance(leaf, BackRef):
                return self._postfix(lex, leaf)
            return self._postfix(lex, leaf)
        if k == "VAR":
            raise lex.error("variables such as $%s cannot appear in a pattern" % t.value, t.start, t.end,
                            hint="$n (a digit) is a back-reference; $Name alone on a line invokes a rule block")
        what = {"EOL": "end of line", "ARROW": "'-->'"}.get(k, repr(t.text))
        raise lex.error("unexpected %s in a pattern" % what, t.start, t.end if t.end > t.start else None)

    def _leaf(self, lex: Lexer, t: Token) -> Pattern:
        if t.kind == "SPEC":
            return self._raw_spec(t)
        if t.kind == "ORTHO":
            return RawOrtho(t.value, False, t.loc)
        if t.kind == "IDENT":
            if t.value in KEYWORDS:
                raise lex.error("%r is a keyword, not a macro" % t.value, t.start, t.end)
            return Macro(t.value, None, t.loc)
        nt = lex.next("pattern")
        if nt.start != t.end:
            raise lex.error("\"'\" (strict) must be followed directly by {...}, [x] or $n", t.start, t.end)
        if nt.kind == "SPEC":
            return self._raw_spec(nt, strict=True)
        if nt.kind == "ORTHO":
            return RawOrtho(nt.value, True, nt.loc)
        if nt.kind == "BACKREF":
            return BackRef(nt.value, nt.loc)
        raise lex.error("\"'\" (strict) applies only to {...}, [x] or $n", t.start, nt.end)

    def _postfix(self, lex: Lexer, node: Pattern) -> Pattern:
        post = lex.peek("pattern")
        if post.kind == "STAR":
            lex.next("pattern")
            return Star(node, node.loc)
        if post.kind == "PLUS" and not getattr(self, "_rhs_mode", False):
            lex.next("pattern")
            return Plus(node, node.loc)
        if post.kind == "QMARK":
            lex.next("pattern")
            return Opt(node, node.loc)
        return node

    def _tier_elem(self, lex: Lexer, t: Token, spec: Optional[Pattern], rhs: bool) -> Pattern:
        g = t.value
        op, tier, x, exact, name = g["op"], g["tier"], g["x"] or "", bool(g["exact"]), g["name"]
        if op:
            if not rhs:
                raise lex.error("^%s is only allowed on the right-hand side" % op, t.start, t.end, hint="spec §6.5")
            if spec is None:
                raise lex.error("^%s needs a segment before it, e.g. V^%s[L]" % (op, op), t.start, t.end)
            if exact:
                raise lex.error("\"'\" (exactly one link) is not allowed with ^%s" % op, t.start, t.end)
            return LinkOp(spec, tier, op, x, name, spec.loc)
        if x == "0" and spec is None and not rhs:
            raise lex.error("^0 applies to a segment: write S^0", t.start, t.end)
        if spec is None:
            return AutoFloat(tier, x, name, t.loc)
        return Linked(spec, tier, x, exact, name, spec.loc)

    # -- small helpers ---------------------------------------------------------------------

    def _expect(self, lex: Lexer, mode: str, kind: str, what: str) -> Token:
        t = lex.next(mode)
        if t.kind != kind:
            raise lex.error("expected %s" % what, t.start, t.end if t.end > t.start else None)
        return t

    def _expect_eol(self, lex: Lexer, mode: str) -> None:
        t = lex.next(mode)
        if t.kind != "EOL":
            raise lex.error("unexpected %r at the end of the line" % t.text, t.start, t.end)


def parse(text: str, file: str = "<string>") -> Script:
    """Parse a script (spec §2–§10; design §7.2). Raises :class:`YascLoadError` holding every
    syntax error found (spec §12)."""
    return Parser(text, file).parse()
