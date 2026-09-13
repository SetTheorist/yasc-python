"""Tests for yasc.runtime and the public API (spec §8.10, §10, §11.2; plan P6)."""

import os
import re
import tempfile
import time
import unittest

import yasc
from yasc.errors import NotImplementedYet, YascLoadError, YascRuntimeError
from yasc.lexicon import read_records
from yasc.runtime import Runtime

ORTHO = """
  [p t k b d g f s x v z] {-Syll -Son -Nasal -Lateral}
  [p t k b d g] {-Cont}
  [f s x v z] {+Cont}
  [p t k f s x] {-Voice}
  [b d g v z] {+Voice}
  [m n] {-Syll +Nasal}
  [l r] {-Syll +Son +Cont +Voice -Nasal}
  [l] {+Lateral}
  [r] {-Lateral}
  [p b f v m] {!Labial}
  [t d s z n l r] {!Coronal}
  [k g x] {!Dorsal}
  [i e a o u] {+Syll}
  [i u] {+High}
  [e o] {-High -Low}
  [a] {+Low}
  [i e] {-Back}
  [a o u] {+Back}
"""

HDR = """$P := Phonology [[
  Syll Binary
  Son Binary
  Cont Binary
  Voice Binary
  Nasal Binary
  Lateral Binary
  Place Node(Labial Coronal Dorsal)
  Labial Unary
  Coronal Unary
  Dorsal Unary
  High Binary
  Low Binary
  Back Binary
  {+Syll} --> {+Son +Cont +Voice -Nasal -Lateral}
  {+Nasal} --> {+Son -Cont +Voice -Lateral}
  {+High} --> {-Low}
  {+Low} --> {-High}
]]
$X := Orthography [[%s]]
$O := Orthography [[%s]]
V === {+Syll}
C === {-Syll}
""" % (re.sub(r"\[([^\]]*)\]", lambda m: "[" + m.group(1).upper() + "]", ORTHO), ORTHO)


def script(body, prelude="", **kw):
    """``yasc.loads`` of HDR + prelude + ``$R := Rules [[ body ]]``."""
    lines = "\n".join("  " + x for x in body.strip("\n").split("\n"))
    return yasc.loads(HDR + prelude + "$R := Rules [[\n" + lines + "\n]]\n", **kw)


def record(line, fmt="tsv"):
    (rec,) = read_records([line], fmt)
    return rec


class OutputTests(unittest.TestCase):
    def test_default_output(self):
        res = script("[p] --> [f]").apply("pata")
        self.assertEqual(res.text, "pata\tfata\n")
        self.assertIsNone(res.printed)
        self.assertEqual([str(v) for v in res.outputs], ["fata"])
        self.assertEqual((res.outputs[0].label, res.outputs[0].dialect), (None, None))

    def test_print_directives(self):
        sc = script("$x := $_\n[p] --> [f]\n"
                    '!print "%I{0}|%O{1}|%O{2}|%F{3}|%F{4}|%F{5}|%L{2}|100%%\\t%I{6}\\n" $in $x $_ $field[2] $NF $NR $raw')
        res = sc.runtime.run(record("pata\tgloss"))
        self.assertEqual(res.text, "pata|pata|fata|gloss|2|1||100%\tpata\n")
        self.assertEqual(res.printed, res.text)

    def test_print_other_orthography_and_segments(self):
        sc = script('!print "%O[$X]{0} %I{0}\\n%S{0}\\n" $_')
        res = sc.apply("pa")
        segs = sc.input_orthography.parse("pa").segs
        self.assertEqual(res.text, "PA pa\n%s %s\n" % (segs[0].canonical(), segs[1].canonical()))

    def test_several_prints_concatenate(self):
        res = script('!print "a"\n[p] --> [f]\n!print "%O{0}\\n" $_').apply("pa")
        self.assertEqual(res.text, "afa\n")


