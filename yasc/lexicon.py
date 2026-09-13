"""Record readers for input lexica (spec §10.2; design §2 ``lexicon.py``, plan P6).

A lexicon is a text file (or stdin) with one **record** per line. Four formats exist
(``!set InputFormat = ...``):

* ``tsv`` (default) — columns separated by tabs;
* ``csv`` — comma-separated values with the usual quoting (:mod:`csv`);
* ``lines`` — the whole line is the form (one field);
* ``regex:<pattern>`` — every group of the pattern is a field; named groups are also
  column names.

A header line ``#! form gloss features date dialect paradigm ...`` names the columns
(names are separated by whitespace or commas). Without a header, column 1 is the form and
the other columns are plain fields. Blank lines and lines starting with ``%%`` are skipped.

Special columns (spec §10.2): ``form`` (the input text), ``features`` (lexical features,
``+Romance !N -Common``), ``date`` (an integer, spec §8.10), ``dialect`` and ``paradigm``
(stored for plan P9). Every record keeps its raw line, its line number and its source name
for error messages (spec §12).
"""

import csv
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .errors import SourceLoc, YascRuntimeError

__all__ = ["Record", "LexiconError", "parse_features", "parse_format", "read_records", "read_file", "word_records",
           "SPECIAL_COLUMNS", "FORMATS"]

#: Column names with a meaning of their own (spec §10.2).
SPECIAL_COLUMNS = ("form", "features", "date", "dialect", "paradigm")

#: The fixed input formats; ``regex:<pattern>`` is the fifth (spec §10.2).
FORMATS = ("tsv", "csv", "lines")

_RE_FEATURE = re.compile(r"^(\[[A-Za-z]+\]|[^A-Za-z\s\[\]{}()#_]*)([A-Za-z][A-Za-z0-9_]*)$")


class LexiconError(YascRuntimeError):
    """A malformed input record: a bad date or feature item, or a line that does not match
    the ``regex:`` format (spec §10.2, §12). It is a run-time error for that record only."""


@dataclass
class Record:
    """One input record (spec §10.2).

    ``form`` is the text of the form column; ``fields`` holds every column in order
    (``$field[1]`` is ``fields[0]``); ``names`` are the column names from the header (or
    regex groups), ``None`` without a header. ``lexical`` maps lexical feature names to
    values (Unary ``!``), ``date``, ``dialect`` and ``paradigm`` come from the special
    columns. ``raw`` is the line as read (without the line break), ``line`` its 1-based
    number in ``source``; ``form_col`` is the 1-based column of the form in ``raw``;
    ``nr`` is the record number (``$NR``, counted from 1 over all inputs of a run).
    ``error`` is set when the line could not be read as a record.
    """

    form: str
    fields: Tuple[str, ...] = ()
    names: Optional[Tuple[str, ...]] = None
    lexical: Dict[str, str] = field(default_factory=dict)
    date: Optional[int] = None
    dialect: Optional[str] = None
    paradigm: Optional[str] = None
    raw: str = ""
    line: int = 0
    source: str = "<input>"
    form_col: int = 1
    nr: int = 0
    error: Optional[YascRuntimeError] = None

    def field(self, n: int) -> str:
        """``$field[n]`` (1-based, spec §10.1). As in awk, a field past the last one is the
        empty string (design §13 entry 105); ``n < 1`` raises :class:`LexiconError`."""
        if n < 1:
            raise LexiconError("fields are numbered from 1: $field[%d] does not exist" % n, self.loc(),
                               source_line=self.raw or None)
        return self.fields[n - 1] if n <= len(self.fields) else ""

    def column(self, name: str) -> Optional[str]:
        """The field of the column called ``name`` in the header, or ``None``."""
        if self.names is None or name not in self.names:
            return None
        k = self.names.index(name)
        return self.fields[k] if k < len(self.fields) else None

    def loc(self, col: int = 1, end_col: Optional[int] = None) -> Optional[SourceLoc]:
        """The record's location (``source:line:col``), or ``None`` without a line number."""
        if self.line < 1:
            return None
        return SourceLoc(self.source, self.line, max(col, 1), end_col)

    @property
    def label(self) -> str:
        """How error messages name the record: ``form (source:line)``."""
        where = " (%s:%d)" % (self.source, self.line) if self.line else ""
        return "%s%s" % (self.form, where)

    @property
    def dialects(self) -> Tuple[str, ...]:
        """The dialect names of the ``dialect`` column, split on whitespace or commas;
        ``()`` when it is absent or empty (spec §10.2, §10.4; plan P9)."""
        return tuple(x for x in re.split(r"[\s,]+", self.dialect or "") if x)


def parse_features(text: str) -> Dict[str, str]:
    """Parse a ``features`` column: space- or comma-separated ``vName`` items such as
    ``+Romance !N -Common 2Class [H]Tone``; a bare ``Name`` is Unary ``!`` (spec §10.2,
    §8.6). Raises :class:`ValueError` on a malformed item."""
    out: Dict[str, str] = {}
    for item in re.split(r"[\s,]+", text.strip()):
        if not item:
            continue
        m = _RE_FEATURE.match(item)
        if m is None:
            raise ValueError("bad lexical feature %r: write vName, e.g. +Romance, !N, -Common" % item)
        value, name = m.group(1), m.group(2)
        if value.startswith("["):
            value = value[1:-1]
        out[name] = value or "!"
    return out


