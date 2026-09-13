"""Line-oriented, mode-sensitive lexer for YASC scripts (spec §3; design §7.1).

Two stages:

1. :func:`split_lines` turns the source into :class:`LogicalLine` objects. It strips
   ``%%`` comments and ``#``-comment lines (spec §3.2), joins physical lines at ``/\\``
   (spec §3.3) and starts a new logical line before every ``]]`` that follows other text, so
   ``]]`` (with its trailing modifiers) always begins a line. Inside ``[...]`` and ``"..."``
   nothing is recognised (spec §3.3). Every character keeps its *physical* line and column.
2. :class:`Lexer` produces tokens from one logical line on demand, in a **mode** chosen by
   the parser (design §7.1): ``top``, ``phon``, ``ortho``, ``pattern``, ``template``;
   ``spec`` (inside ``{}``), bracketed orthographic text (``[...]``) and ``string`` are
   entered automatically from those modes.

Every :class:`Token` carries a :class:`~yasc.errors.SourceLoc`.
"""

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

from .errors import SourceLoc, YascSyntaxError

__all__ = [
    "LogicalLine",
    "Token",
    "ConstraintSyntax",
    "Lexer",
    "split_lines",
    "tokenize",
    "unescape_string",
    "escape_string",
    "MODES",
]

#: The lexer modes the parser can request (design §7.1).
MODES = ("top", "phon", "ortho", "pattern", "template")

_IDENT = r"[A-Za-z][A-Za-z0-9_]*"
# In patterns an identifier never contains a run of underscores, so ``V___`` is ``V ___``
# (design §13, decision 64).
_PAT_IDENT = r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*"
_VAR = r"[A-Za-z][A-Za-z0-9_]*"
_OP1 = r"(?:<[^\s<>()\[\]{}#]+>|[^\sA-Za-z\[\]{}()#<>]+)"

_RE_WS = re.compile(r"\s+")
_RE_IDENT = re.compile(_IDENT)
_RE_PAT_IDENT = re.compile(_PAT_IDENT)

# Spec mode (spec §6.2).
_RE_ABSENT = re.compile(r"_(%s)" % _IDENT)
_RE_WEAKVAR = re.compile(r"\(\?(%s)\)(%s)" % (_VAR, _IDENT))
_RE_VARC = re.compile(r"\((%s)\)(%s)" % (_VAR, _IDENT))
_RE_CMP = re.compile(r"(>=|<=|>|<)\s*(-?\d+)\s*(%s)" % _IDENT)
_RE_OPVAR = re.compile(r"(%s(?:#%s)*)\((\??)(%s)\)(%s)" % (_OP1, _OP1, _VAR, _IDENT))
_RE_VALUEC = re.compile(r"(\[[A-Za-z]+\]|[^\sA-Za-z\[\]{}()#_]+)(%s)" % _IDENT)
_RE_SETVAL = re.compile(r"\[[A-Za-z]+\]|[^\sA-Za-z\[\]{}()#_]+")

# Pattern mode (spec §3.4, §6.1, §8).
_RE_TIER = re.compile(
    r"\^(?P<op>[+-])?(?:(?P<tier>%s)\.)?(?P<x>\[[^\]\s]*\]|\(\??%s\)|\*|0)?(?P<exact>')?(?:=(?P<name>%s))?"
    % (_IDENT, _VAR, _VAR))
