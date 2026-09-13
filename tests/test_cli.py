"""Tests for yasc.cli and ``python -m yasc`` (spec §11.1, §12; plan P6).

End-to-end runs of ``examples/simple/`` are compared with the expected-output files in
``tests/data/`` (text, ``--trace``, ``--from``/``--to``, ``--list-rules`` and ``--json``).
Paths are given relative to the project root, which the helpers make the working directory.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from yasc.cli import build_arg_parser, main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
GRIMM = "examples/simple/grimm.yasc"
LEX = "examples/simple/lexicon.tsv"
PROTO = "examples/dialects/proto.yasc"          # plan P9
PROTO_LEX = "examples/dialects/lexicon.tsv"


def run_main(argv, stdin=None):
    """Call main(argv) from the project root and capture (status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    cwd = os.getcwd()
    old_stdin = sys.stdin
    try:
        os.chdir(ROOT)
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(argv)
    finally:
        os.chdir(cwd)
        sys.stdin = old_stdin
    return status, out.getvalue(), err.getvalue()


def expected(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return fh.read()


def temp_file(d, name, text):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class ParserTests(unittest.TestCase):
    def test_help_exit_status_zero(self):
        status, out, _ = run_main(["--help"])
        self.assertEqual(status, 0)
        for option in ("SCRIPT", "LEXICON", "-o", "--trace", "--json", "--from", "--to", "--dialect", "--wide",
                       "--only", "--skip", "--check", "--list-rules", "--word", "--profile", "--allow-pending"):
            self.assertIn(option, out)

    def test_missing_script_is_usage_error(self):
        status, _, err = run_main([])
        self.assertEqual(status, 2)
        self.assertIn("usage:", err)

    def test_parser_options(self):
        args = build_arg_parser().parse_args(
            ["s.yasc", "a.tsv", "b.tsv", "-o", "out.txt", "--trace", "--json", "--from", "-50",
             "--to", "400", "--dialect", "West", "--list-rules", "--profile", "--allow-pending",
             "--only", "R1", "R2", "--skip", "R3", "--word", "pater", "mater"])
        self.assertEqual((args.script, args.lexicons, args.output), ("s.yasc", ["a.tsv", "b.tsv"], "out.txt"))
        self.assertTrue(args.trace and args.json and args.list_rules and args.profile and args.allow_pending)
        self.assertFalse(args.check or args.wide)
        self.assertEqual((args.date_from, args.date_to, args.dialect), (-50, 400, "West"))
        self.assertEqual((args.only, args.skip, args.word), (["R1", "R2"], ["R3"], ["pater", "mater"]))

    def test_multi_value_option_placement(self):
        parser = build_arg_parser()
        after = parser.parse_args(["s.yasc", "a.tsv", "--only", "A", "B"])
        self.assertEqual((after.script, after.lexicons, after.only), ("s.yasc", ["a.tsv"], ["A", "B"]))
        before = parser.parse_args(["--only", "A", "--", "s.yasc", "a.tsv"])
        self.assertEqual((before.script, before.lexicons, before.only), ("s.yasc", ["a.tsv"], ["A"]))


class SimpleExampleTests(unittest.TestCase):
    """examples/simple/ against tests/data/simple-grimm.* (plan P6 acceptance)."""

    def check(self, argv, data_file):
        status, out, err = run_main(argv)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, expected(data_file))
        return out

    def test_text_output(self):
        self.check([GRIMM, LEX], "simple-grimm.out")

    def test_trace(self):
        self.check([GRIMM, LEX, "--trace"], "simple-grimm-trace.out")

    def test_date_window(self):
        self.check([GRIMM, LEX, "--from", "-450", "--to", "150"], "simple-grimm-window.out")

    def test_list_rules(self):
        out = self.check([GRIMM, "--list-rules"], "simple-grimm-rules.out")
        self.assertEqual(len(out.splitlines()), 7)

    def test_json_output_and_schema(self):
        out = self.check([GRIMM, LEX, "--json"], "simple-grimm.jsonl")
        objs = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(len(objs), 9)
        for o in objs:
            self.assertEqual(list(o), ["input", "nr", "source", "outputs", "printed", "trace", "warnings"])
            self.assertIsInstance(o["input"], str)
            self.assertIsInstance(o["nr"], int)
            for v in o["outputs"]:
                self.assertEqual(list(v), ["form", "label", "dialect", "approximate"])
                self.assertIsInstance(v["form"], str)
                self.assertIsInstance(v["approximate"], list)
            self.assertIsInstance(o["warnings"], list)
        self.assertEqual(objs[3]["outputs"][0]["form"], "gastiz")

    def test_json_trace_entries(self):
        status, out, _ = run_main([GRIMM, "--json", "--trace", "--word", "pater"])
        self.assertEqual(status, 0)
        (o,) = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(o["trace"], [{"rule_id": 1, "name": "GrimmVoiceless", "line": 72, "before": "pater",
                                       "after": "faθer", "foci": [[0, 1], [2, 3]], "note": None}])

    def test_list_rules_json(self):
        status, out, _ = run_main([GRIMM, "--list-rules", "--json"])
        rules = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["name"] for r in rules][:2], ["GrimmVoiceless", "GrimmVoiced"])
        self.assertEqual(rules[5]["date"], 300)

    def test_word_only_skip_and_output_file(self):
        status, out, _ = run_main([GRIMM, "--word", "pater", "porta", "--only", "OToA"])
        self.assertEqual(out, "pater\tpater\tpater\t\nporta\tporta\tparta\t\n")
        status, out, _ = run_main([GRIMM, "--word", "pater", "--skip", "GrimmVoiceless"])
        self.assertEqual(out, "pater\tpater\tpater\t\n")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt")
            status, out, _ = run_main([GRIMM, LEX, "-o", path])
            self.assertEqual((status, out), (0, ""))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), expected("simple-grimm.out"))

    def test_stdin(self):
        status, out, _ = run_main([GRIMM], stdin="#! form gloss\ngenu\tknee\n")
        self.assertEqual((status, out), (0, "genu\tgenu\tkenu\tknee\n"))

    def test_check(self):
        status, out, err = run_main([GRIMM, "--check"])
        self.assertEqual((status, err), (0, ""))
        self.assertIn("OK: 6 rules", out)

    def test_profile(self):
        status, out, err = run_main([GRIMM, LEX, "--profile"])
        self.assertEqual(status, 0)
        self.assertEqual(out, expected("simple-grimm.out"))
        self.assertIn("GrimmVoiceless", err)
        self.assertIn("profile: total", err)


