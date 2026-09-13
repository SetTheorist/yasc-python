"""Tests for yasc.orthography: parsing and rendering (spec §5.6; design §9)."""

import unittest

from yasc.errors import SourceLoc, YascDefinitionError, YascRuntimeError
from yasc.features import FeatureSystem, FeatureType
from yasc.form import Form
from yasc.marks import Bracket, Mark
from yasc.orthography import OpaqueSegment, Orthography, UnparsableError, is_unknown
from yasc.segment import Absent, Cmp, Eq, SegmentSpec


# --------------------------------------------------------------------------------------------
# An X-SAMPA fragment (orig-notes/xsampa) over a small feature system
# --------------------------------------------------------------------------------------------


def xsampa_system():
    fs = FeatureSystem("xsampa")
    for name in ("Syll", "Cons", "Son", "Cont", "Voice", "Nasal", "Lat", "Ant", "High", "Low", "Back",
                 "Round", "ATR"):
        fs.add_feature(name, FeatureType.binary())
    fs.add_feature("Place", FeatureType.node(), children=("Labial", "Coronal", "Dorsal", "Glottal"))
    for name in ("Labial", "Coronal", "Dorsal", "Glottal", "Asp", "DelRel", "Ejective", "Implosive"):
        fs.add_feature(name, FeatureType.unary())
    fs.add_implication(SegmentSpec(fs, [Eq(fs.feature("Syll"), "+")]),
                       SegmentSpec(fs, [Eq(fs.feature("Voice"), "+")]), weak=True)
    fs.add_implication(SegmentSpec(fs, [Eq(fs.feature("Nasal"), "+")]),
                       SegmentSpec(fs, [Eq(fs.feature("Voice"), "+")]))
    return fs.seal()


def xsampa_orthography(fs):
    o = Orthography(fs, "X-SAMPA")
    # Consonants.
    o.add_phones("p b t d t` c J\\ k g q ? m n N f v s z S Z x h l r t_s p_> b_< J\\_<", {"Syll": "-", "Cons": "+"})
    o.add_phones("j w", {"Syll": "-", "Cons": "-", "Son": "+", "Voice": "+", "High": "+"})
    o.add_phones("p b t d t` c J\\ k g q ? t_s p_> b_< J\\_<", {"Son": "-", "Cont": "-", "Nasal": "-"})
    o.add_phones("f v s z S Z x h", {"Son": "-", "Cont": "+", "Nasal": "-"})
    o.add_phones("m n N", {"Son": "+", "Cont": "-", "Nasal": "+", "Voice": "+"})
    o.add_phones("l r", {"Son": "+", "Cont": "+", "Nasal": "-", "Voice": "+"})
    o.add_phones(["l"], {"Lat": "+"})
    o.add_phones(["r"], {"Lat": "-"})
    o.add_phones("p t t` c k q ? f s S x h t_s p_>", {"Voice": "-"})
    o.add_phones("b d J\\ g v z Z b_< J\\_<", {"Voice": "+"})
    o.add_phones("p b m f v p_> b_< w", {"Labial": "!"})
    o.add_phones("t d t` n s z S Z l r t_s", {"Coronal": "!"})
    o.add_phones("t d n s z l r t_s", {"Ant": "+"})
    o.add_phones("t` S Z", {"Ant": "-"})
    o.add_phones("c J\\ k g q N x J\\_<", {"Dorsal": "!"})
    o.add_phones("c J\\ k g N x J\\_< j", {"High": "+"})
    o.add_phones(["q"], {"High": "-"})
    o.add_phones("c J\\ J\\_< j", {"Back": "-"})
    o.add_phones("k g q N x w", {"Back": "+"})
    o.add_phones(["w"], {"Round": "+"})
    o.add_phones("? h", {"Glottal": "!"})
    o.add_phones(["t_s"], {"DelRel": "!"})
    o.add_phones(["p_>"], {"Ejective": "!"})
    o.add_phones("b_< J\\_<", {"Implosive": "!"})
    # Vowels.
    o.add_phones("i y u e E o O a @", {"Syll": "+", "Cons": "-", "Son": "+", "Voice": "+"})
    o.add_phones("i y u", {"High": "+", "Low": "-"})
    o.add_phones("e E o O @", {"High": "-", "Low": "-"})
    o.add_phones(["a"], {"High": "-", "Low": "+"})
    o.add_phones("i y e E", {"Back": "-"})
    o.add_phones("u o O a @", {"Back": "+"})
    o.add_phones("y u o O", {"Round": "+"})
    o.add_phones("i e E a @", {"Round": "-"})
    o.add_phones("i u e o", {"ATR": "+"})
    o.add_phones("E O", {"ATR": "-"})
    # Diacritics (all postfixes in X-SAMPA).
    o.add_diacritic({"Asp": "!"}, "#_h")
    o.add_diacritic({"Round": "+"}, "#_w")
    o.add_diacritic({"Nasal": "+"}, "#~")
    o.add_diacritic({"Syll": "+"}, "#=")
    o.add_diacritic({"Voice": "-"}, "#_0")
    o.add_diacritic({"Implosive": "!"}, "#_<")
    o.add_diacritic({"Ejective": "!"}, "#_>")
    return o.seal()