def parse_format(fmt: str):
    """Validate an ``InputFormat`` value: ``tsv``, ``csv``, ``lines`` or ``regex:<pattern>``
    (spec §10.2). Returns the format name, or the compiled pattern for ``regex:``."""
    if fmt in FORMATS:
        return fmt
    if fmt.startswith("regex:"):
        try:
            return re.compile(fmt[6:])
        except re.error as e:
            raise ValueError("invalid InputFormat regex %r: %s" % (fmt[6:], e)) from None
    raise ValueError("InputFormat must be tsv, csv, lines or regex:<pattern>, got %r" % fmt)


def _split(text: str, fmt) -> Optional[Tuple[List[str], List[int], Optional[Tuple[str, ...]]]]:
    """Split one line into ``(fields, 0-based field offsets, regex names)``; ``None`` when
    a ``regex:`` format does not match."""
    if fmt == "tsv":
        fields = text.split("\t")
        offs, pos = [], 0
        for f in fields:
            offs.append(pos)
            pos += len(f) + 1
        return fields, offs, None
    if fmt == "csv":
        fields = next(csv.reader([text]))
        offs, pos = [], 0
        for f in fields:
            k = text.find(f, pos) if f else -1
            offs.append(k if k >= 0 else pos)
            pos = (k + len(f)) if k >= 0 else pos
        return fields, offs, None
    if fmt == "lines":
        return [text], [0], None
    m = fmt.match(text)
    if m is None:
        return None
    names = [""] * fmt.groups
    for name, idx in fmt.groupindex.items():
        names[idx - 1] = name
    fields = [g if g is not None else "" for g in m.groups()]
    offs = [max(m.start(k + 1), 0) for k in range(fmt.groups)]
    return fields, offs, tuple(names)


def _record(text: str, fmt, names: Optional[Tuple[str, ...]], source: str, line: int) -> Record:
    """Build a record from one data line (spec §10.2)."""
    split = _split(text, fmt)
    if split is None:
        rec = Record(text.strip(), (text,), names, raw=text, line=line, source=source)
        rec.error = LexiconError("line does not match the InputFormat regex %r" % fmt.pattern, rec.loc(),
                                 source_line=text, record=rec.label)
        return rec
    fields, offs, rx_names = split
    if rx_names is not None and names is None:
        names = rx_names
    k = names.index("form") if names and "form" in names else 0
    raw_form = fields[k] if k < len(fields) else ""
    form = raw_form.strip()
    col = (offs[k] if k < len(offs) else 0) + (len(raw_form) - len(raw_form.lstrip())) + 1
    rec = Record(form, tuple(fields), names, raw=text, line=line, source=source, form_col=col)
    if names:
        for name, value in zip(names, fields):
            if name not in SPECIAL_COLUMNS or name == "form":
                continue
            value = value.strip()
            c = offs[names.index(name)] + 1
            try:
                if name == "features":
                    rec.lexical = parse_features(value)
                elif name == "date":
                    rec.date = int(value) if value else None
                elif name == "dialect":
                    rec.dialect = value or None
                elif name == "paradigm":
                    rec.paradigm = value or None
            except ValueError as e:
                msg = str(e) if name == "features" else "the date column must be an integer, got %r" % value
                rec.error = LexiconError(msg, rec.loc(c, c + max(len(value), 1)), source_line=text,
                                         record=rec.label)
                break
    return rec


def read_records(lines: Iterable[str], fmt="tsv", source: str = "<input>") -> Iterator[Record]:
    """Read records from ``lines`` (spec §10.2). ``fmt`` is ``tsv``, ``csv``, ``lines``,
    ``regex:<pattern>`` or a compiled pattern. A ``#!`` header names the columns (it may be
    repeated to rename them); blank lines and ``%%`` lines are skipped. Records are numbered
    from 1 (``nr``); a malformed record carries ``error`` instead of stopping the reader."""
    if isinstance(fmt, str):
        fmt = parse_format(fmt)
    names: Optional[Tuple[str, ...]] = None
    nr = 0
    for number, text in enumerate(lines, 1):
        text = text.rstrip("\r\n")
        if number == 1 and text.startswith("﻿"):
            text = text[1:]
        stripped = text.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        if stripped.startswith("#!"):
            names = tuple(n for n in re.split(r"[\s,]+", stripped[2:].strip()) if n) or None
            continue
        nr += 1
        rec = _record(text, fmt, names, source, number)
        rec.nr = nr
        yield rec


def read_file(path: str, fmt="tsv") -> Iterator[Record]:
    """Read the records of the UTF-8 file ``path`` (spec §10.2); see :func:`read_records`."""
    with open(path, encoding="utf-8") as fh:
        yield from read_records(fh, fmt, path)


def word_records(words: Iterable[str]) -> Iterator[Record]:
    """Records for ad-hoc forms (CLI ``--word``, spec §11.1): each word is a one-field
    record from source ``<word>``."""
    for k, w in enumerate(words, 1):
        yield Record(w.strip(), (w,), None, raw=w, line=k, source="<word>", nr=k)