class ErrorTests(unittest.TestCase):
    def test_bad_records_are_reported_and_processing_continues(self):
        with tempfile.TemporaryDirectory() as d:
            lex = temp_file(d, "lex.tsv", "#! form gloss date\npater\tfather\ngen!u\tknee\ntris\tthree\tlate\n"
                                          "genu\tknee\n")
            status, out, err = run_main([GRIMM, lex])
        self.assertEqual(status, 2)
        self.assertEqual(out, "pater\tpater\tfaθer\tfather\ngenu\tgenu\tkenu\tknee\n")
        self.assertIn("lex.tsv:3:4: error: cannot parse '!'", err)
        self.assertIn("lex.tsv:4:", err)
        self.assertIn("the date column must be an integer", err)

    def test_assert_failure_is_a_record_error(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(ROOT, GRIMM), encoding="utf-8") as fh:
                text = fh.read()
            script = temp_file(d, "s.yasc", text.replace("  !print", '  !assert "kenu"\n  !print'))
            status, out, err = run_main([script, "--word", "genu", "pater"])
        self.assertEqual(status, 2)
        self.assertEqual(out, "genu\tgenu\tkenu\t\n")
        self.assertIn("assertion failed: expected 'kenu', got 'faθer'", err)
        self.assertIn("for record 'pater (<word>:2)'", err)

    def test_load_errors_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            script = temp_file(d, "bad.yasc", "$P := Phonology [[\n  Syll Binary\n]]\n"
                                              "$O := Orthography [[\n  [a] {+Syll +Foo}\n  [p] {-Sill}\n]]\n")
            status, out, err = run_main([script, "--check"])
        self.assertEqual((status, out), (1, ""))
        self.assertIn("unknown feature 'Foo'", err)
        self.assertIn("unknown feature 'Sill'", err)
        status, _, err = run_main(["no/such/script.yasc"])
        self.assertEqual(status, 1)
        self.assertIn("cannot read script", err)

    def test_missing_lexicon_exit_2(self):
        status, _, err = run_main([GRIMM, "no-such-lexicon.tsv"])
        self.assertEqual(status, 2)
        self.assertIn("cannot read lexicon", err)

    def test_dialect_option_errors_exit_2(self):
        # Plan P9: --wide needs declared dialects; --dialect must name a declared one.
        status, out, err = run_main([GRIMM, LEX, "--wide"])
        self.assertEqual((status, out), (2, ""))
        self.assertIn("--wide needs a script that declares its dialects", err)
        status, out, err = run_main([PROTO, PROTO_LEX, "--dialect", "North"])
        self.assertEqual((status, out), (2, ""))
        self.assertIn("unknown dialect 'North'", err)

    def test_pending_constructs(self):
        # Plan P8: nothing is pending any more, so Appendix B loads and runs strictly.
        script = "examples/revised-example.yasc"
        status, out, err = run_main([script, "--check"])
        self.assertEqual(status, 0, err)
        self.assertIn("OK", out)
        status, out, err = run_main([script, "--word", "tabi", "papi"])
        self.assertEqual((status, out), (0, "tabi > dab\npapi > pap\n"))
        self.assertNotIn("skipped", err)