FS = xsampa_system()
XS = xsampa_orthography(FS)


def toy(extra=None):
    """A small orthography for tokenisation and diacritic tests."""
    fs = FeatureSystem()
    for name in ("Syll", "Voice", "Asp", "Round", "Long", "Lat", "Strid"):
        fs.add_feature(name, FeatureType.binary())
    fs.add_feature("Stress", FeatureType.scalar(0, 2), scope="syllable")
    fs.seal()
    o = Orthography(fs)
    return fs, o


# --------------------------------------------------------------------------------------------
# X-SAMPA round trip
# --------------------------------------------------------------------------------------------


class TestXSampa(unittest.TestCase):
    def test_every_grapheme_round_trips(self):
        seen = {}
        self.assertGreaterEqual(len(XS.graphemes), 25)
        for g in XS.graphemes:
            with self.subTest(grapheme=g):
                form = XS.parse(g)
                self.assertEqual(form.n, 1)
                seg = form.segs[0]
                self.assertNotIn(seg, seen, "%s and %s have the same bundle" % (g, seen.get(seg)))
                seen[seg] = g
                self.assertEqual(XS.render(form), g)
                self.assertEqual(XS.render_segment(seg), (g, False, ()))

    def test_multi_character_graphemes(self):
        for g in ("J\\", "t_s", "p_>", "b_<", "J\\_<", "t`"):
            with self.subTest(grapheme=g):
                self.assertEqual(XS.parse(g).segs, (XS.phone_segment(g),))

    def test_words_round_trip(self):
        words = ["t_sa~", "n=", "k_h_wa", "b_<aJ\\_<i", "p_>ot`E", "?ah", "J\\u p_hO", "d_>a", "S_wiN",
                 "l=r=", "c_hy~ x_w@"]
        for w in words:
            with self.subTest(word=w):
                form = XS.parse(w)
                self.assertEqual(XS.parse(XS.render(form)), form)

    def test_render_normalises(self):
        # d with -Voice is exactly t; t_w_h prints its postfixes in declaration order.
        self.assertEqual(XS.render(XS.parse("d_0")), "t")
        self.assertEqual(XS.render(XS.parse("t_w_h")), "t_h_w")
        self.assertEqual(XS.parse("t_w_h"), XS.parse("t_h_w"))

    def test_grapheme_beats_base_plus_diacritic(self):
        tokens = XS.tokenize("p_>")
        self.assertEqual(len(tokens), 1)
        phone, merge = tokens[0][1]
        self.assertEqual(XS.graphemes[phone], "p_>")
        self.assertEqual(merge, ())
        # Without a grapheme, the diacritic applies: d_> is d + ejective.
        seg = XS.parse("d_>").segs[0]
        self.assertEqual(seg.get("Ejective"), "!")
        self.assertEqual(seg.get("Coronal"), "!")

    def test_implications_applied(self):
        seg = XS.parse("t~").segs[0]
        self.assertEqual(seg.get("Nasal"), "+")
        self.assertEqual(seg.get("Voice"), "+")  # {+Nasal} --> {+Voice}
        # t~ and d~ give the same closed segment; t is declared first, so t~ wins.
        self.assertEqual(XS.render(XS.parse("t~")), "t~")
        self.assertEqual(XS.parse("d~"), XS.parse("t~"))
        raw = XS.parse("t~", close=False).segs[0]
        self.assertEqual(raw.get("Voice"), "-")

    def test_xsampa_brackets_versus_diacritics(self):
        """`<` and `>` are bracket delimiters, but `_<`, `_>`, `b_<` and `p_>` win."""
        f = XS.parse("<N:ab_<>")
        self.assertEqual(f.segs, (XS.phone_segment("a"), XS.phone_segment("b_<")))
        self.assertEqual(f.brackets, (Bracket("N", 0, 2),))
        f = XS.parse("<V:p_>><N:d_<a>")
        self.assertEqual(f.brackets, (Bracket("V", 0, 1), Bracket("N", 1, 3)))
        self.assertEqual(f.segs[0], XS.phone_segment("p_>"))
        self.assertEqual(f.segs[1].get("Implosive"), "!")
        self.assertEqual(XS.parse(XS.render(f)), f)
        self.assertEqual(XS.render(f), "<V:p_>><N:d_<a>")


