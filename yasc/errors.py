"""Source locations, the YASC exception hierarchy and caret-style diagnostics.

Implements spec §12 ("Errors and diagnostics"); see design §2 (``errors.py``, phase P0).

Every YASC error may carry a :class:`SourceLoc`, the text of the offending source line and a
hint. :meth:`YascError.format` renders them in the conventional compiler style::

    script.yasc:3:9: error: unknown feature 'Hi'
      V --> {+Hi} / ___#
              ^^
    hint: did you mean 'High'?

Column conventions (design §13, decision 6):

* ``line`` and ``col`` are 1-based; ``col`` counts Unicode code points, so a tab is one
  column.
* ``end_col`` is 1-based and *exclusive* (one past the last character of the span).
* In the excerpt, tabs are expanded to tab stops of :data:`TAB_WIDTH` columns, combining
  characters take no width and East Asian wide characters take two, so the carets line up
  under the right characters.
"""

import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Union

__all__ = [
    "TAB_WIDTH",
    "MAX_REPORTED_ERRORS",
    "SourceLoc",
    "location_from_offset",
    "format_excerpt",
    "YascError",
    "YascLoadError",
    "YascSyntaxError",
    "YascDefinitionError",
    "YascRuntimeError",
    "YascWarning",
    "NotImplementedYet",
]

#: Tab stop width used when displaying source excerpts (design §13, decision 6).
TAB_WIDTH = 8

#: Maximum number of errors that one load reports (spec §12).
MAX_REPORTED_ERRORS = 20


@dataclass(frozen=True)
class SourceLoc:
    """A position (or a span on one line) in a source file (spec §12).

    ``line`` and ``col`` are 1-based; ``col`` counts code points. ``end_col``, when given, is
    the exclusive 1-based end column of a span on the same line. ``str(loc)`` gives
    ``file:line:col``.
    """

    file: str
    line: int
    col: int
    end_col: Optional[int] = None

    def __post_init__(self) -> None:
        if self.line < 1 or self.col < 1:
            raise ValueError("SourceLoc line and col are 1-based: got %d:%d" % (self.line, self.col))
        if self.end_col is not None and self.end_col < self.col:
            raise ValueError("SourceLoc end_col %d precedes col %d" % (self.end_col, self.col))

    def __str__(self) -> str:
        return "%s:%d:%d" % (self.file, self.line, self.col)


def location_from_offset(
    text: str,
    offset: int,
    file: str = "<string>",
    end_offset: Optional[int] = None,
) -> Tuple[SourceLoc, str]:
    """Build a :class:`SourceLoc` and the source-line excerpt for a character offset (spec §12).

    ``offset`` is a 0-based code-point index into ``text`` (``len(text)`` is allowed and
    denotes end of input). ``end_offset``, if given, is the exclusive end of the span; a span
    that runs past the end of the line is clipped to the end of that line. Lines end at
    ``\\n``; a ``\\r`` before it is not part of the returned line text.

    Returns ``(loc, line_text)``, suitable for the ``loc`` and ``source_line`` arguments of
    :class:`YascError`.
    """
    if not 0 <= offset <= len(text):
        raise ValueError("offset %d outside text of length %d" % (offset, len(text)))
    if end_offset is not None and end_offset < offset:
        raise ValueError("end_offset %d precedes offset %d" % (end_offset, offset))
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end == -1:
        line_end = len(text)
    line_text = text[line_start:line_end]
    if line_text.endswith("\r"):
        line_text = line_text[:-1]
    line = text.count("\n", 0, offset) + 1
    col = offset - line_start + 1
    end_col = None
    if end_offset is not None:
        end_col = min(end_offset, line_start + len(line_text)) - line_start + 1
        end_col = max(end_col, col)
    return SourceLoc(file, line, col, end_col), line_text


