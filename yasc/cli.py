"""Command-line interface: ``python -m yasc SCRIPT [LEXICON ...]`` (spec §11.1, plan P6).

:func:`main` loads the script once, then runs it on every record of the lexica (or of
standard input, or of the ``--word`` forms) and writes one result per record:

* text (default): what the script's ``!print`` wrote, or ``input<TAB>output`` per variant;
  with ``--trace`` each record is preceded by ``== input`` and its trace lines,
  ``rule-id  name  line  before → after  (focus i..j)`` (indented by two spaces);
* ``--json``: one JSON object per line; the schema is documented on
  :meth:`yasc.runtime.Result.to_dict`.

Exit status (spec §12): 0 on success; 1 when the script fails to load; 2 on usage errors
(including options of later phases) or when any record failed. A record's error is reported
on stderr and processing continues with the next record.
"""

import argparse
import itertools
import json
import os
import sys
from typing import List, Optional, TextIO

from . import __version__
from .errors import YascError

#: Exit status for "some records failed" and usage errors (spec §12).
EXIT_FAILURE = 2
#: Exit status for a script that fails to load (spec §12).
EXIT_LOAD_ERROR = 1


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the options listed in spec §11.1."""
    parser = argparse.ArgumentParser(
        prog="python -m yasc",
        description="YASC %s: apply a sound-change script to a lexicon." % __version__,
        epilog="Options that take several values (--only, --skip, --word) consume all the "
        "arguments that follow them. Either put them after SCRIPT and the LEXICON files, or "
        "put SCRIPT and the LEXICON files after '--'.",
    )
    parser.add_argument("script", metavar="SCRIPT", help="the .yasc script to run")
    parser.add_argument(
        "lexicons", metavar="LEXICON", nargs="*",
        help="input lexicon files (default: standard input)",
    )
    parser.add_argument("-o", dest="output", metavar="OUT", help="write output to OUT instead of stdout")
    parser.add_argument(
        "--trace", action="store_true",
        help="print every rule application that changed a form",
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON object per record")
    parser.add_argument(
        "--from", dest="date_from", metavar="A", type=int,
        help="only run rules dated A or later (undated rules are then excluded)",
    )
    parser.add_argument("--to", dest="date_to", metavar="B", type=int, help="only run rules dated B or earlier")
    parser.add_argument("--dialect", metavar="D", help="produce only dialect D (spec §10.4)")
    parser.add_argument("--wide", action="store_true",
                        help="one output column per dialect declared by !dialects (spec §10.4)")
    parser.add_argument("--paradigm", metavar="NAME",
                        help="expand records without a paradigm column with the Paradigm $NAME (spec §9)")
    parser.add_argument(
        "--only", metavar="NAME", nargs="+", action="extend", default=[],
        help="run only the rules with these names",
    )
    parser.add_argument(
        "--skip", metavar="NAME", nargs="+", action="extend", default=[],
        help="skip the rules with these names",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="parse and compile the script, report every error, and exit",
    )
    parser.add_argument(
        "--list-rules", action="store_true",
        help="print each rule's number, name, date, source line and canonical text",
    )
    parser.add_argument(
        "--word", metavar="FORM", nargs="+", action="extend", default=[],
        help="run these ad-hoc forms instead of a lexicon",
    )
    parser.add_argument("--profile", action="store_true", help="report the time spent in each rule on stderr")
    parser.add_argument(
        "--allow-pending", action="store_true",
        help="skip constructs that are not implemented, with a warning (none are since P9; "
             "kept for compatibility)",
    )
    return parser


def _where(loc, script: str) -> str:
    if loc is None:
        return "-"
    same = os.path.realpath(loc.file) == os.path.realpath(script) if os.path.exists(loc.file) else loc.file == script
    return str(loc.line) if same else "%s:%d" % (loc.file, loc.line)


def list_rules(compiled, out: TextIO, as_json: bool = False) -> None:
    """``--list-rules`` (spec §11.1): one line per rule, tab-separated after a ``#!`` header:
    number, name, date, source line, pending phase and canonical text (with ``--json``, one
    object per rule)."""
    if not as_json:
        out.write("#! id\tname\tdate\tline\tpending\trule\n")
    for r in compiled.rules:
        pend = getattr(r, "phase", None) or ",".join(getattr(r, "pending", ()) or ())
        if as_json:
            out.write(json.dumps({"id": r.id, "name": r.name, "date": r.date,
                                  "file": r.loc.file if r.loc else None, "line": r.loc.line if r.loc else None,
                                  "pending": pend or None, "source": r.source}, ensure_ascii=False) + "\n")
        else:
            out.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (r.id, r.name or "-", "-" if r.date is None else r.date,
                                                     _where(r.loc, compiled.filename), pend or "-", r.source))


def _records(args, fmt):
    from .lexicon import read_file, read_records, word_records
    if args.word:
        return word_records(args.word)
    if args.lexicons:
        return itertools.chain.from_iterable(read_file(p, fmt) for p in args.lexicons)
    return read_records(sys.stdin, fmt, "<stdin>")


def _report_profile(runtime, profile, err: TextIO) -> None:
    """``--profile``: time per rule, slowest first (design §10)."""
    names = {r.id: r.name for r in runtime.compiled.rules}
    err.write("profile: id\tname\tcalls\tapplied\tseconds\n")
    for rid, (calls, applied, secs) in sorted(profile.items(), key=lambda kv: (-kv[1][2], kv[0])):
        err.write("profile: %s\t%s\t%d\t%d\t%.4f\n" % (rid, names.get(rid) or "-", calls, applied, secs))
    err.write("profile: total\t\t\t\t%.4f\n" % sum(c[2] for c in profile.values()))


def _emit(res, args, out: TextIO, err: TextIO, columns: Optional[List[str]] = None) -> None:
    """Write one record's result, its warnings and its error (spec §11.1, §12). With
    ``columns`` (``--wide``, spec §10.4) the text output is one row per record with one
    column per dialect."""
    from .runtime import format_trace
    rec = res.record
    where = "%s:%d" % (rec.source, rec.line) if rec.line else rec.source
    if args.json:
        out.write(json.dumps(res.to_dict(), ensure_ascii=False) + "\n")
    else:
        if args.trace:
            out.write("== %s\n" % res.input)
            for step in res.trace:
                out.write("  %s\n" % format_trace(step, res.orthography))
        if res.error is None:
            out.write(res.wide(columns) if columns is not None else res.text)
    for w in res.warnings:
        err.write("%s: warning: %s\n" % (where, w))
    if res.error is not None:
        err.write(res.error.format() + "\n")


def run(args, out: TextIO, err: TextIO) -> int:
    """Load the script and process the records per ``args`` (spec §11.1); returns the exit
    status (spec §12)."""
    from .compile import compile_file
    from .runtime import Runtime
    for p in args.lexicons:
        if not os.path.isfile(p):
            err.write("yasc: cannot read lexicon %r: no such file\n" % p)
            return EXIT_FAILURE
    try:
        compiled = compile_file(args.script, allow_unimplemented=args.allow_pending or args.list_rules)
    except YascError as e:
        err.write(e.format() + "\n")
        return EXIT_LOAD_ERROR
    except OSError as e:
        err.write("yasc: cannot read script %r: %s\n" % (args.script, e.strerror or e))
        return EXIT_LOAD_ERROR
    if not args.list_rules:
        for w in compiled.warnings:
            err.write(w.format() + "\n")
    if args.check:
        out.write("%s: OK: %d rules, %d warnings\n" % (args.script, len(compiled.rules), len(compiled.warnings)))
        return 0
    if args.list_rules:
        list_rules(compiled, out, args.json)
        return 0
    runtime = Runtime(compiled, date_window=(args.date_from, args.date_to), only=args.only, skip=args.skip,
                      allow_pending=args.allow_pending, trace=args.trace, dialect=args.dialect,
                      paradigm=args.paradigm)
    names = runtime.dialect_names
    if args.dialect is not None and names and args.dialect not in names:
        err.write("yasc: unknown dialect %r: the script declares %s (spec §10.4)\n" % (args.dialect, " ".join(names)))
        return EXIT_FAILURE
    columns = None
    if args.wide:
        if not names:
            err.write("yasc: --wide needs a script that declares its dialects with !dialects (spec §10.4)\n")
            return EXIT_FAILURE
        columns = [args.dialect] if args.dialect is not None else list(names)
        if not args.json:
            out.write("#! form\t%s\n" % "\t".join(columns))
    profile = runtime.enable_profile() if args.profile else None
    status = 0
    try:
        for nr, rec in enumerate(_records(args, runtime.input_format), 1):
            rec.nr = nr
            res = runtime.run(rec)
            _emit(res, args, out, err, columns)
            if res.error is not None:
                status = EXIT_FAILURE
    except (OSError, UnicodeDecodeError) as e:
        err.write("yasc: cannot read the input: %s\n" % e)
        return EXIT_FAILURE
    if profile is not None:
        _report_profile(runtime, profile, err)
    return status


def main(argv: Optional[List[str]] = None) -> int:
    """Run the CLI (spec §11.1) and return the process exit status (spec §12).

    ``argv`` defaults to ``sys.argv[1:]``. Argument errors and ``--help`` return argparse's
    status (2 and 0) instead of raising :class:`SystemExit` (design §13 entry 9).
    """
    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else EXIT_FAILURE
    if args.output:
        try:
            out = open(args.output, "w", encoding="utf-8", newline="")
        except OSError as e:
            print("yasc: cannot write %r: %s" % (args.output, e.strerror or e), file=sys.stderr)
            return EXIT_FAILURE
        try:
            return run(args, out, sys.stderr)
        finally:
            out.close()
    return run(args, sys.stdout, sys.stderr)