# --------------------------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------------------------


class TestDeclarations(unittest.TestCase):
    def test_additive_and_conflicts(self):
        fs, o = toy()
        o.add_phones(["p"], {"Syll": "-"})
        o.add_phones("p t", {"Voice": "-"})
        o.add_phones(["p"], {"Voice": "-"})  # repeating a value is fine
        with self.assertRaises(YascDefinitionError) as cm:
            o.add_phones(["p"], {"Voice": "+"})
        self.assertIn("conflicting", str(cm.exception))
        o.seal()
        self.assertEqual(o.phone_bundle("p"), fs.segment(Syll="-", Voice="-"))

    def test_spec_features(self):
        fs, o = toy()
        spec = SegmentSpec(fs, [Eq(fs.feature("Syll"), "+"), Absent(fs.feature("Voice"))])
        o.add_phones(["a"], spec)
        with self.assertRaises(YascDefinitionError):
            o.add_phones(["a"], {"Voice": "+"})  # conflicts with _Voice
        with self.assertRaises(YascDefinitionError):
            o.add_phones(["b"], SegmentSpec(fs, [Cmp(fs.feature("Stress"), ">", 0)]))
        with self.assertRaises(YascDefinitionError):
            o.add_phones(["c"], {"Nope": "+"})

    def test_bad_templates_and_graphemes(self):
        _fs, o = toy()
        for template in ("_h", "#_h#", "#", "a b#"):
            with self.subTest(template=template), self.assertRaises(YascDefinitionError):
                o.add_diacritic({"Asp": "+"}, template)
        for g in ("", "a b"):
            with self.subTest(grapheme=g), self.assertRaises(YascDefinitionError):
                o.add_phones([g], {"Syll": "+"})

    def test_sealed(self):
        _fs, o = toy()
        o.add_phones(["a"], {"Syll": "+"})
        o.seal()
        self.assertTrue(o.sealed)
        with self.assertRaises(YascDefinitionError):
            o.add_phones(["b"], {"Syll": "-"})
        with self.assertRaises(YascDefinitionError):
            o.set_separator("word", "|")

    def test_settings(self):
        _fs, o = toy()
        o.set_setting("MorphemeSeparator", "-")
        o.set_setting("BracketOpen", "[")
        o.set_setting("FloatingPrefix", "~")
        self.assertEqual(o.separators["morpheme"], "-")
        self.assertEqual(o.delimiters["open"], "[")
        self.assertEqual(o.floating_prefix, "~")
        with self.assertRaises(YascDefinitionError):
            o.set_setting("Nonsense", "x")
        with self.assertRaises(NotImplementedError):
            o.set_setting("Escape", "\\")
        with self.assertRaises(YascDefinitionError):
            o.set_separator("tone", "x")


# --------------------------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------------------------


def _texts(o, text):
    return [o.graphemes[tok[1][0]] for tok in o.tokenize(text)]


