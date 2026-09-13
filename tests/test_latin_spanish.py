"""Step checks for the Latin -> Spanish example (``examples/latin-spanish/``; plans P5–P7).

Each hand-derivation step of ``notes.md`` §1 is replayed in isolation: the rule (or the
``Stress`` group) is looked up by its ``/"`` name, the "before" form of the notes is parsed
with the script's own ``$IPA`` orthography and syllabified with ``$Syl`` (plan P7), the rule
is applied, and the result must equal the "after" form of the notes, both as segments and
as rendered IPA **with stress marks** (``ˈ``). Syllable dots are dropped: the notes write
them only in the first steps, and ``$IPA`` renders derived syllable edges without dots.
When the "before" form carries a stress mark, the other syllables get ``0Stress``, as the
rule Unstressed has run by then.
"""

import contextlib
import io
import os
import unittest

import yasc
from yasc.cli import main
from yasc.compile import compile_file
from yasc.errors import NotImplementedYet, YascLoadError
from yasc.ir import IRBasicRule, IRGroup
from yasc.lexicon import read_file
from yasc.rules import ApplyContext, BasicRule, apply_rule, build_rules
from yasc.syllable import Syllabifier

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(os.path.dirname(HERE), "examples", "latin-spanish")
SCRIPT = os.path.join(EXAMPLE, "latin-spanish.yasc")
LEXICON = os.path.join(EXAMPLE, "lexicon.tsv")

# Basic rules in the script: 37 from the original example, plus the 3 respelling rules
# (SpellQu, SpellSoftC, SpellZ) added at !date 1500 (notes.md §4, fixes after P6).
N_RULES = 40


