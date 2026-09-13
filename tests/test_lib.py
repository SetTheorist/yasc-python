"""The standard library ``yasc/lib/ipa.yasc`` and ``!include "lib:..."`` (plan P10;
design §13 entries 163–166).

Checks that every X-SAMPA phone of ``orig-notes/xsampa`` parses to one segment and renders
back, that X-SAMPA -> IPA rendering agrees on the whole inventory, that distinct phones have
distinct bundles (one documented alias: ``P`` = ``v\\``), that the phonologically meaningful
diacritics round-trip, and that the library compiles strictly with no warnings.
"""

import os
import unittest

import yasc
from yasc.compile import LIB_DIR, compile_file, compile_source
from yasc.errors import YascLoadError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "yasc", "lib", "ipa.yasc")

#: X-SAMPA phone -> IPA, for the inventory of orig-notes/xsampa (consonants, vowels, clicks,
#: implosives, ejectives, other symbols) plus U and the affricates d_z t_S d_Z (entry 164).
XSAMPA_IPA = {
    "p": "p", "b": "b", "t": "t", "d": "d", "t`": "ʈ", "d`": "ɖ", "c": "c", "J\\": "ɟ", "k": "k",
    "g": "g", "q": "q", "G\\": "ɢ", "?": "ʔ", "m": "m", "F": "ɱ", "n": "n", "n`": "ɳ", "J": "ɲ",
    "N": "ŋ", "N\\": "ɴ", "B\\": "ʙ", "r": "r", "R\\": "ʀ", "4": "ɾ", "r`": "ɽ", "p\\": "ɸ",
    "B": "β", "f": "f", "v": "v", "T": "θ", "D": "ð", "s": "s", "z": "z", "S": "ʃ", "Z": "ʒ",
    "s`": "ʂ", "z`": "ʐ", "C": "ç", "j\\": "ʝ", "x": "x", "G": "ɣ", "X": "χ", "R": "ʁ",
    "X\\": "ħ", "?\\": "ʕ", "h": "h", "h\\": "ɦ", "K": "ɬ", "K\\": "ɮ", "P": "ʋ", "v\\": "ʋ",
    "r\\": "ɹ", "r\\`": "ɻ", "j": "j", "M\\": "ɰ", "l": "l", "l`": "ɭ", "L": "ʎ", "L\\": "ʟ",
    "i": "i", "y": "y", "I": "ɪ", "Y": "ʏ", "1": "ɨ", "}": "ʉ", "M": "ɯ", "u": "u", "U": "ʊ",
    "e": "e", "2": "ø", "@\\": "ɘ", "8": "ɵ", "7": "ɤ", "o": "o", "@": "ə", "E": "ɛ", "9": "œ",
    "3": "ɜ", "3\\": "ɞ", "V": "ʌ", "O": "ɔ", "{": "æ", "6": "ɐ", "a": "a", "&": "ɶ", "A": "ɑ",
    "Q": "ɒ",
    "O\\": "ʘ", "|\\": "ǀ", "!\\": "ǃ", "=\\": "ǂ", "|\\|\\": "ǁ",
    "b_<": "ɓ", "d_<": "ɗ", "J\\_<": "ʄ", "g_<": "ɠ", "G\\_<": "ʛ",
    "p_>": "pʼ", "t_>": "tʼ", "c_>": "cʼ", "k_>": "kʼ", "s_>": "sʼ",
    "W": "ʍ", "w": "w", "H": "ɥ", "H\\": "ʜ", "<\\": "ʢ", ">\\": "ʡ", "s\\": "ɕ", "z\\": "ʑ",
    "l\\": "ɺ", "x\\": "ɧ", "k_p": "k͡p", "t_s": "t͡s", "d_z": "d͡z", "t_S": "t͡ʃ", "d_Z": "d͡ʒ",
}

#: Documented aliases: input -> the text it renders as (entry 164).
ALIASES = {"v\\": "P"}

#: (X-SAMPA input, X-SAMPA rendering, IPA rendering) for each supported diacritic.
DIACRITICS = [
    ("n_0", "n_0", "n̥"), ("k_p_v", "k_p_v", "k͡p̬"), ("p_h", "p_h", "pʰ"),
    ("b_h", "b_h", "bʰ"), ("a_k", "a_k", "a̰"), ("x_>", "x_>", "xʼ"),
    ("m_<", "m_k", "m̰"), ("n=", "n=", "n̩"), ("n_=", "n=", "n̩"),
    ("i_^", "i_^", "i̯"), ("a~", "a~", "ã"), ("a_~", "a~", "ã"),
    ("k_w", "k_w", "kʷ"), ("t_j", "t_j", "tʲ"), ("k_j", "k_j", "kʲ"), ("l_G", "l_G", "lˠ"),
    ("t_?\\", "t_?\\", "tˤ"), ("t_d", "t_d", "t̪"), ("@`", "@`", "ə˞"),
    ("Q_A", "Q_A", "ɒ̘"), ("1_q", "1_q", "ɨ̙"), ("a:", "a:", "aː"),
]