class TestTokenisation(unittest.TestCase):
    def setUp(self):
        self.fs, self.o = toy()
        feats = ["a", "b", "c", "d", "ab", "bcd", "t", "s", "h", "ts", "sh"]
        for n, g in enumerate(feats):
            # Every grapheme gets a distinct bundle.
            self.o.add_phones([g], {"Syll": "+-"[n % 2], "Voice": "+-"[(n // 2) % 2],
                                    "Asp": "+-"[(n // 4) % 2], "Round": "+-"[(n // 8) % 2]})
        self.o.seal()

    def test_fewest_tokens_beats_longest_first(self):
        # Greedy longest match would give ab|c|d (3 tokens); a|bcd has 2.
        self.assertEqual(_texts(self.o, "abcd"), ["a", "bcd"])

    def test_tie_goes_to_longest_grapheme_first(self):
        # ts|h and t|sh both have 2 tokens: the longer first grapheme wins.
        self.assertEqual(_texts(self.o, "tsh"), ["ts", "h"])
        self.assertEqual(_texts(self.o, "abc"), ["ab", "c"])

    def test_single_longest(self):
        self.assertEqual(_texts(self.o, "ts"), ["ts"])
        self.assertEqual(_texts(self.o, "ab"), ["ab"])


class TestDiacritics(unittest.TestCase):
    def setUp(self):
        fs, o = toy()
        o.add_phones(["t"], {"Syll": "-", "Voice": "-"})
        o.add_phones(["a"], {"Syll": "+"})
        o.add_diacritic({"Asp": "+"}, "#_h")
        o.add_diacritic({"Round": "+", "Lat": "+"}, "#_a")
        o.add_diacritic({"Round": "-"}, "#_b")
        o.add_diacritic({"Voice": "+"}, "*#")
        o.add_diacritic({"Strid": "+"}, "^#")
        o.add_diacritic({"Voice": "-"}, "#_0")
        o.add_diacritic({"Long": "+", "Asp": "-"}, "(#)")
        self.fs, self.o = fs, o.seal()

    def seg(self, text):
        form = self.o.parse(text)
        self.assertEqual(form.n, 1, text)
        return form.segs[0]

    def test_stacking_outer_wins(self):
        self.assertEqual(self.seg("t_a_b").get("Round"), "-")  # _b is outermost
        self.assertEqual(self.seg("t_b_a").get("Round"), "+")
        self.assertEqual(self.seg("t_a_b").get("Lat"), "+")

    def test_prefixes(self):
        s = self.seg("^*t")
        self.assertEqual((s.get("Voice"), s.get("Strid")), ("+", "+"))
        # Postfixes are inner, prefixes outer: *t_0 is voiced.
        self.assertEqual(self.seg("*t_0").get("Voice"), "+")

    def test_circumfix(self):
        s = self.seg("(t)")
        self.assertEqual(s.get("Long"), "+")
        self.assertEqual(self.seg("(t_h)").get("Asp"), "-")  # the circumfix is outside _h
        self.assertEqual(self.seg("(t)_h").get("Asp"), "+")  # _h is outside the circumfix
        self.assertEqual(self.seg("*(t_a)_b").get("Voice"), "+")
        with self.assertRaises(UnparsableError):
            self.o.parse("(t")  # a circumfix must be closed

    def test_round_trips(self):
        for text in ("t_a_b", "t_b_a", "*t_0", "^*t_h", "(t_h)", "(t)_h", "*(a_a)_b", "(t_0)", "t_h_a"):
            with self.subTest(text=text):
                form = self.o.parse(text)
                out = self.o.render(form)
                self.assertEqual(self.o.parse(out), form, out)
                self.assertFalse(self.o.render_segment(form.segs[0])[1])

    def test_postfix_order_in_output(self):
        # Round - and Lat +: needs _a then _b, in that order, although _b alone is nearer.
        target = self.seg("t_a_b")
        text, approx, residual = self.o.render_segment(target)
        self.assertEqual(text, "t_a_b")
        self.assertFalse(approx)
        self.assertEqual(residual, ())


# --------------------------------------------------------------------------------------------
# Separators, brackets, syllable marks
# --------------------------------------------------------------------------------------------


class TestSeparatorsAndBrackets(unittest.TestCase):
    def setUp(self):
        fs, o = toy()
        o.add_phones("C D t s", {"Syll": "-"})
        o.add_phones(["D"], {"Voice": "+"})
        o.add_phones(["t"], {"Asp": "+"})
        o.add_phones(["s"], {"Strid": "+"})
        o.add_phones(["ts"], {"Syll": "-", "Asp": "+", "Strid": "+"})
        o.add_phones("A a", {"Syll": "+"})
        o.add_phones(["a"], {"Round": "+"})
        o.add_syllable_mark({"Stress": 2}, '"')
        self.fs, self.o = fs, o

    def test_marks(self):
        f = self.o.parse("Ca.Ca+Ca=Ca Ca#Ca##Ca")
        marks = [set(f.gaps[g]) for g in range(f.n + 1)]
        self.assertEqual(marks[2], {Mark.SYLLABLE})
        self.assertEqual(marks[4], {Mark.MORPHEME})
        self.assertEqual(marks[6], {Mark.CLITIC})
        self.assertEqual(marks[8], {Mark.WORD})
        self.assertEqual(marks[10], {Mark.WORD})
        self.assertEqual(marks[12], {Mark.PHRASE})
        self.assertEqual(self.o.render(f), "Ca.Ca+Ca=Ca Ca Ca##Ca")
        self.assertEqual(self.o.parse(self.o.render(f)), f)

    def test_whitespace(self):
        f = self.o.parse("  Ca \t Ca  ")
        self.assertEqual(f.n, 4)
        self.assertEqual(f.gaps[0], frozenset())
        self.assertEqual(f.gaps[2], frozenset({Mark.WORD}))
        self.assertEqual(f.gaps[4], frozenset())

    def test_edge_marks_round_trip(self):
        f = self.o.parse("#Ca+")
        self.assertEqual(f.gaps[0], frozenset({Mark.WORD}))
        self.assertEqual(f.gaps[2], frozenset({Mark.MORPHEME}))
        self.assertEqual(self.o.render(f), "#Ca+")

    def test_several_marks_in_one_gap(self):
        f = self.o.parse("Ca.+Ca")
        self.assertEqual(f.gaps[2], frozenset({Mark.SYLLABLE, Mark.MORPHEME}))
        self.assertEqual(self.o.parse(self.o.render(f)), f)

    def test_custom_separators(self):
        self.o.set_separator("morpheme", "-")
        self.o.set_separator("syllable", None)
        f = self.o.parse("Ca-Ca")
        self.assertEqual(f.gaps[2], frozenset({Mark.MORPHEME}))
        with self.assertRaises(UnparsableError):
            self.o.parse("Ca.Ca")

    def test_phone_separator(self):
        self.o.set_separator("PhoneSeparator", "|")
        self.o.seal()
        t, s = self.o.phone_segment("t"), self.o.phone_segment("s")
        f = Form.from_segments([t, s])
        self.assertEqual(self.o.parse("t|s"), f)
        self.assertEqual(self.o.parse("ts").segs, (self.o.phone_segment("ts"),))
        # Rendering puts the separator in only where the grapheme sequence is ambiguous.
        self.assertEqual(self.o.render(f), "t|s")
        self.assertEqual(self.o.render(Form.from_segments([s, t])), "st")

    def test_nested_brackets(self):
        f = self.o.parse("<V:<N:CAD>CAD>")
        self.assertEqual(f.n, 6)
        self.assertEqual(f.brackets, (Bracket("V", 0, 6), Bracket("N", 0, 3)))
        self.assertEqual(self.o.render(f), "<V:<N:CAD>CAD>")
        self.assertEqual(self.o.parse(self.o.render(f)), f)

    def test_bracket_placement_with_marks(self):
        for text in ("<N:Ca>#<V:Ca>", "<A:><B:Ca>", "Ca<N:>", "<A:<B:Ca>>", "<N:Ca+Ca>"):
            with self.subTest(text=text):
                f = self.o.parse(text)
                self.assertEqual(self.o.parse(self.o.render(f)), f)
        self.assertEqual(self.o.parse("<A:><B:Ca>").brackets, (Bracket("A", 0, 0), Bracket("B", 0, 2)))

    def test_bracket_errors(self):
        with self.assertRaises(UnparsableError) as cm:
            self.o.parse("<N:Ca")
        self.assertIn("never closed", str(cm.exception))
        with self.assertRaises(UnparsableError) as cm:
            self.o.parse("Ca>")
        self.assertIn("without a matching", str(cm.exception))
        self.assertEqual(cm.exception.loc.col, 3)

    def test_custom_delimiters(self):
        self.o.set_delimiter("open", "[")
        self.o.set_delimiter("label_end", "|")
        self.o.set_delimiter("close", "]")
        f = self.o.parse("[V|[N|CAD]CAD]")
        self.assertEqual(f.brackets, (Bracket("V", 0, 6), Bracket("N", 0, 3)))
        self.assertEqual(self.o.render(f), "[V|[N|CAD]CAD]")

    def test_syllable_marks(self):
        f = self.o.parse('"Ca.Ca"Ca')
        stress = self.fs.segment(Stress=2)
        self.assertEqual(f.pending_syllable_marks, ((0, stress), (4, stress)))
        self.assertIn(Mark.SYLLABLE, f.gaps[4])
        # A mark at the form edge stores no boundary: the edge implies one (design §13
        # entry 126, amended after P7).
        self.assertNotIn(Mark.SYLLABLE, f.gaps[0])
        self.assertEqual(self.o.render(f), '"Ca.Ca"Ca')
        self.assertEqual(self.o.parse(self.o.render(f)), f)


# --------------------------------------------------------------------------------------------
# Unparsable input
# --------------------------------------------------------------------------------------------


class TestUnparsable(unittest.TestCase):
    def test_error_has_column_and_caret(self):
        with self.assertRaises(UnparsableError) as cm:
            XS.parse("pa%%ta")
        err = cm.exception
        self.assertIsInstance(err, YascRuntimeError)
        self.assertEqual((err.offset, err.end_offset), (2, 4))
        self.assertEqual(err.loc.col, 3)
        self.assertEqual(str(err).splitlines()[1:3], ["  pa%%ta", "    ^^"])
        self.assertIn("'%%'", err.message)
        self.assertIn("OnUnparsable", str(err))

    def test_error_location_in_source(self):
        line = "   V --> [pa%] / ___"
        with self.assertRaises(UnparsableError) as cm:
            XS.parse("pa%", loc=SourceLoc("s.yasc", 4, 11), source_line=line)
        err = cm.exception
        self.assertEqual(str(err.loc), "s.yasc:4:13")
        self.assertIn("\n" + " " * 14 + "^", str(err))

    def test_skip_and_keep(self):
        skipped = XS.parse("pa%ta", on_unparsable="skip")
        self.assertEqual(skipped, XS.parse("pata"))
        kept = XS.parse("pa%ta", on_unparsable="keep")
        self.assertEqual(kept.n, 5)
        self.assertTrue(is_unknown(kept.segs[2]))
        self.assertIsInstance(kept.segs[2], OpaqueSegment)
        self.assertEqual(kept.segs[2].get("Unknown"), "!")
        self.assertEqual(kept.segs[2], XS.parse("%", on_unparsable="keep").segs[0])
        self.assertNotEqual(kept.segs[2], XS.parse("&", on_unparsable="keep").segs[0])
        self.assertNotEqual(kept.segs[2], FS.empty)
        self.assertNotEqual(FS.empty, kept.segs[2])
        self.assertEqual(XS.render(kept), "pa%ta")
        self.assertEqual(XS.parse(XS.render(kept), on_unparsable="keep"), kept)
        with self.assertRaises(ValueError):
            XS.parse("pa", on_unparsable="ignore")


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


class TestRendering(unittest.TestCase):
    def test_approximate_with_residual(self):
        seg = XS.phone_segment("a").replace({"Lat": "+"})
        text, approx, residual = XS.render_segment(seg)
        self.assertEqual(text, "a")
        self.assertTrue(approx)
        self.assertEqual(residual, (("Lat", "+", None),))
        out, approximations = XS.render_ex(Form.from_segments([XS.phone_segment("p"), seg]))
        self.assertEqual(out, "pa")
        self.assertEqual(approximations, [(1, (("Lat", "+", None),))])

    def test_diacritics_used_when_needed(self):
        seg = XS.phone_segment("k").replace({"Asp": "!", "Round": "+"})
        self.assertEqual(XS.render_segment(seg), ("k_h_w", False, ()))

    def test_memoised(self):
        seg = XS.phone_segment("k").replace({"Asp": "!"})
        self.assertIs(XS.render_segment(seg)[0], XS.render_segment(seg)[0])

    def test_ignored_features(self):
        fs = FS
        o = Orthography(fs)
        o.add_phones(["t"], {"Syll": "-", "Voice": "-", "Coronal": "!"})
        o.add_phones(["d"], {"Syll": "-", "Voice": "+", "Coronal": "!"})
        o.add_diacritic({"Asp": "!"}, "#_h")
        o.ignore(["Asp", "Place"])  # a Node ignores its descendants
        o.seal()
        self.assertIn(fs.index("Coronal"), o.ignored)
        th = fs.segment(Syll="-", Voice="-", Coronal="!", Asp="!")
        self.assertEqual(o.render_segment(th), ("t", False, ()))
        labial_t = fs.segment(Syll="-", Voice="-", Labial="!")
        self.assertEqual(o.render_segment(labial_t), ("t", False, ()))

    def test_render_tie_first_declared(self):
        fs, o = toy()
        o.add_phones("x y", {"Syll": "+"})
        o.seal()
        self.assertEqual(o.render(o.parse("y")), "x")


# --------------------------------------------------------------------------------------------
# Regressions for orig-notes/ret.py (design §8)
# --------------------------------------------------------------------------------------------


class TestRetPyRegressions(unittest.TestCase):
    def test_r18_segment_to_string(self):
        """R18: ``segment_to_string`` tested ``metric != 0`` (the *last* phone's metric)
        instead of the best one, applied at most one diacritic, and broke ties by dict order.

        Scenario: [p] is declared first and matches exactly, while the last declared phone
        does not, so ret.py would still try diacritics on an exact match. A segment needing
        two diacritics (k_h_w) must get both, and two graphemes with the same bundle must
        render as the first declared, independently of any dict order.
        """
        p = XS.phone_segment("p")
        self.assertEqual(XS.render_segment(p), ("p", False, ()))
        self.assertNotEqual(XS.phone_segment(XS.graphemes[-1]), p)
        two = XS.phone_segment("k").replace({"Asp": "!", "Round": "+"})
        self.assertEqual(XS.render_segment(two)[0], "k_h_w")
        fs, o = toy()
        o.add_phones(["z"], {"Syll": "-"})
        o.add_phones(["y"], {"Syll": "-"})
        o.seal()
        self.assertEqual(o.render_segment(o.phone_segment("y"))[0], "z")

    def test_r19_parse_segment_string(self):
        """R19: ``parse_segment_string`` built its whitespace regex as ``'(?:\\s'+igs+')+'``,
        which with ignorables x, y becomes ``(?:\\sx|y)+`` and eats every ``y`` (and only
        whitespace followed by ``x``); unparsable garbage was silently skipped.

        Scenario: with a grapheme ``y``, ``"ya y"`` keeps every ``y`` as a segment and has
        exactly one word boundary; any whitespace run is one boundary; garbage is an error
        unless ``OnUnparsable`` says otherwise.
        """
        form = XS.parse("ya y")
        self.assertEqual(form.segs, (XS.phone_segment("y"), XS.phone_segment("a"), XS.phone_segment("y")))
        self.assertEqual(form.gaps[2], frozenset({Mark.WORD}))
        self.assertEqual(XS.parse("ya \t\n y"), form)
        with self.assertRaises(UnparsableError):
            XS.parse("ya $ y")
        self.assertEqual(XS.parse("ya $ y", on_unparsable="skip"), form)


if __name__ == "__main__":
    unittest.main()