class VariableTests(unittest.TestCase):
    def test_assignments(self):
        sc = script("$keep ::= $_\n[p] --> [f]\n$_ := [tu]\n"
                    '!print "%O{0} %O{1}\\n" $keep $_\n$_ := $keep\n!print "%O{0}\\n" $_')
        self.assertEqual(sc.apply("pa").text, "pa tu\npa\n")

    def test_field_assignment_is_parsed(self):
        sc = script("$_ := $field[2]\n[k] --> [x]")
        res = sc.runtime.run(record("pa\tku"))
        self.assertEqual(res.text, "pa\txu\n")
        self.assertEqual(sc.runtime.run(record("pa")).text, "pa\t\n")

    def test_unset_variable_is_record_error(self):
        sc = script('!print "%O{0}" $nope')
        with self.assertRaises(YascRuntimeError) as cm:
            sc.apply("pa")
        self.assertIn("$nope is not set", cm.exception.message)

    def test_variables_do_not_leak_between_records(self):
        sc = script('!print "%O{0}\\n" $_\n$seen := $_')
        rt = sc.runtime
        self.assertIsNone(rt.run(record("pa")).error)
        self.assertIsNone(rt.run(record("ta")).error)

    def test_top_level_commands_and_assignments(self):
        sc = yasc.loads(HDR + "$w := $_\n$R := Rules [[\n  [p] --> [f]\n]]\n!print \"%O{0}>%O{1}\\n\" $w $_\n")
        self.assertEqual(sc.apply("pa").text, "pa>fa\n")


class CommandTests(unittest.TestCase):
    def test_set_default_mode(self):
        self.assertEqual(script("[p] --> [f]").apply("papa").text, "papa\tfafa\n")
        sc = script("!set DefaultMode = once\n[p] --> [f]")
        self.assertEqual(sc.apply("papa").text, "papa\tfapa\n")
        self.assertEqual(sc.apply("papa").text, "papa\tfapa\n")

    def test_set_max_iterations(self):
        with self.assertRaises(YascRuntimeError) as cm:
            script("!set MaxIterations = 2\n[a] --> [e] /*").apply("papapapa")
        self.assertIn("MaxIterations = 2", cm.exception.message)

    def test_set_trace_and_seed_and_constraints(self):
        res = script("!set Seed = 7\n!set EnforceConstraints = on\n!set Trace = on\n[p] --> [f]").apply("pa")
        self.assertEqual([s.name for s in res.trace], [None])
        self.assertEqual(res.trace[0].foci, ((0, 1),))

    def test_input_settings_at_top_level(self):
        sc = script("[p] --> [f]", prelude="!set OnUnparsable = skip\n!set InputFormat = csv\n")
        self.assertEqual(sc.apply("pa!ta").text, "pa!ta\tfata\n")
        self.assertEqual(sc.runtime.input_format, "csv")
        with self.assertRaises(YascRuntimeError) as cm:
            script("[p] --> [f]").apply("pa!ta")
        self.assertIn("cannot parse '!'", cm.exception.message)

    def test_orthography_and_use(self):
        self.assertEqual(script("[p] --> [f]\n!orthography output $X").apply("pa").text, "pa\tFA\n")
        self.assertEqual(script('!use $X\n!print "%O{0}\\n" $_').apply("pa").text, "PA\n")

    def test_only_and_skip(self):
        rules = '[p] --> [f] /" One\n[t] --> [s] /" Two'
        self.assertEqual(script("!skip Two\n" + rules).apply("pata").text, "pata\tfata\n")
        self.assertEqual(script("!only Two\n" + rules).apply("pata").text, "pata\tpasa\n")
        sc = script(rules, skip=["One"])
        self.assertEqual(sc.apply("pata").text, "pata\tpasa\n")

    def test_assert(self):
        sc = script('[p] --> [f]\n!assert "fata"')
        self.assertEqual(sc.apply("pata").text, "pata\tfata\n")
        with self.assertRaises(YascRuntimeError) as cm:
            sc.apply("papa")
        err = cm.exception
        self.assertIn("assertion failed: expected 'fata', got 'fafa'", err.message)
        self.assertEqual(err.loc.line, HDR.count("\n") + 3)
        self.assertEqual(err.record, "papa")


DATED = '[e] --> [i] /" Undated\n!date 1900\n[t] --> [d] /" Early\n!date 1960\n[v] --> [b] /" Late'