class DialectCliTests(unittest.TestCase):
    """Plan P9: --wide, --dialect, --paradigm and the JSON label/dialect fields on
    examples/dialects/ and examples/paradigm/ (spec §10.4, §11.1)."""

    def test_wide_expected_output(self):
        status, out, err = run_main([PROTO, PROTO_LEX, "--wide"])
        self.assertEqual(status, 0, err)
        with open(os.path.join(ROOT, "examples", "dialects", "expected-wide.out"), encoding="utf-8") as fh:
            self.assertEqual(out, fh.read())

    def test_dialect_option(self):
        status, out, _ = run_main([PROTO, PROTO_LEX, "--dialect", "East"])
        self.assertEqual(status, 0)
        self.assertEqual(out.splitlines()[:2], ["pater\tpater\tEast", "kine\ttini\tEast"])
        self.assertEqual(len(out.splitlines()), 6)
        status, out, _ = run_main([PROTO, PROTO_LEX, "--dialect", "West", "--wide"])
        self.assertEqual(out.splitlines()[0], "#! form\tWest")
        self.assertEqual(out.splitlines()[-1], "tokit\t")

    def test_json_label_and_dialect(self):
        status, out, _ = run_main([PROTO, "--json", "--word", "kine"])
        self.assertEqual(status, 0)
        d = json.loads(out)
        self.assertEqual([(o["form"], o["label"], o["dialect"]) for o in d["outputs"]],
                         [("kini", None, "West"), ("tini", None, "East")])
        status, out, _ = run_main(["examples/paradigm/declension.yasc", "--json", "--paradigm", "Second",
                                   "--word", "lup"])
        self.assertEqual(status, 0)
        d = json.loads(out)
        self.assertEqual([(o["form"], o["label"], o["dialect"]) for o in d["outputs"]][:2],
                         [("lup+os", "Nom.Sg|Acc.Pl", None), ("lup+u", "Acc.Sg", None)])


class ModuleTests(unittest.TestCase):
    def test_python_m_yasc(self):
        proc = subprocess.run([sys.executable, "-m", "yasc", GRIMM, LEX], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.decode("utf-8"), expected("simple-grimm.out"))
        proc = subprocess.run([sys.executable, "-m", "yasc", "--help"], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: python -m yasc", proc.stdout)


if __name__ == "__main__":
    unittest.main()