def _char_width(ch: str) -> int:
    """Display width of one non-tab character: 0 for combining marks, 2 for wide ones."""
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def _display(text: str, start_width: int = 0) -> Tuple[str, int]:
    """Expand tabs in ``text``; return the expanded text and the display width reached."""
    out = []
    width = start_width
    for ch in text:
        if ch == "\t":
            n = TAB_WIDTH - (width % TAB_WIDTH)
            out.append(" " * n)
            width += n
        else:
            out.append(ch)
            width += _char_width(ch)
    return "".join(out), width


def format_excerpt(source_line: str, col: int, end_col: Optional[int] = None, indent: str = "  ") -> str:
    """Render a source line and a caret line under columns ``col``..``end_col`` (spec §12).

    ``col`` and ``end_col`` follow the :class:`SourceLoc` conventions. ``col`` may be one past
    the last character (a caret at end of line). At least one caret is always drawn. Returns
    two lines joined by ``\\n`` (no trailing newline).
    """
    line_text = source_line.rstrip("\r\n")
    start = min(max(col, 1), len(line_text) + 1) - 1
    stop = start + 1 if end_col is None else min(max(end_col - 1, start), len(line_text))
    shown, _ = _display(line_text)
    _, caret_start = _display(line_text[:start])
    _, caret_stop = _display(line_text[start:stop], caret_start)
    carets = "^" * max(1, caret_stop - caret_start)
    return "%s%s\n%s%s%s" % (indent, shown, indent, " " * caret_start, carets)