def cli(*argv):
    """Run the CLI in-process: (status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = main(list(argv))
    return status, out.getvalue(), err.getvalue()

_COMPILED = []


def compiled():
    """The example compiled once (strictly: since P7 it has no pending constructs)."""
    if not _COMPILED:
        _COMPILED.append(compile_file(SCRIPT))
    return _COMPILED[0]


def plain(text):
    """A form of the notes without stress marks and syllable dots (its segments only)."""
    return text.replace("ˈ", "").replace(".", "")


def prepare(c, before):
    """A notes form as a syllabified $IPA form (plan P7): dots dropped, the stress mark
    kept and consumed by ``$Syl``; with a stress mark the other syllables get ``0Stress``."""
    form = Syllabifier(c.syllabifications["Syl"]).syllabify(c.orthographies["IPA"].parse(before.replace(".", "")))
    if "ˈ" in before:
        zero = c.phonologies["P"].segment(Stress=0)
        tier = form.syllables
        syls = [s if s.feats.get("Stress") is not None else s.with_feats(s.feats.merge(zero)) for s in tier.syls]
        form = form.with_syllables(tier.evolve(tier.n, syls))
    return form

# (word, date, rule name, before, after, status). ``after`` None = "the rule does not apply".
# status: "ok", ("skip", reason) or ("xfail", diagnosis).
STEPS = [
    # notes.md §1.1 FACTUM -> hecho
    ("factum", 0, "Unstressed", "fak.tum", "fak.tum", "ok"),
    ("factum", 0, "Stress", "fak.tum", "ˈfak.tum", "ok"),
    ("factum", 100, "FinalMLoss", "ˈfak.tum", "ˈfaktu", "ok"),
    ("factum", 200, "QualityForQuantity", "ˈfaktu", "ˈfaktʊ", "ok"),
    ("factum", 200, "LaxHighLowering", "ˈfaktʊ", "ˈfakto", "ok"),
    ("factum", 300, "VelarPalatalisation", "ˈfakto", None, "ok"),
    ("factum", 300, "KtToJt", "ˈfakto", "ˈfajto", "ok"),
    ("factum", 500, "Lenition", "ˈfajto", None, "ok"),
    ("factum", 700, "ARaising", "ˈfajto", "ˈfejto", "ok"),
    ("factum", 700, "JtToTsh", "ˈfejto", "ˈfetʃo", "ok"),
    ("factum", 800, "Diphthongisation", "ˈfetʃo", None, "ok"),
    ("factum", 1100, "Apocope", "ˈfetʃo", None, "ok"),
    ("factum", 1300, "FToH", "ˈfetʃo", "ˈhetʃo", "ok"),
    ("factum", 1550, "HLoss", "ˈhetʃo", "ˈetʃo", "ok"),
    ("factum", 1550, "SibilantDevoicing", "ˈetʃo", None, "ok"),
    ("factum", 1600, "Velarisation", "ˈetʃo", None, "ok"),
    ("factum", 1600, "Interdentalisation", "ˈetʃo", None, "ok"),
    # notes.md §1.2 OCULUM -> ojo
    ("oculum", 0, "Stress", "o.ku.lum", "ˈo.ku.lum", "ok"),
    ("oculum", 100, "FinalMLoss", "ˈo.ku.lum", "ˈokulu", "ok"),
    ("oculum", 200, "QualityForQuantity", "ˈokulu", "ˈɔkʊlʊ", "ok"),
    ("oculum", 200, "LaxHighLowering", "ˈɔkʊlʊ", "ˈɔkolo", "ok"),
    ("oculum", 300, "VelarPalatalisation", "ˈɔkolo", None, "ok"),
    ("oculum", 500, "Lenition", "ˈɔkolo", "ˈɔgolo", "ok"),
    ("oculum", 600, "Syncope", "ˈɔgolo", "ˈɔglo", "ok"),
    ("oculum", 600, "MedialKL", "ˈɔglo", "ˈɔʎo", "ok"),
    ("oculum", 800, "Diphthongisation", "ˈɔʎo", None, "ok"),
    ("oculum", 800, "LaxMidTensing", "ˈɔʎo", "ˈoʎo", "ok"),
    ("oculum", 900, "LateralToZh", "ˈoʎo", "ˈoʒo", "ok"),
    ("oculum", 1550, "SibilantDevoicing", "ˈoʒo", "ˈoʃo", "ok"),
    ("oculum", 1600, "Velarisation", "ˈoʃo", "ˈoxo", "ok"),
    # notes.md §1.3 CĪVITĀTEM -> ciudad
    ("civitatem", 0, "Stress", "kiː.wi.taː.tem", "kiː.wi.ˈtaː.tem", "ok"),
    ("civitatem", 100, "FinalMLoss", "kiː.wi.ˈtaː.tem", "kiːwiˈtaːte", "ok"),
    ("civitatem", 100, "WToBeta", "kiːwiˈtaːte", "kiːβiˈtaːte", "ok"),
    ("civitatem", 200, "QualityForQuantity", "kiːβiˈtaːte", "kiβɪˈtatɛ", "ok"),
    ("civitatem", 200, "LaxHighLowering", "kiβɪˈtatɛ", "kiβeˈtatɛ", "ok"),
    ("civitatem", 300, "VelarPalatalisation", "kiβeˈtatɛ", "tsiβeˈtatɛ", "ok"),
    ("civitatem", 500, "Lenition", "tsiβeˈtatɛ", "tsiβeˈdadɛ", "ok"),
    ("civitatem", 600, "Syncope", "tsiβeˈdadɛ", "tsiβˈdadɛ", "ok"),
    ("civitatem", 800, "LaxMidTensing", "tsiβˈdadɛ", "tsiβˈdade", "ok"),
    ("civitatem", 1100, "Apocope", "tsiβˈdade", "tsiβˈdad", "ok"),
    ("civitatem", 1450, "BetaVocalisation", "tsiβˈdad", "tsiuˈdad", "ok"),
    ("civitatem", 1450, "HiatusGlide", "tsiuˈdad", "tsjuˈdad", "ok"),
    ("civitatem", 1600, "Interdentalisation", "tsjuˈdad", "θjuˈdad", "ok"),
    # notes.md §1.4 SCHOLAM -> escuela
    ("scholam", 0, "Stress", "s.ko.lam", "sˈko.lam", "ok"),
    ("scholam", 100, "FinalMLoss", "sˈko.lam", "sˈkola", "ok"),
    ("scholam", 200, "QualityForQuantity", "sˈkola", "sˈkɔla", "ok"),
    ("scholam", 300, "Prothesis", "sˈkɔla", "esˈkɔla", "ok"),
    ("scholam", 500, "Lenition", "esˈkɔla", None, "ok"),
    ("scholam", 600, "Syncope", "esˈkɔla", None, "ok"),
    ("scholam", 800, "Diphthongisation", "esˈkɔla", "esˈkwela", "ok"),
    ("scholam", 1550, "SibilantDevoicing", "esˈkwela", None, "ok"),
    # notes.md §1.5 CLĀVEM -> llave
    ("clavem", 0, "Stress", "klaː.wem", "ˈklaː.wem", "ok"),
    ("clavem", 100, "FinalMLoss", "ˈklaː.wem", "ˈklaːwe", "ok"),
    ("clavem", 100, "WToBeta", "ˈklaːwe", "ˈklaːβe", "ok"),
    ("clavem", 200, "QualityForQuantity", "ˈklaːβe", "ˈklaβɛ", "ok"),
    ("clavem", 600, "MedialKL", "ˈklaβɛ", None, "ok"),
    ("clavem", 800, "LaxMidTensing", "ˈklaβɛ", "ˈklaβe", "ok"),
    ("clavem", 900, "LateralToZh", "ˈklaβe", None, "ok"),
    ("clavem", 1000, "InitialClusterPalatalisation", "ˈklaβe", "ˈʎaβe", "ok"),
    ("clavem", 1100, "Apocope", "ˈʎaβe", None, "ok"),
    ("clavem", 1600, "BetaMerger", "ˈʎaβe", "ˈʎabe", "ok"),
]


class LatinCompileTests(unittest.TestCase):
    """Since P7 the example compiles strictly, with no warnings (notes.md §4 C2)."""

    def test_compiles_without_warnings(self):
        c = compiled()
        self.assertEqual(len(c.rules), N_RULES)
        self.assertEqual(c.warnings, [])

    def test_allow_unimplemented_changes_nothing(self):
        c = compile_file(SCRIPT, allow_unimplemented=True)
        self.assertEqual((len(c.rules), c.warnings), (N_RULES, []))
        self.assertIn("Syl", c.syllabifications)

    def test_every_rule_builds(self):
        rules = build_rules(compiled())
        self.assertEqual(len(rules), N_RULES)
        self.assertTrue(all(isinstance(r, BasicRule) for r in rules.values()))
        self.assertTrue(rules[9].persistent)  # HiatusGlide /::


class LatinEndToEndTests(unittest.TestCase):
    """The whole script over ``lexicon.tsv`` with the P6 runtime (plan P6/P7).

    ``test_lexicon_end_to_end`` skips by itself while loading or running raises
    :class:`NotImplementedYet` (today: the P7 syllable constructs), so it activates once
    P7 lands. Records whose ``note`` says NOT DERIVED are not compared. See notes.md §4
    (P6 entry, E1/E2) for words the rules are known to get wrong."""

    def test_lexicon_end_to_end(self):
        try:
            sc = yasc.load(SCRIPT)
        except YascLoadError as e:
            pending = [x for x in e.errors if isinstance(x, NotImplementedYet)]
            if pending and len(pending) == len(e.errors):
                phases = sorted({x.phase or "?" for x in pending})
                self.skipTest("needs plan phase %s: %s" % ("/".join(phases), pending[0].message))
            raise
        records = list(read_file(LEXICON))
        results = [sc.runtime.run(rec) for rec in records]
        for res in results:
            if isinstance(res.error, NotImplementedYet):
                self.skipTest("needs plan phase %s: %s" % (res.error.phase, res.error.message))
        for rec, res in zip(records, results):
            if "NOT DERIVED" in (rec.column("note") or ""):
                continue
            with self.subTest(word=rec.form):
                self.assertIsNone(res.error, res.error and res.error.format())
                latin, ipa, spelling = res.printed.rstrip("\n").split("\t")
                self.assertEqual(ipa, "[%s]" % rec.column("expected_ipa"))
                self.assertEqual(spelling, rec.column("expected_spelling"))

    def test_check_with_allow_pending(self):
        status, out, err = cli(SCRIPT, "--check", "--allow-pending")
        self.assertEqual(status, 0, err)
        self.assertIn("OK: %d rules, 0 warnings" % N_RULES, out)
        status, out, err = cli(SCRIPT, "--check")
        self.assertEqual(status, 0, err)
        self.assertIn("OK: %d rules, 0 warnings" % N_RULES, out)

    def test_list_rules(self):
        status, out, _ = cli(SCRIPT, "--list-rules")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), N_RULES + 1)  # header line + one per rule
        rows = [line.split("\t") for line in lines[1:]]
        self.assertEqual([r[0] for r in rows], [str(n) for n in range(1, N_RULES + 1)])
        self.assertEqual((rows[0][1], rows[0][2]), ("Unstressed", "0"))
        self.assertEqual(rows[-1][1:3], ["BetaMerger", "1600"])
        self.assertIn("FinalMLoss", [r[1] for r in rows])


class LatinStepTests(unittest.TestCase):
    """One test per hand-derivation step of notes.md §1 (generated below).

    QualityForQuantity on the long ā of CĪVITĀTEM and CLĀVEM relies on the implication
    ``{+Low +ATR} --> {-ATR}``, whose trigger reads ATR, re-firing after the rule sets
    +ATR (spec §4.5; design §13 entry 16). Plain ``{+Low} --> {-ATR}`` would not re-fire."""

    def check(self, rule_name, before, after):
        c = compiled()
        ipa = c.orthographies["IPA"]
        ir = c.named[rule_name]
        self.assertIsInstance(ir, (IRBasicRule, IRGroup))
        form = prepare(c, before)
        out = apply_rule(ir, form, ApplyContext())
        if after is None:
            self.assertFalse(out.applied, "%s should not apply to %s" % (rule_name, before))
            self.assertEqual(out.form.segs, form.segs)
            return
        self.assertTrue(out.applied, "%s should apply to %s" % (rule_name, before))
        self.assertEqual(ipa.render(out.form), after.replace(".", ""))
        self.assertEqual(out.form.segs, ipa.parse(plain(after)).segs)


def _make(rule_name, before, after):
    def test(self):
        self.check(rule_name, before, after)
    test.__doc__ = "%s: %s -> %s (notes.md §1)" % (rule_name, before, after or "(no change)")
    return test


for _n, (_word, _date, _rule, _before, _after, _status) in enumerate(STEPS):
    _fn = _make(_rule, _before, _after)
    if isinstance(_status, tuple) and _status[0] == "skip":
        _fn = unittest.skip(_status[1])(_fn)
    elif isinstance(_status, tuple) and _status[0] == "xfail":
        _fn = unittest.expectedFailure(_fn)
    setattr(LatinStepTests, "test_%s_%02d_%d_%s" % (_word, _n, _date, _rule), _fn)