_RE_BRACKET = re.compile(r"([<>]):(%s|\*)" % _IDENT)
_MODS = [
    (re.compile(r"/\?\?\?(:each)?"), "/???"),
    (re.compile(r"/%\s*(\d+(?:\.\d+)?)(:each)?"), "/%"),
    (re.compile(r"/\"\s*(" + r"[A-Za-z_][A-Za-z0-9_.]*" + r")?"), '/"'),
    (re.compile(r"/::"), "/::"),
    (re.compile(r"/:@\s*(-?\d+)?"), "/:@"),
    (re.compile(r"/:([ioFLDC][+-])"), None),
    (re.compile(r"/:C\*"), "/:C*"),
    (re.compile(r"/:Raw(?![A-Za-z0-9_])"), "/:Raw"),
    (re.compile(r"/:T(?![A-Za-z0-9_])"), "/:T"),
    (re.compile(r"/:1(?![0-9])"), "/:1"),
    (re.compile(r"/:\*"), "/:*"),
    (re.compile(r"/:>"), "/:>"),
    (re.compile(r"/:<"), "/:<"),
    (re.compile(r"/:~"), "/:~"),
    (re.compile(r"/:\$"), "/:$"),
    (re.compile(r"/\*"), "/*"),
    (re.compile(r"/~"), "/:~"),
    (re.compile(r"/!"), "/!"),
]
_PUNCT_PATTERN = [
    ("-->", "ARROW"), ("<<", "LALT"), (">>", "RALT"), ("...", "ANYTHING"), ("##", "BOUNDARY"),
    ("|", "BAR"), ("(", "LPAREN"), (")", "RPAREN"), ("*", "STAR"), ("+", "PLUS"), ("?", "QMARK"),
    (":", "COLON"), ("~", "TILDE"), ("'", "STRICT"), ("#", "BOUNDARY"), (".", "BOUNDARY"),
    ("-", "BOUNDARY"), ("=", "BOUNDARY"), (",", "COMMA"),
]

# Phonology mode (spec §4).
_RE_IMPL_ARROW = re.compile(r"<(--|~~)(?:\(([^\s()]+)\))?(--|~~)?>|-->|~~>")
_RE_PHON_VALUE = re.compile(r"[^\s\[\]{}()#,=A-Za-z]+")
_RE_BRVALUE = re.compile(r"\[([A-Za-z]+)\]")
_RE_OPNAME = re.compile(r"\(([^\s()]+)\)")

# Top mode (spec §2, §10).
_RE_VARTOK = re.compile(r"\$(?:(_)|(%s)(?:\[(\d+)\])?)" % _IDENT)
_RE_NUM = re.compile(r"-?\d+")
_RE_WORD = re.compile(r"[^\s()\"=]+")


# --------------------------------------------------------------------------------------------
# Logical lines
# --------------------------------------------------------------------------------------------


class LogicalLine:
    """One statement's text after comment removal and ``/\\`` joining (spec §3.2, §3.3).

    ``text`` is the joined text; ``pos[k]`` is the physical ``(line, col)`` of ``text[k]``
    (1-based; one extra entry marks the end of the line). ``opens`` is true when the line
    contains a ``[[`` token, ``closes`` when it starts with ``]]``. ``phys`` maps physical
    line numbers to their text, for caret excerpts.
    """

    __slots__ = ("text", "pos", "file", "phys", "opens", "closes")

    def __init__(self, text: str, pos: List[Tuple[int, int]], file: str, phys: Sequence[str],
                 opens: bool, closes: bool) -> None:
        self.text = text
        self.pos = pos
        self.file = file
        self.phys = phys
        self.opens = opens
        self.closes = closes

    @property
    def line(self) -> int:
        """The physical line number of the first character (spec §12)."""
        return self.pos[0][0]

    def loc(self, start: int, end: Optional[int] = None) -> SourceLoc:
        """The location of ``text[start:end]`` (spec §12). A span that crosses a physical
        line (after ``/\\`` joining) has no ``end_col``."""
        start = max(0, min(start, len(self.text)))
        line, col = self.pos[start]
        end_col = None
        if end is not None and end > start:
            eline, ecol = self.pos[min(end, len(self.text)) - 1]
            if eline == line and ecol + 1 >= col:
                end_col = ecol + 1
        return SourceLoc(self.file, line, col, end_col)

    def source_line(self, start: int) -> str:
        """The physical line containing ``text[start]`` (for caret excerpts)."""
        line = self.pos[max(0, min(start, len(self.text)))][0]
        return self.phys[line - 1] if 0 < line <= len(self.phys) else ""

    def error(self, message: str, start: int, end: Optional[int] = None, hint: Optional[str] = None) -> YascSyntaxError:
        """A :class:`YascSyntaxError` located at ``text[start:end]`` (spec §12)."""
        return YascSyntaxError(message, self.loc(start, end), hint, self.source_line(start))

    def __repr__(self) -> str:
        return "<LogicalLine %d: %r>" % (self.line, self.text)