class YascError(Exception):
    """Base class of every YASC error (spec §12).

    Carries a message, an optional :class:`SourceLoc`, an optional hint and optionally the
    text of the source line that ``loc`` points into. ``str(error)`` equals
    :meth:`format`.
    """

    #: The severity label printed after the location.
    label = "error"

    def __init__(
        self,
        message: str,
        loc: Optional[SourceLoc] = None,
        hint: Optional[str] = None,
        source_line: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.loc = loc
        self.hint = hint
        self.source_line = source_line

    @classmethod
    def at_offset(
        cls,
        message: str,
        text: str,
        offset: int,
        file: str = "<string>",
        end_offset: Optional[int] = None,
        hint: Optional[str] = None,
        **kwargs: object
    ) -> "YascError":
        """Create an error located at ``offset`` in ``text`` (spec §12).

        Uses :func:`location_from_offset` to fill in ``loc`` and ``source_line``. Extra keyword
        arguments go to the subclass constructor.
        """
        loc, line_text = location_from_offset(text, offset, file, end_offset)
        return cls(message, loc=loc, hint=hint, source_line=line_text, **kwargs)

    def _notes(self) -> List[str]:
        """Extra ``note:`` lines printed between the excerpt and the hint."""
        return []

    def format(self) -> str:
        """Render the error as a caret-style diagnostic (spec §12).

        The first line is ``file:line:col: error: message`` (or ``error: message`` without a
        location). The source line and a caret line follow when both ``loc`` and
        ``source_line`` are known, then any notes, then ``hint: ...``.
        """
        prefix = "%s: " % self.loc if self.loc is not None else ""
        lines = ["%s%s: %s" % (prefix, self.label, self.message)]
        if self.loc is not None and self.source_line is not None:
            lines.append(format_excerpt(self.source_line, self.loc.col, self.loc.end_col))
        lines.extend("note: %s" % note for note in self._notes())
        if self.hint:
            lines.append("hint: %s" % self.hint)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format()


class YascSyntaxError(YascError):
    """A load-time error in the script's syntax (spec §12)."""


class YascDefinitionError(YascError):
    """A load-time error in a definition: unknown feature, bad value, macro cycle... (spec §12)."""


class NotImplementedYet(YascError, NotImplementedError):
    """A construct that parses but whose semantics belong to a later phase (plan P4).

    ``phase`` names the plan phase that will implement it (for example ``"P8"``). It is also a
    :class:`NotImplementedError`, so generic handlers catch it.
    """

    def __init__(
        self,
        message: str,
        loc: Optional[SourceLoc] = None,
        hint: Optional[str] = None,
        source_line: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> None:
        super().__init__(message, loc=loc, hint=hint, source_line=source_line)
        self.phase = phase

    def _notes(self) -> List[str]:
        if self.phase:
            return ["not implemented until plan phase %s" % self.phase]
        return []


class YascWarning(YascError):
    """A load-time warning, such as macro shadowing or a construct compiled only as a
    placeholder (spec §7, §12; plan P4). It is never raised; it is collected on
    ``CompiledScript.warnings``. ``phase`` names the plan phase a placeholder waits for."""

    label = "warning"

    def __init__(
        self,
        message: str,
        loc: Optional[SourceLoc] = None,
        hint: Optional[str] = None,
        source_line: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> None:
        super().__init__(message, loc=loc, hint=hint, source_line=source_line)
        self.phase = phase

    def _notes(self) -> List[str]:
        if self.phase:
            return ["not implemented until plan phase %s" % self.phase]
        return []


class YascRuntimeError(YascError):
    """An error while applying rules to one record (spec §12).

    Optionally names the ``record`` (its form text or number), the ``rule_id`` and the
    ``rule_name``; ``loc`` is the rule's source location.
    """

    def __init__(
        self,
        message: str,
        loc: Optional[SourceLoc] = None,
        hint: Optional[str] = None,
        source_line: Optional[str] = None,
        record: Optional[Union[str, int]] = None,
        rule_id: Optional[Union[str, int]] = None,
        rule_name: Optional[str] = None,
    ) -> None:
        super().__init__(message, loc=loc, hint=hint, source_line=source_line)
        self.record = record
        self.rule_id = rule_id
        self.rule_name = rule_name

    def _notes(self) -> List[str]:
        parts = []
        if self.rule_id is not None or self.rule_name is not None:
            rule = "rule"
            if self.rule_id is not None:
                rule += " %s" % self.rule_id
            if self.rule_name is not None:
                rule += " %r" % self.rule_name
            parts.append("in " + rule)
        if self.record is not None:
            parts.append("for record %r" % (self.record,))
        return [" ".join(parts)] if parts else []


class YascLoadError(YascError):
    """All the errors found while loading one script (spec §12).

    ``errors`` keeps every error, in the order found; nested ``YascLoadError`` instances are
    flattened. :meth:`format` reports at most :data:`MAX_REPORTED_ERRORS` of them and then a
    count of the rest.
    """

    def __init__(self, errors: Iterable[YascError]) -> None:
        flat: List[YascError] = []
        for err in errors:
            if isinstance(err, YascLoadError):
                flat.extend(err.errors)
            elif isinstance(err, YascError):
                flat.append(err)
            else:
                raise TypeError("YascLoadError expects YascError instances, got %r" % (err,))
        if not flat:
            raise ValueError("YascLoadError needs at least one error")
        self.errors = flat
        count = len(flat)
        summary = "%d error%s while loading" % (count, "" if count == 1 else "s")
        super().__init__(summary, loc=flat[0].loc)

    @property
    def reported(self) -> List[YascError]:
        """The errors that :meth:`format` shows: the first :data:`MAX_REPORTED_ERRORS`."""
        return self.errors[:MAX_REPORTED_ERRORS]

    @property
    def omitted(self) -> int:
        """How many errors :meth:`format` leaves out."""
        return max(0, len(self.errors) - MAX_REPORTED_ERRORS)

    def format(self) -> str:
        """Format the reported errors, one diagnostic after another (spec §12).

        When more than :data:`MAX_REPORTED_ERRORS` were collected, a final line says how many
        were not shown.
        """
        blocks = [err.format() for err in self.reported]
        if self.omitted:
            blocks.append(
                "note: %d more error%s not shown (at most %d are reported)"
                % (self.omitted, "" if self.omitted == 1 else "s", MAX_REPORTED_ERRORS)
            )
        return "\n".join(blocks)