class DateTests(unittest.TestCase):
    """Spec §8.10 and the wishlist scenario: a record ``television 1954`` escapes a rule
    dated 1900 but gets one dated 1960; undated rules never apply to a dated record."""

    def test_television_1954(self):
        sc = script(DATED)
        self.assertEqual(str(sc.apply("television").outputs[0]), "dilibision")
        res = sc.apply("television", date=1954)
        self.assertEqual(str(res.outputs[0]), "telebision")
        self.assertTrue(any("undated" in w for w in res.warnings))
        self.assertEqual(str(sc.apply("television", date=1960).outputs[0]), "television")
        self.assertEqual(str(sc.apply("television", date=-5000).outputs[0]), "telebision".replace("te", "de"))

    def test_date_column(self):
        res = script(DATED).runtime.run(record("television\t1954", r"regex:(?P<form>\w+)\t(?P<date>\d+)"))
        self.assertEqual(res.text, "television\ttelebision\n")

    def test_from_to_window(self):
        def out(window, date=None):
            return str(script(DATED, date_window=window).apply("television", date=date).outputs[0])
        self.assertEqual(out((1950, None)), "telebision")        # --from: undated excluded
        self.assertEqual(out((None, 1950)), "dilivision")        # --to only: undated included
        self.assertEqual(out((1900, 1900)), "delevision")
        self.assertEqual(out((1900, 1950), date=1954), "television")
        self.assertEqual(out((None, None)), "dilibision")

    def test_group_date_is_inherited(self):
        sc = script("[[\n  [t] --> [d]\n]] /:@1960")
        self.assertEqual(str(sc.apply("tata", date=1954).outputs[0]), "dada")
        self.assertEqual(str(sc.apply("tata", date=1970).outputs[0]), "tata")
        self.assertEqual(str(script("[[\n  [t] --> [d]\n]] /:@1960", date_window=(None, 1950))
                             .apply("tata").outputs[0]), "tata")

    def test_date_allows_table(self):
        from yasc.rules import ApplyContext, RecordData
        cases = [  # (record date, window, rule date, allowed)
            (None, None, None, True), (None, None, 5, True),
            (1954, None, None, False), (1954, None, 1900, False), (1954, None, 1954, False), (1954, None, 1960, True),
            (None, (100, None), None, False), (None, (100, None), 99, False), (None, (100, None), 100, True),
            (None, (None, 100), None, True), (None, (None, 100), 100, True), (None, (None, 100), 101, False),
            (None, (100, 200), 150, True), (-10, (-50, 0), -20, False), (-10, (-50, 0), -5, True),
        ]
        for rec, win, date, ok in cases:
            ctx = ApplyContext(record=RecordData(date=rec), date_window=win)
            self.assertEqual(ctx.date_allows(date), ok, (rec, win, date))
        ctx = ApplyContext(record=RecordData(date=1954))
        ctx.group_date = 1960
        self.assertTrue(ctx.date_allows(None))
        self.assertFalse(ctx.date_allows(1900))

    def test_commands_are_not_dated(self):
        sc = script('!date 1900\n[t] --> [d]\n$x := $_\n!print "%O{0}\\n" $x')
        self.assertEqual(sc.apply("ta", date=2000).text, "ta\n")


class LexicalTests(unittest.TestCase):
    def test_lexical_features(self):
        sc = script("[p] --> [f] /:L+ {!N}\n[t] --> [s] /:L- {+Old}")
        self.assertEqual(str(sc.apply("pata").outputs[0]), "pasa")
        self.assertEqual(str(sc.apply("pata", features="!N +Old").outputs[0]), "fata")
        self.assertEqual(str(sc.apply("pata", features={"N": "!", "Old": "-"}).outputs[0]), "fasa")

    def test_features_column(self):
        sc = script("[p] --> [f] /:L+ {!N}")
        res = sc.runtime.run(record("pa\t!N +Romance", r"regex:(?P<form>\w+)\t(?P<features>.*)"))
        self.assertEqual(res.text, "pa\tfa\n")


class PendingAndErrorTests(unittest.TestCase):
    def test_strict_load_accepts_p9(self):
        # Plan P9: /% and /??? are no longer pending; they compile and run strictly.
        self.assertEqual(script("[p] --> [f] /%100").apply("pata").text, "pata\tfata\n")

    def test_allow_pending_skips_and_warns_once(self):
        # The pending mechanism is phase-independent: mark rule 1 pending by hand.
        import dataclasses
        from yasc.runtime import Script
        c = script("[p] --> [f]\n[t] --> [d]").compiled
        sec = c.rule_sections["R"]
        c.statements[c.statements.index(sec)] = dataclasses.replace(
            sec, members=(dataclasses.replace(sec.members[0], pending=("PX",)),) + tuple(sec.members[1:]))
        sc = Script(c, allow_pending=True)
        first = sc.apply("pata", trace=True)
        self.assertEqual(first.text, "pata\tpada\n")
        self.assertEqual(first.warnings, ["skipped rule 1 (needs plan phase PX)"])
        self.assertEqual(first.trace[0].note, "skipped: needs plan phase PX")
        self.assertEqual(sc.apply("pata").warnings, [])

    def test_errors_stay_with_their_record(self):
        rt = script("[p] --> [f]").runtime
        bad, good = rt.run(record("pa!")), rt.run(record("pa"))
        self.assertIsInstance(bad.error, YascRuntimeError)
        self.assertIn("pa!", bad.to_dict()["error"])
        self.assertEqual((bad.outputs, bad.text), ([], ""))
        self.assertIsNone(good.error)
        self.assertEqual(good.text, "pa\tfa\n")