def split_lines(text: str, file: str = "<string>") -> List[LogicalLine]:
    """Split source text into logical lines (spec §3.2, §3.3; design §7.1 step 1).

    * ``%%`` starts a comment to the end of the physical line;
    * a physical line whose first non-blank character is ``#`` followed by whitespace or the
      end of the line is a comment line;
    * ``/\\`` joins the next physical line to this one; the rest of the physical line after it
      is discarded;
    * a ``]]`` preceded by other text on the same logical line starts a new logical line;
    * inside ``[...]`` and ``"..."`` (on one physical line) nothing is recognised.

    Blank logical lines are dropped.
    """
    phys = text.split("\n")
    phys = [p[:-1] if p.endswith("\r") else p for p in phys]
    out: List[LogicalLine] = []
    chars: List[str] = []
    pos: List[Tuple[int, int]] = []
    flags = {"opens": False}

    def flush(end_pos: Tuple[int, int]) -> None:
        if "".join(chars).strip():
            s = "".join(chars)
            lead = len(s) - len(s.lstrip())
            body = s.strip()
            p = pos[lead:lead + len(body)]
            line, col = p[-1]
            p.append((line, col + 1))
            out.append(LogicalLine(body, p, file, phys, flags["opens"], body.startswith("]]")))
        chars.clear()
        pos.clear()
        flags["opens"] = False

    continuing = False
    for ln, raw in enumerate(phys, 1):
        stripped = raw.lstrip()
        if stripped.startswith("#") and (len(stripped) == 1 or stripped[1].isspace()):
            continue
        i, n = 0, len(raw)
        in_br = in_str = cont = False
        while i < n:
            ch = raw[i]
            if in_br:
                if ch == "]":
                    in_br = False
            elif in_str:
                if ch == "\\" and i + 1 < n:
                    chars.extend(raw[i:i + 2])
                    pos.extend([(ln, i + 1), (ln, i + 2)])
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
            else:
                if raw.startswith("%%", i):
                    break
                if raw.startswith("/\\", i):
                    cont = True
                    break
                if raw.startswith("[[", i):
                    flags["opens"] = True
                    chars.extend("[[")
                    pos.extend([(ln, i + 1), (ln, i + 2)])
                    i += 2
                    continue
                if raw.startswith("]]", i):
                    if "".join(chars).strip():
                        flush((ln, i + 1))
                    chars.extend("]]")
                    pos.extend([(ln, i + 1), (ln, i + 2)])
                    i += 2
                    continue
                if ch == "[":
                    in_br = True
                elif ch == '"' and not (i > 0 and raw[i - 1] == "/"):
                    in_str = True
            chars.append(ch)
            pos.append((ln, i + 1))
            i += 1
        if cont:
            chars.append(" ")
            pos.append((ln, i + 1))
            continuing = True
            continue
        continuing = False
        flush((ln, n + 1))
    if continuing or chars:
        flush((len(phys), 1))
    return out