def _lib():
    return compile_file(LIB)


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = _lib()
        cls.X = cls.c.orthographies["XSAMPA"]
        cls.I = cls.c.orthographies["IPA"]

    def test_compiles_strictly_without_warnings(self):
        self.assertEqual(self.c.warnings, [])
        self.assertEqual(sorted(self.c.orthographies), ["IPA", "XSAMPA"])
        self.assertIs(self.c.orthography, self.X)          # defined last: the active one

    def test_every_xsampa_phone_parses_and_round_trips(self):
        for g in XSAMPA_IPA:
            with self.subTest(phone=g):
                form = self.X.parse(g)
                self.assertEqual(form.n, 1)
                self.assertEqual(self.X.render(form), ALIASES.get(g, g))

    def test_xsampa_to_ipa_agrees_on_the_inventory(self):
        for g, ipa in XSAMPA_IPA.items():
            with self.subTest(phone=g):
                self.assertEqual(self.I.render(self.X.parse(g)), ipa)
                back = self.I.parse(ipa)
                self.assertEqual(back.n, 1)
                self.assertEqual(back.seg(0), self.X.parse(g).seg(0))

    def test_ipa_g_alias(self):
        self.assertEqual(self.I.parse("ɡ").seg(0), self.I.parse("g").seg(0))
        self.assertEqual(self.I.render(self.I.parse("ɡa")), "ga")

    def test_distinct_phones_have_distinct_bundles(self):
        seen = {}
        for g in XSAMPA_IPA:
            seg = self.X.parse(g).seg(0)
            if seg in seen:
                self.assertEqual(ALIASES.get(g), seen[seg], "%s and %s share a bundle" % (g, seen[seg]))
            else:
                seen[seg] = g
        self.assertEqual(len(seen), len(XSAMPA_IPA) - len(ALIASES))

    def test_diacritics(self):
        for text, xs, ipa in DIACRITICS:
            with self.subTest(text=text):
                form = self.X.parse(text)
                self.assertEqual(form.n, 1)
                self.assertEqual(self.X.render(form), xs)
                self.assertEqual(self.I.render(form), ipa)
                self.assertEqual(self.I.parse(ipa).seg(0), form.seg(0))

    def test_xsampa_separators_avoid_the_clashes(self):
        # Entry 165: "=" is syllabic, "/" is the clitic separator, ( ) are the brackets,
        # "-" separates phones only where needed, " and % are stress marks.
        X, I = self.X, self.I
        for text in ["(N:kat)", "pa/la", "n=", "t_s", "pa.ta", "ka+ta", "b_<a", '"pa%ta']:
            with self.subTest(text=text):
                self.assertEqual(X.render(X.parse(text)), text)
        self.assertEqual(X.render(X.parse("t-s")), "ts")
        self.assertEqual(X.parse("n=").n, 1)
        # A syllable mark replaces the "." of its gap (design §13 entry 57).
        self.assertEqual(X.render(X.parse('"pa.%ta')), '"pa%ta')
        self.assertEqual(I.render(X.parse('"pa.%ta')), "ˈpaˌta")
        self.assertEqual(I.render(X.parse("pa/la")), "pa=la")
        self.assertEqual(I.render(X.parse("(N:kat)")), "<N:kat>")

    def test_implications_fill_defaults(self):
        fs = self.c.phonology
        seg = self.X.parse("t_j").seg(0)
        self.assertEqual(seg.get("Back"), "-")                 # {+Front} --> {-Back}
        self.assertEqual(self.X.parse("a").seg(0).get("Long"), "-")   # {} ~~> {-Long}
        self.assertIs(fs, self.X.fs)


class LibIncludeTests(unittest.TestCase):
    SCRIPT = ('!include "lib:ipa.yasc"\n'
              "!orthography input $XSAMPA output $IPA\n"
              "Rules [[\n"
              "  {-Son -Cont -Voice} --> {+Cont +DelRel} / {+Syll} ___ {+Syll}\n"
              "  [S] --> [s]\n"
              "]]\n")

    def test_include_from_a_string_script(self):
        sc = yasc.loads(self.SCRIPT, "<string>")
        self.assertEqual(sc.compiled.warnings, [])
        self.assertEqual(sc.apply("apa").text, "apa\taɸa\n")
        self.assertEqual(sc.apply("ak_haSi").outputs[0].text, "axʰasi")

    def test_include_from_another_directory(self):
        # Entry 163: lib: does not depend on where the including file is.
        c = compile_source('!include "lib:ipa.yasc"\n', os.path.join(ROOT, "examples", "x.yasc"))
        self.assertIn(os.path.join(LIB_DIR, "ipa.yasc"), c.included)

    def test_missing_lib_file(self):
        with self.assertRaises(YascLoadError) as cm:
            compile_source('!include "lib:nope.yasc"\n', "<string>")
        text = str(cm.exception)
        self.assertIn("cannot include 'lib:nope.yasc': file not found", text)
        self.assertIn("standard library", text)

    def test_relative_include_unchanged(self):
        # Regression for the P10 change to compile._include: plain paths stay relative.
        with self.assertRaises(YascLoadError) as cm:
            compile_source('!include "nope.yasc"\n', os.path.join(ROOT, "examples", "x.yasc"))
        self.assertIn("relative to the including file", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