class ApiTests(unittest.TestCase):
    def test_script_properties(self):
        sc = script('[p] --> [f] /" Spirant\n!date 5\n[t] --> [s]')
        self.assertEqual([(r.id, r.name, r.date) for r in sc.rules], [(1, "Spirant", None), (2, None, 5)])
        self.assertEqual(sc.rules[0].source, '[p] --> [f] /" Spirant')
        self.assertIn("Syll", [f.name for f in sc.phonology.features])
        self.assertEqual(sc.orthography.render(sc.input_orthography.parse("pa")), "pa")
        self.assertEqual(sc.warnings, [])

    def test_result_and_trace(self):
        res = script('[p] --> [f] /" Spirant').apply("papa", trace=True)
        self.assertTrue(res.ok)
        self.assertEqual([(s.rule_id, s.name, s.foci) for s in res.trace], [(1, "Spirant", ((0, 1), (2, 3)))])
        d = res.to_dict()
        self.assertEqual(list(d), ["input", "nr", "source", "outputs", "printed", "trace", "warnings"])
        self.assertEqual(d["trace"][0]["before"], "papa")
        self.assertEqual(d["trace"][0]["after"], "fafa")
        self.assertEqual(repr(res.outputs[0]), "<Variant 'fafa'>")

    def test_load_file_and_determinism(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.yasc")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(HDR + "$R := Rules [[\n  [p] --> [f]\n]]\n")
            sc = yasc.load(path)
        rt = Runtime(sc.compiled, trace=True)
        a = [rt.run(r).to_dict() for r in read_records(["pata", "tapa"])]
        b = [rt.run(r).to_dict() for r in read_records(["pata", "tapa"])]
        self.assertEqual(a, b)


@unittest.skipIf(os.environ.get("YASC_SKIP_PERF"), "YASC_SKIP_PERF is set")
class PerformanceTests(unittest.TestCase):
    """Plan P6 acceptance: 1,000 records x 100 simple rules through the full runtime
    (lexicon reading, rules, output) in 10 s or less."""

    def test_1000_records_100_rules(self):
        import contextlib
        import io
        import random
        from yasc.cli import main
        cons, vows = "ptkbdgfsxvzmnlr", "aeiou"
        ctxs = ["/ V ___ V", "/ ___ #", "/ # ___", "/ ___ C", "/ C ___", ""]
        rules, i = [], 0
        while len(rules) < 90:
            a, b = cons[i % 15], cons[(i * 7 + 3) % 15]
            if a != b:
                rules.append("[%s] --> [%s] %s" % (a, b, ctxs[i % len(ctxs)]))
            i += 1
        for k in range(10):
            rules.append("[%s] --> [%s] / ___ C* [%s]" % (vows[k % 5], vows[(k + 1) % 5], vows[(k + 2) % 5]))
        rng = random.Random(1234)
        words = ["".join(rng.choice(cons) + rng.choice(vows) for _ in range(rng.randint(2, 4)))
                 + (rng.choice(cons) if rng.random() < 0.5 else "") for _ in range(1000)]
        with tempfile.TemporaryDirectory() as d:
            spath, lpath, opath = (os.path.join(d, n) for n in ("perf.yasc", "perf.tsv", "perf.out"))
            with open(spath, "w", encoding="utf-8") as fh:
                fh.write(HDR + "$R := Rules [[\n" + "\n".join("  " + r for r in rules) + "\n]]\n")
            with open(lpath, "w", encoding="utf-8") as fh:
                fh.write("#! form gloss\n" + "".join("%s\tw%d\n" % (w, n) for n, w in enumerate(words)))
            err = io.StringIO()
            t0 = time.perf_counter()
            with contextlib.redirect_stderr(err):
                status = main([spath, lpath, "-o", opath])
            elapsed = time.perf_counter() - t0
            with open(opath, encoding="utf-8") as fh:
                n_lines = len(fh.read().splitlines())
        print("\n[perf] full runtime: 1000 records x %d rules in %.2f s" % (len(rules), elapsed))
        self.assertEqual((status, n_lines), (0, 1000), err.getvalue()[:2000])
        self.assertLessEqual(elapsed, 10.0)
