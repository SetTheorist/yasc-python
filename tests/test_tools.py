"""The P10 tools: ``yasc.tools.minimize`` and ``yasc.tools.make_ortho`` (plan P10; design §8
R20 and §13 entries 167–168)."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

from yasc.compile import compile_source
from yasc.tools import make_ortho as mo
from yasc.tools import minimize as mz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Two features, four phones: every feature is needed (ret.py's "all features" bug, R20).
ALL_NEEDED = """
$P := Phonology [[
  A Binary
  B Binary
  C Binary
]]
$O := Orthography [[
  [p q r s] {+C}
  [p r] {+A}
  [q s] {-A}
  [q r] {+B}
  [p s] {-B}
]]
"""


def _toy(text=ALL_NEEDED):
    c = compile_source(text, "<toy>")
    return mz.phones_of(c.orthography)


def _run(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = main(argv)
    return status, out.getvalue(), err.getvalue()


class MinimizeTests(unittest.TestCase):
    def test_r20_forced_features_do_not_crash(self):
        # ret.py: features_used = [].extend(forced) is None, so the first append crashed.
        res = mz.minimize(_toy(), forced=["A"])
        self.assertEqual(res.forced, ("A",))
        self.assertEqual(res.features[0], "A")
        self.assertEqual(set(res.features), {"A", "B"})

    def test_r20_every_feature_needed(self):
        # ret.py stopped with best_feature = None appended when all features were needed.
        res = mz.minimize(_toy())
        self.assertEqual(sorted(res.features), ["A", "B"])
        self.assertNotIn(None, res.features)
        self.assertEqual(len(set(res.bundles.values())), 4)

    def test_constant_feature_is_predictable(self):
        res = mz.minimize(_toy())
        self.assertEqual(res.predictable["C"], ((), [("", "+C")]))

    def test_forced_alias_and_errors(self):
        c = mz.load("lib:ipa.yasc")
        phones = mz.phones_of(c.orthography, ["p", "b"])
        self.assertEqual(mz.minimize(phones, forced=["voiced"]).features, ("Voice",))
        with self.assertRaises(ValueError):
            mz.minimize(phones, forced=["Nope"])
        with self.assertRaises(ValueError):
            mz.minimize(phones, forced=["Place"])        # a Node is never a candidate
        with self.assertRaises(ValueError):
            mz.phones_of(c.orthography, ["pa"])          # two segments

    def test_minimal_on_a_library_inventory(self):
        c = mz.load("lib:ipa.yasc")
        phones = mz.phones_of(c.orthography, "p t k b d g m n s a i u".split())
        res = mz.minimize(phones)
        fs = c.phonology
        idx = [fs.get(f).index for f in res.features]
        keys = {tuple(s.values[i] for i in idx) for _, s in phones}
        self.assertEqual(len(keys), len(phones))
        for drop in idx:                                 # every chosen feature is needed
            rest = [i for i in idx if i != drop]
            self.assertLess(len({tuple(s.values[i] for i in rest) for _, s in phones}), len(phones))
        self.assertEqual(res.to_dict(), mz.minimize(phones).to_dict())   # deterministic

    def test_identical_bundles_are_reported(self):
        c = mz.load("lib:ipa.yasc")
        res = mz.minimize(mz.phones_of(c.orthography, ["P", "v\\", "f"]))
        self.assertEqual(res.merged, (("P", "v\\"),))

    def test_whole_library_inventory(self):
        c = mz.load("lib:ipa.yasc")
        res = mz.minimize(mz.phones_of(c.orthography), predict=False)
        self.assertEqual(res.merged, (("P", "v\\"),))
        self.assertGreater(len(res.features), 10)

    def test_cli(self):
        status, out, err = _run(mz.main, ["--phones", "p t a", "--json"])
        self.assertEqual(status, 0, err)
        d = json.loads(out)
        self.assertEqual(set(d), {"features", "forced", "bundles", "merged", "predictable"})
        self.assertEqual(sorted(d["bundles"]), ["a", "p", "t"])
        status, out, _ = _run(mz.main, ["--phones", "p t a"])
        self.assertTrue(out.startswith("features ("))
        self.assertEqual(_run(mz.main, ["--phones", "p ZZ"])[0], 2)
        self.assertEqual(_run(mz.main, ["--phones", "p t", "--force", "Nope"])[0], 2)
        self.assertEqual(_run(mz.main, ["-O", "Nope"])[0], 2)
        self.assertEqual(_run(mz.main, ["no/such/script.yasc"])[0], 1)

    def test_python_m_entry_point(self):
        proc = subprocess.run([sys.executable, "-m", "yasc.tools.minimize", "--phones", "p b", "--no-predict"],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("features (1): Voice", proc.stdout)


class MakeOrthoTests(unittest.TestCase):
    PHONES = "p t k b d g m n N s S a e i o u k_w a:".split()

    def test_full_mode_follows_the_library(self):
        text = mo.make_ortho(self.PHONES, name="Mine")
        c = compile_source('!include "lib:ipa.yasc"\n' + text, "<gen>")
        self.assertEqual(c.warnings, [])
        mine, xs = c.orthographies["Mine"], c.orthographies["XSAMPA"]
        for ph in self.PHONES:
            with self.subTest(phone=ph):
                self.assertEqual(mine.parse(ph).seg(0), xs.parse(ph).seg(0))

    def test_minimal_mode_is_standalone_and_distinct(self):
        text = mo.make_ortho(self.PHONES, minimal=True, forced=["Syll"])
        self.assertIn("$P := Phonology [[", text)
        c = compile_source(text, "<gen>")
        self.assertEqual(c.warnings, [])
        segs = {c.orthography.parse(ph).seg(0) for ph in self.PHONES}
        self.assertEqual(len(segs), len(self.PHONES))
        self.assertIn("Syll Binary", text)

    def test_ipa_graphemes(self):
        text = mo.make_ortho(["S", "a", "k_w"], ipa=True)
        self.assertIn("[ʃ", text)
        self.assertIn("kʷ", text)

    def test_clash_is_reported(self):
        text = mo.make_ortho(["P", "v\\"])
        self.assertIn("not distinguished (identical bundles): P = v\\", text)

    def test_cli(self):
        status, out, err = _run(mo.main, ["p t", "a", "--minimal"])
        self.assertEqual(status, 0, err)
        self.assertIn("Orthography [[", out)
        self.assertEqual(_run(mo.main, ["p", "pa"])[0], 2)
        self.assertEqual(_run(mo.main, ["p", "--minimal", "--force", "Nope"])[0], 2)
        self.assertEqual(_run(mo.main, ["--help"])[0], 0)

    def test_python_m_entry_point(self):
        proc = subprocess.run([sys.executable, "-m", "yasc.tools.make_ortho", "p b a", "--minimal"],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[b a]", proc.stdout)


if __name__ == "__main__":
    unittest.main()