# --------------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """A token: ``kind``, source ``text``, a decoded ``value``, the offsets ``start``/``end``
    in its logical line, and ``loc`` (design §7.1)."""

    kind: str
    text: str
    value: Any
    start: int
    end: int
    loc: SourceLoc = field(compare=False, repr=False)


@dataclass(frozen=True)
class ConstraintSyntax:
    """One constraint inside ``{...}``, before feature resolution (spec §6.2).

    ``kind`` is one of ``value`` (``vF``), ``bare`` (``F``), ``absent`` (``_F``), ``in``
    (``{v1 v2}F``), ``cmp`` (``>=2F``), ``var`` (``(a)F`` or ``op#op(a)F``), ``weakvar``
    (``(?a)F``) or ``ellipsis`` (``...``, allowed only in orthography lines).
    """

    kind: str
    feature: Optional[str] = None
    value: Optional[str] = None
    values: Tuple[str, ...] = ()
    var: Optional[str] = None
    ops: Tuple[str, ...] = ()
    cmp: Optional[str] = None
    n: Optional[int] = None
    loc: Optional[SourceLoc] = field(default=None, compare=False, repr=False)

    def canonical(self) -> str:
        """The constraint as written in spec syntax (spec §6.2, §11.3)."""
        k = self.kind
        if k == "value":
            return "%s%s" % (self.value, self.feature)
        if k == "bare":
            return self.feature or ""
        if k == "absent":
            return "_" + (self.feature or "")
        if k == "in":
            return "{%s}%s" % (" ".join(self.values), self.feature)
        if k == "cmp":
            return "%s%d%s" % (self.cmp, self.n, self.feature)
        if k == "var":
            return "%s(%s)%s" % ("#".join(self.ops), self.var, self.feature)
        if k == "weakvar":
            return "(?%s)%s" % (self.var, self.feature)
        return "..."


def unescape_string(body: str) -> str:
    """Decode the escapes ``\\n \\t \\" \\\\`` of a string literal (spec §10.5); other
    backslashes are kept literally."""
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            rep = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt)
            if rep is not None:
                out.append(rep)
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def escape_string(value: str) -> str:
    """Inverse of :func:`unescape_string`: the literal (with quotes) for ``value``."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


# --------------------------------------------------------------------------------------------
# The lexer
# --------------------------------------------------------------------------------------------


class Lexer:
    """Produces tokens from one :class:`LogicalLine` in a mode chosen per call (design §7.1).

    ``next(mode)`` consumes a token, ``peek(mode)`` does not; the end of the line is a token
    of kind ``EOL``. ``pos`` may be saved and restored for backtracking. ``match(regex)``
    consumes raw text for the few places where the parser reads a fixed shape directly.
    """

    def __init__(self, line: LogicalLine) -> None:
        self.line = line
        self.text = line.text
        self.pos = 0

    # -- helpers ---------------------------------------------------------------------------

    def skip_ws(self) -> None:
        """Skip whitespace."""
        m = _RE_WS.match(self.text, self.pos)
        if m:
            self.pos = m.end()

    def at_end(self) -> bool:
        """True when only whitespace remains."""
        self.skip_ws()
        return self.pos >= len(self.text)

    def rest(self) -> str:
        """The unconsumed text, stripped."""
        return self.text[self.pos:].strip()

    def ws_before(self) -> bool:
        """True if the character before the current position is whitespace (or none)."""
        return self.pos == 0 or self.text[self.pos - 1].isspace()

    def _tok(self, kind: str, start: int, end: int, value: Any = None) -> Token:
        self.pos = end
        return Token(kind, self.text[start:end], value, start, end, self.line.loc(start, end))

    def error(self, message: str, start: Optional[int] = None, end: Optional[int] = None,
              hint: Optional[str] = None) -> YascSyntaxError:
        """A syntax error at ``start..end`` (default: the current position)."""
        if start is None:
            start = self.pos
        return self.line.error(message, start, end, hint)

    def match(self, regex: "re.Pattern") -> Optional[Token]:
        """Consume ``regex`` at the current position (after whitespace); a ``RAW`` token whose
        value is the match object, or ``None``."""
        self.skip_ws()
        m = regex.match(self.text, self.pos)
        if not m:
            return None
        return self._tok("RAW", m.start(), m.end(), m)

    def peek(self, mode: str = "pattern") -> Token:
        """The next token in ``mode`` without consuming it."""
        save = self.pos
        try:
            return self.next(mode)
        finally:
            self.pos = save

    def next(self, mode: str = "pattern") -> Token:
        """Consume and return the next token in ``mode`` (design §7.1)."""
        self.skip_ws()
        if self.pos >= len(self.text):
            return Token("EOL", "", None, self.pos, self.pos, self.line.loc(self.pos))
        if mode == "pattern":
            return self._pattern()
        if mode == "template":
            return self._template()
        if mode == "phon":
            return self._phon()
        if mode == "ortho":
            return self._ortho()
        if mode == "top":
            return self._top()
        raise ValueError("unknown lexer mode %r" % (mode,))

    # -- shared sub-modes ------------------------------------------------------------------

    def _bracketed(self) -> Token:
        """Bracketed-orthographic mode: literal text up to the first ``]`` (spec §5.6)."""
        start = self.pos
        close = self.text.find("]", start + 1)
        if close < 0:
            raise self.error("unterminated orthographic string: missing ']'", start, len(self.text),
                             hint="inside [...] everything up to the first ] is literal (spec §3.3)")
        return self._tok("ORTHO", start, close + 1, self.text[start + 1:close])

    def _string(self) -> Token:
        """String mode: ``"..."`` with ``\\n \\t \\" \\\\`` (spec §10.5)."""
        start = self.pos
        i = start + 1
        while i < len(self.text):
            ch = self.text[i]
            if ch == "\\" and i + 1 < len(self.text):
                i += 2
                continue
            if ch == '"':
                return self._tok("STRING", start, i + 1, unescape_string(self.text[start + 1:i]))
            i += 1
        raise self.error("unterminated string: missing '\"'", start, len(self.text))

    def _spec(self) -> Token:
        """Spec mode: ``{`` constraints ``}`` as one ``SPEC`` token (spec §6.2)."""
        start = self.pos
        self.pos += 1
        cons: List[ConstraintSyntax] = []
        text = self.text
        while True:
            self.skip_ws()
            p = self.pos
            if p >= len(text):
                raise self.error("unterminated segment spec: missing '}'", start, len(text))
            ch = text[p]
            if ch == "}":
                self.pos = p + 1
                break
            if text.startswith("...", p):
                self.pos = p + 3
                cons.append(ConstraintSyntax("ellipsis", loc=self.line.loc(p, p + 3)))
                continue
            if ch == "{":
                cons.append(self._value_set())
                continue
            m = _RE_ABSENT.match(text, p)
            if m:
                cons.append(ConstraintSyntax("absent", m.group(1), loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            m = _RE_WEAKVAR.match(text, p)
            if m:
                cons.append(ConstraintSyntax("weakvar", m.group(2), var=m.group(1), loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            m = _RE_VARC.match(text, p)
            if m:
                cons.append(ConstraintSyntax("var", m.group(2), var=m.group(1), loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            m = _RE_CMP.match(text, p)
            if m:
                cons.append(ConstraintSyntax("cmp", m.group(3), cmp=m.group(1), n=int(m.group(2)),
                                             loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            m = _RE_OPVAR.match(text, p)
            if m:
                if m.group(2):
                    raise self.error("operations cannot apply to a weak variable (?%s)" % m.group(3), p, m.end(),
                                     hint="use (%s) instead of (?%s) (design §13, decision 13)"
                                     % (m.group(3), m.group(3)))
                cons.append(ConstraintSyntax("var", m.group(4), var=m.group(3), ops=tuple(m.group(1).split("#")),
                                             loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            m = _RE_VALUEC.match(text, p)
            if m:
                cons.append(ConstraintSyntax("value", m.group(2), value=m.group(1), loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            m = _RE_IDENT.match(text, p)
            if m:
                cons.append(ConstraintSyntax("bare", m.group(), loc=self.line.loc(p, m.end())))
                self.pos = m.end()
                continue
            if "}" not in text[p:]:
                raise self.error("unterminated segment spec: missing '}'", start, len(text))
            end = p + 1
            while end < len(text) and not text[end].isspace() and text[end] != "}":
                end += 1
            raise self.error("cannot read constraint %r in a segment spec" % text[p:end], p, end,
                             hint="constraints are vF, _F, {v1 v2}F, >nF, (a)F, (?a)F, op(a)F or F (spec §6.2)")
        return self._tok("SPEC", start, self.pos, tuple(cons))

    def _value_set(self) -> ConstraintSyntax:
        """``{v1 v2}F`` inside a spec (spec §6.2)."""
        start = self.pos
        self.pos += 1
        vals: List[str] = []
        while True:
            self.skip_ws()
            if self.pos >= len(self.text):
                raise self.error("unterminated value set: missing '}'", start, len(self.text))
            if self.text[self.pos] == "}":
                self.pos += 1
                break
            m = _RE_SETVAL.match(self.text, self.pos)
            if not m:
                raise self.error("expected a value in the value set", self.pos, self.pos + 1)
            vals.append(m.group())
            self.pos = m.end()
        m = _RE_IDENT.match(self.text, self.pos)
        if not m:
            raise self.error("a value set {...} must be followed directly by a feature name", start, self.pos,
                             hint="write e.g. {1 2}Stress (spec §6.2)")
        self.pos = m.end()
        return ConstraintSyntax("in", m.group(), values=tuple(vals), loc=self.line.loc(start, m.end()))

    def _modifier(self) -> Optional[Token]:
        """A rule modifier starting with ``/`` (spec §8.1–§8.7), or ``None``."""
        p = self.pos
        text = self.text
        for regex, key in _MODS:
            m = regex.match(text, p)
            if not m:
                continue
            if key is None:
                return self._tok("MOD", p, m.end(), ("/:" + m.group(1), None))
            if key == "/???":
                return self._tok("MOD", p, m.end(), ("/???", bool(m.group(1))))
            if key == "/%":
                return self._tok("MOD", p, m.end(), ("/%", (float(m.group(1)), bool(m.group(2)))))
            if key == '/"':
                if not m.group(1):
                    raise self.error('/" must be followed by a rule name', p, m.end(), hint='write /" Name (spec §8.7)')
                return self._tok("MOD", p, m.end(), ('/"', m.group(1)))
            if key == "/:@":
                if m.group(1) is None:
                    raise self.error("/:@ must be followed by an integer date", p, m.end(),
                                     hint="write e.g. /:@1200 (spec §8.10)")
                return self._tok("MOD", p, m.end(), ("/:@", int(m.group(1))))
            return self._tok("MOD", p, m.end(), (key, None))
        if text.startswith("/:", p):
            end = p + 2
            while end < len(text) and not text[end].isspace() and text[end] != "/":
                end += 1
            raise self.error("unknown modifier %r" % text[p:end], p, end,
                             hint="modifiers: / /! /:i± /:o± /:F± /:L± /:D± /:C± /:C* /:1 /* /:* /:> /:< /:~ "
                             "/:Raw /:$ /:T /:@n /%n /??? /\" /:: (spec §8)")
        return self._tok("MOD", p, p + 1, ("/", None))

    # -- modes -----------------------------------------------------------------------------

    def _pattern(self) -> Token:
        """Pattern mode: the token set of spec §3.4 (design §7.1)."""
        text = self.text
        p = self.pos
        ch = text[p]
        if ch == "/":
            return self._modifier()
        if ch == "{":
            return self._spec()
        if ch == "[":
            return self._bracketed()
        if ch == '"':
            return self._string()
        if ch == "^":
            m = _RE_TIER.match(text, p)
            if not m.group("x") and not m.group("name"):
                raise self.error("a tier element needs a value: ^[H], ^(a), ^*, ^0 or ^=name", p, m.end(),
                                 hint="spec §6.5")
            return self._tok("TIER", p, m.end(), m.groupdict())
        if ch == "$":
            m = re.compile(r"\$(\d+)").match(text, p)
            if m:
                return self._tok("BACKREF", p, m.end(), int(m.group(1)))
            m = _RE_VARTOK.match(text, p)
            if m:
                return self._tok("VAR", p, m.end(), m.group(1) or m.group(2))
            raise self.error("expected $n or $name after '$'", p, p + 1)
        if ch == "_":
            end = p
            while end < len(text) and text[end] == "_":
                end += 1
            return self._tok("LOCUS", p, end)
        m = _RE_BRACKET.match(text, p)
        if m:
            kind = "BRACKET_OPEN" if m.group(1) == "<" else "BRACKET_CLOSE"
            return self._tok(kind, p, m.end(), m.group(2))
        if ch == "0" and not (p + 1 < len(text) and (text[p + 1].isalnum() or text[p + 1] == "_")):
            return self._tok("NOTHING", p, p + 1)
        for lit, kind in _PUNCT_PATTERN:
            if text.startswith(lit, p):
                return self._tok(kind, p, p + len(lit), lit)
        m = _RE_PAT_IDENT.match(text, p)
        if m:
            return self._tok("IDENT", p, m.end(), m.group())
        end = p + 1
        while end < len(text) and not text[end].isspace():
            end += 1
        hint = None
        if ch in "<>":
            hint = "direction modifiers are /:> and /:<; brackets are asserted with <:N and >:N"
        raise self.error("unexpected %r in a pattern" % text[p:end], p, end if end - p < 10 else p + 1, hint=hint)

    def _template(self) -> Token:
        """Paradigm-template tokens: ``$_``, ``[...]``, ``<Label:``, ``>``, boundaries and
        modifiers (spec §9)."""
        text = self.text
        p = self.pos
        m = re.compile(r"<(%s):" % _IDENT).match(text, p)
        if m:
            return self._tok("TOPEN", p, m.end(), m.group(1))
        if text[p] == ">":
            return self._tok("TCLOSE", p, p + 1)
        if text.startswith("$_", p):
            return self._tok("STEM", p, p + 2)
        return self._pattern()

    def _phon(self) -> Token:
        """Phonology mode: feature-declaration lines (spec §4)."""
        text = self.text
        p = self.pos
        ch = text[p]
        if ch == "{":
            return self._spec()
        if ch == "'" and text.startswith("{", p + 1):
            self.pos += 1
            t = self._spec()
            return Token("SSPEC", text[p:t.end], t.value, p, t.end, self.line.loc(p, t.end))
        m = _RE_IMPL_ARROW.match(text, p)
        if m:
            return self._tok("IARROW", p, m.end(), m)
        if text.startswith("==", p) and (p + 2 >= len(text) or text[p + 2].isspace()):
            return self._tok("ALIAS", p, p + 2)
        for lit, kind in (("(", "LPAREN"), (")", "RPAREN"), (",", "COMMA"), ("=", "EQUALS")):
            if ch == lit:
                return self._tok(kind, p, p + 1, lit)
        m = _RE_BRVALUE.match(text, p)
        if m:
            return self._tok("VALUE", p, m.end(), m.group(1))
        m = _RE_IDENT.match(text, p)
        if m:
            return self._tok("IDENT", p, m.end(), m.group())
        m = _RE_PHON_VALUE.match(text, p)
        if m:
            return self._tok("VALUE", p, m.end(), m.group())
        raise self.error("unexpected %r in a phonology line" % ch, p, p + 1,
                         hint="a comment after code starts with %% (spec §3.2, A1)" if ch == "#" else None)

    def _ortho(self) -> Token:
        """Orthography mode: grapheme lists, specs, ``==>``, settings (spec §5.6)."""
        text = self.text
        p = self.pos
        ch = text[p]
        if ch == "[":
            return self._bracketed()
        if ch == "{":
            return self._spec()
        if text.startswith("==>", p):
            return self._tok("DARROW", p, p + 3)
        if text.startswith("==", p):
            return self._tok("ALIAS", p, p + 2)
        if ch == "*":
            return self._tok("STAR", p, p + 1)
        m = _RE_IDENT.match(text, p)
        if m:
            return self._tok("IDENT", p, m.end(), m.group())
        raise self.error("unexpected %r in an orthography line" % ch, p, p + 1)

    def _top(self) -> Token:
        """Top mode: commands, variables, strings, numbers, words (spec §2, §10)."""
        text = self.text
        p = self.pos
        ch = text[p]
        if ch == '"':
            return self._string()
        if ch == "[":
            if text.startswith("[[", p):
                return self._tok("BLOCK_OPEN", p, p + 2)
            return self._bracketed()
        if text.startswith("]]", p):
            return self._tok("BLOCK_CLOSE", p, p + 2)
        if text.startswith("::=", p):
            return self._tok("ASSIGN", p, p + 3)
        if text.startswith(":=", p):
            return self._tok("ASSIGN", p, p + 2)
        if text.startswith("===", p):
            return self._tok("MACRO_DEF", p, p + 3)
        if ch == "$":
            m = _RE_VARTOK.match(text, p)
            if m:
                if m.group(1):
                    return self._tok("VAR", p, m.end(), ("_", None))
                return self._tok("VAR", p, m.end(), (m.group(2), int(m.group(3)) if m.group(3) else None))
            raise self.error("expected a variable name after '$'", p, p + 1)
        if ch == "!":
            m = _RE_IDENT.match(text, p + 1)
            if m:
                return self._tok("COMMAND", p, m.end(), m.group())
        m = _RE_NUM.match(text, p)
        if m and (m.end() >= len(text) or not (text[m.end()].isalpha() or text[m.end()] == "_")):
            return self._tok("NUM", p, m.end(), int(m.group()))
        for lit, kind in (("(", "LPAREN"), (")", "RPAREN"), ("=", "EQUALS")):
            if ch == lit:
                return self._tok(kind, p, p + 1, lit)
        m = _RE_IDENT.match(text, p)
        if m and (m.end() >= len(text) or text[m.end()].isspace() or text[m.end()] in '()="['):
            return self._tok("IDENT", p, m.end(), m.group())
        m = _RE_WORD.match(text, p)
        return self._tok("WORD", p, m.end(), m.group())


def tokenize(text: str, mode: str = "pattern", file: str = "<string>") -> List[Token]:
    """Tokenize a single-line text in one mode (a convenience for tests and tools; design
    §7.1). The final ``EOL`` token is not included."""
    lines = split_lines(text, file)
    out: List[Token] = []
    for line in lines:
        lex = Lexer(line)
        while True:
            t = lex.next(mode)
            if t.kind == "EOL":
                break
            out.append(t)
    return out
