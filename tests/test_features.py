"""Tests for yasc.features: feature types, operations, geometry, implications (spec §4)."""

import unittest

from yasc.errors import YascDefinitionError, YascLoadError, YascRuntimeError
from yasc.features import (
    BINARY,
    ENUM,
    NODE,
    SCALAR,
    SYLLABLE,
    UNARY,
    FeatureSystem,
    FeatureType,
    TierDecl,
)
from yasc.segment import Absent, Eq, SegmentSpec, Var


class FeatureTypeTests(unittest.TestCase):
    """Spec §4.1, §4.4."""

    def test_kinds_and_values(self):
        self.assertEqual(FeatureType.unary().values, ("!",))
        self.assertEqual(FeatureType.binary().values, ("+", "-"))
        self.assertEqual(FeatureType.scalar().values, tuple(str(i) for i in range(10)))
        self.assertEqual(FeatureType.scalar(1, 3).values, ("1", "2", "3"))
        self.assertEqual(FeatureType.enum(["[H]", "L", "[HL]"]).values, ("H", "L", "HL"))
        self.assertEqual(FeatureType.node().values, ())
        self.assertEqual(FeatureType.enum(["+", "-"]).kind, ENUM)
        for t, k in ((FeatureType.unary(), UNARY), (FeatureType.binary(), BINARY),
                     (FeatureType.scalar(), SCALAR), (FeatureType.node(), NODE)):
            self.assertEqual(t.kind, k)

    def test_invalid_declarations(self):
        with self.assertRaises(YascDefinitionError):
            FeatureType.scalar(3, 1)
        with self.assertRaises(YascDefinitionError):
            FeatureType.enum(["H", "H"])
        with self.assertRaises(YascDefinitionError):
            FeatureType.enum(["a b"])
        with self.assertRaises(YascDefinitionError):
            FeatureType.enum(["_"])
        with self.assertRaises(YascDefinitionError):
            FeatureType.enum([])
        with self.assertRaises(YascDefinitionError):
            FeatureType(BINARY, ("+", "0"))

    def test_coerce(self):
        t = FeatureType.scalar(0, 2)
        self.assertEqual(t.coerce(2), "2")
        self.assertEqual(t.coerce("1"), "1")
        with self.assertRaises(YascDefinitionError) as cm:
            t.coerce(5, feature_name="Stress")
        self.assertIn("Stress", cm.exception.message)
        self.assertIn("0 1 2", cm.exception.hint)
        e = FeatureType.enum(["H", "L"])
        self.assertEqual(e.coerce("[H]"), "H")
        self.assertEqual(e.coerce("L"), "L")
        with self.assertRaises(YascDefinitionError):
            e.coerce("M")

    def test_builtin_ops(self):
        b = FeatureType.binary()
        self.assertEqual(b.apply("-", "+"), "-")
        self.assertEqual(b.apply("-", "-"), "+")
        s = FeatureType.scalar(1, 3)
        self.assertEqual([s.apply("++", v) for v in s.values], ["2", "3", "3"])
        self.assertEqual([s.apply("--", v) for v in s.values], ["1", "1", "2"])
        self.assertEqual(FeatureType.unary().ops, {})
        with self.assertRaises(YascDefinitionError) as cm:
            s.apply("-", "1")
        self.assertIn("(++)", cm.exception.hint)

    def test_declared_op_with_undefined_results(self):
        t = FeatureType.enum(["H", "L", "M"]).with_op("<M->", ("M", "_", None))
        self.assertEqual(t.apply("<M->", "H"), "M")
        self.assertIsNone(t.apply("<M->", "L"))
        self.assertIsNone(t.apply("<M->", "M"))
        # Parenthesised names are accepted and normalised.
        t2 = FeatureType.binary().with_op("(-)", ("+", "+"))
        self.assertEqual(t2.apply("-", "+"), "+")  # declared op replaces the built-in
        with self.assertRaises(YascDefinitionError) as cm:
            FeatureType.binary().with_op("~", ("+",))
        self.assertIn("one result per value", cm.exception.hint)
        with self.assertRaises(YascDefinitionError):
            FeatureType.binary().with_op("Max", ("+", "+"))
        with self.assertRaises(YascDefinitionError):
            FeatureType.binary().with_op("~", ("+", "X"))

    def test_preimages_injective_and_not(self):
        s = FeatureType.scalar(0, 2).with_op("<Max>", (2, 2, 2))
        self.assertEqual(s.preimages("<Max>", "2"), ("0", "1", "2"))
        self.assertEqual(s.preimages("<Max>", "0"), ())
        self.assertEqual(s.preimages("++", "2"), ("1", "2"))  # saturation is non-injective
        self.assertEqual(s.preimages("++", "0"), ())
        self.assertEqual(FeatureType.binary().preimages("-", "+"), ("-",))
        t = FeatureType.enum(["H", "L"]).with_op("~", ("L", "_"))
        self.assertEqual(t.preimages("~", "L"), ("H",))
        self.assertEqual(t.preimages("~", "H"), ())

    def test_composition_order(self):
        # <Dbl> doubles: 0->0, 1->2, 2 and 3 undefined.
        t = FeatureType.scalar(0, 3).with_op("<Dbl>", ("0", "2", "_", "_"))
        # op1#op2(a) = op1(op2(a)): the op nearest the variable applies first (spec §4.4).
        self.assertIsNone(t.apply_ops(("<Dbl>", "++"), "1"))   # ++ first: 2, then Dbl(2) undefined
        self.assertEqual(t.apply_ops(("++", "<Dbl>"), "1"), "3")  # Dbl first: 2, then ++ -> 3
        self.assertEqual(t.apply_ops(("--", "--"), "3"), "1")
        self.assertEqual(t.apply_ops((), "2"), "2")
        self.assertEqual(t.preimages_ops(("++", "<Dbl>"), "3"), ("1",))
        self.assertEqual(t.preimages_ops(("<Dbl>", "++"), "0"), ())
        self.assertEqual(t.preimages_ops(("<Dbl>", "--"), "0"), ("0", "1"))
        fwd, inv = t.compose(("++", "<Dbl>"))
        self.assertEqual(fwd, {"0": "1", "1": "3", "2": None, "3": None})
        self.assertIs(t.compose(("++", "<Dbl>"))[0], fwd)  # cached

    def test_shares_values(self):
        b = FeatureType.binary()
        self.assertTrue(b.shares_values_with(FeatureType.binary()))
        self.assertTrue(FeatureType.scalar(0, 2).shares_values_with(FeatureType.scalar(0, 2)))
        self.assertFalse(FeatureType.scalar(0, 2).shares_values_with(FeatureType.scalar(0, 3)))
        self.assertFalse(b.shares_values_with(FeatureType.unary()))
        self.assertTrue(FeatureType.node().shares_values_with(FeatureType.node()))

    def test_equality_and_canonical(self):
        self.assertEqual(FeatureType.binary(), FeatureType.binary())
        self.assertNotEqual(FeatureType.binary(), FeatureType.binary().with_op("~", ("+", "+")))
        self.assertEqual(hash(FeatureType.scalar(1, 3)), hash(FeatureType.scalar(1, 3)))
        self.assertEqual(FeatureType.scalar(1, 3).canonical(), "Scalar(1,3)")
        self.assertEqual(FeatureType.enum(["H", "+"]).canonical(), "[H] +")


def small_system():
    fs = FeatureSystem("small")
    fs.add_feature("Syll", FeatureType.binary())
    fs.add_feature("Voice", FeatureType.binary())
    fs.add_feature("Place", FeatureType.node(), children=("Labial", "Coronal"))
    fs.add_feature("Labial", FeatureType.unary())
    fs.add_feature("Coronal", FeatureType.unary())
    fs.add_feature("High", FeatureType.binary(), aliases=("Hi", "hi"))
    fs.add_feature("Stress", FeatureType.scalar(0, 2), scope=SYLLABLE)
    fs.define_op("Stress", "<Max>", ("2", "2", "2"))
    fs.add_feature("Tone", FeatureType.enum(["[H]", "[L]"]), tier=TierDecl(tbu=None, stray="float"))
    return fs


class RegistrationTests(unittest.TestCase):
    """Spec §4.1, §4.3, §5.4, §5.5, §6.2."""

    def test_names_aliases_and_flags(self):
        fs = small_system().seal()
        high = fs.feature("High")
        self.assertIs(fs.feature("Hi"), high)
        self.assertIs(fs.feature("hi"), high)
        self.assertEqual(high.aliases, ("Hi", "hi"))
        self.assertEqual([f.index for f in fs.features], list(range(len(fs))))
        self.assertEqual(fs.index("Voice"), 1)
        self.assertEqual(fs.feature("Stress").scope, SYLLABLE)
        self.assertFalse(fs.feature("Stress").is_plain)
        self.assertIsInstance(fs.feature("Tone").tier, TierDecl)
        self.assertTrue(fs.feature("Syll").is_plain)
        self.assertIn("Hi", fs)
        self.assertEqual(fs.apply_op("Stress", "<Max>", "0"), "2")
        self.assertEqual(fs.preimages("High", "-", "+"), ("-",))

    def test_unknown_feature_hint(self):
        fs = small_system().seal()
        with self.assertRaises(YascDefinitionError) as cm:
            fs.feature("Voic")
        self.assertEqual(cm.exception.hint, "did you mean 'Voice'?")
        self.assertIn("unknown feature 'Voic'", str(cm.exception))
        with self.assertRaises(YascDefinitionError) as cm:
            fs.feature("SYLL")
        self.assertEqual(cm.exception.hint, "did you mean 'Syll'?")
        with self.assertRaises(YascDefinitionError) as cm:
            fs.feature("Hii")
        self.assertEqual(cm.exception.hint, "did you mean 'Hi' (alias of 'High')?")
        with self.assertRaises(YascDefinitionError) as cm:
            fs.feature("Zzzzzz")
        self.assertIsNone(cm.exception.hint)
        self.assertIsNone(fs.get("Zzzzzz"))

    def test_duplicate_and_invalid_names(self):
        fs = small_system()
        with self.assertRaises(YascDefinitionError):
            fs.add_feature("Voice", FeatureType.binary())
        with self.assertRaises(YascDefinitionError):
            fs.add_feature("Hi", FeatureType.binary())
        with self.assertRaises(YascDefinitionError):
            fs.add_aliases("Syll", "Voice")
        with self.assertRaises(YascDefinitionError):
            fs.add_feature("2x", FeatureType.binary())
        with self.assertRaises(YascDefinitionError):
            fs.add_feature("X", FeatureType.binary(), children=("Syll",))
        with self.assertRaises(YascDefinitionError):
            fs.add_feature("X", FeatureType.binary(), scope="word")
        with self.assertRaises(YascDefinitionError):
            fs.define_op("Place", "~", ())
        with self.assertRaises(YascDefinitionError):
            TierDecl(stray="nowhere")

    def test_sealed_is_immutable(self):
        fs = small_system().seal()
        self.assertTrue(fs.sealed)
        self.assertIs(fs.seal(), fs)
        with self.assertRaises(YascDefinitionError):
            fs.add_feature("New", FeatureType.binary())
        with self.assertRaises(YascDefinitionError):
            fs.add_aliases("Syll", "Sy")
        with self.assertRaises(YascDefinitionError):
            fs.define_op("High", "~", ("+", "+"))
        spec = SegmentSpec(fs, [Eq(fs.feature("Syll"), "+")])
        with self.assertRaises(YascDefinitionError):
            fs.add_implication(spec, spec)

    def test_segments_need_sealed_system(self):
        fs = small_system()
        with self.assertRaises(YascDefinitionError):
            fs.segment(Syll="+")


class GeometryTests(unittest.TestCase):
    """Spec §4.3."""

    def test_daughters_before_and_after(self):
        fs = FeatureSystem()
        fs.add_feature("Labial", FeatureType.unary())  # before the node
        fs.add_feature("Place", FeatureType.node(), children=("Labial", "Dorsal", "Tongue"))
        fs.add_feature("Dorsal", FeatureType.unary())  # after the node
        fs.add_feature("Tongue", FeatureType.node(), children=("Back",))
        fs.add_feature("Back", FeatureType.binary())
        fs.seal()
        place = fs.feature("Place")
        self.assertEqual([c.name for c in place.children], ["Labial", "Dorsal", "Tongue"])
        self.assertEqual(place.descendants, (0, 2, 4))
        self.assertIs(fs.feature("Back").parent, fs.feature("Tongue"))
        self.assertIs(fs.feature("Tongue").parent, place)
        self.assertIsNone(place.parent)
        self.assertEqual(fs.feature("Labial").descendants, ())
        self.assertEqual(fs.leaf_indices, (0, 2, 4))

    def test_unknown_daughter(self):
        fs = FeatureSystem()
        fs.add_feature("Place", FeatureType.node(), children=("Labal",))
        fs.add_feature("Labial", FeatureType.unary())
        with self.assertRaises(YascDefinitionError) as cm:
            fs.seal()
        self.assertIn("'Labal'", cm.exception.message)
        self.assertEqual(cm.exception.hint, "did you mean 'Labial'?")
        self.assertFalse(fs.sealed)

    def test_two_parents(self):
        fs = FeatureSystem()
        fs.add_feature("A", FeatureType.node(), children=("x",))
        fs.add_feature("B", FeatureType.node(), children=("x",))
        fs.add_feature("x", FeatureType.binary())
        with self.assertRaises(YascDefinitionError) as cm:
            fs.seal()
        self.assertIn("two parents", cm.exception.message)

    def test_cycle(self):
        fs = FeatureSystem()
        fs.add_feature("A", FeatureType.node(), children=("B",))
        fs.add_feature("B", FeatureType.node(), children=("C",))
        fs.add_feature("C", FeatureType.node(), children=("A",))
        with self.assertRaises(YascDefinitionError) as cm:
            fs.seal()
        self.assertIn("cyclic", cm.exception.message)

    def test_self_daughter_and_multiple_errors(self):
        fs = FeatureSystem()
        fs.add_feature("A", FeatureType.node(), children=("A",))
        with self.assertRaises(YascDefinitionError):
            fs.seal()
        fs = FeatureSystem()
        fs.add_feature("A", FeatureType.node(), children=("Q", "R"))
        with self.assertRaises(YascLoadError) as cm:
            fs.seal()
        self.assertEqual(len(cm.exception.errors), 2)

    def test_phonix_geometry(self):
        """The Phonix geometry of spec §4.3 / orig-notes/scer.txt, built programmatically."""
        tree = [
            ("ROOT", ["Place", "Glottal", "Manner"]),
            ("Place", ["Labial", "Coronal", "Dorsal"]),
            ("Labial", ["ro"]), "ro",
            ("Coronal", ["ant", "dist"]), "ant", "dist",
            ("Dorsal", ["hi", "lo", "bk", "fr"]), "hi", "lo", "bk", "fr",
            ("Glottal", ["vc", "sg", "cg"]), "vc", "sg", "cg",
            ("Manner", ["Class", "cont", "nas", "str", "lat", "dr"]),
            "cont", "nas", "str", "lat", "dr",
            ("Class", ["cons", "syll", "son"]), "cons", "syll", "son",
        ]
        fs = FeatureSystem("Phonix")
        for item in tree:
            if isinstance(item, tuple):
                fs.add_feature(item[0], FeatureType.node(), children=item[1])
            else:
                fs.add_feature(item, FeatureType.binary())
        fs.seal()
        root = fs.feature("ROOT")
        self.assertEqual(len(root.descendants), 18)
        self.assertEqual(set(root.descendants), set(fs.leaf_indices))
        self.assertEqual([c.name for c in fs.feature("Manner").children],
                         ["Class", "cont", "nas", "str", "lat", "dr"])
        self.assertIs(fs.feature("Class").parent, fs.feature("Manner"))
        self.assertIs(fs.feature("Place").parent, root)
        self.assertEqual([fs.features[i].name for i in fs.feature("Place").descendants],
                         ["ro", "ant", "dist", "hi", "lo", "bk", "fr"])
        seg = fs.segment(ro="+")
        self.assertEqual(seg.get("Labial"), "!")
        self.assertEqual(seg.get("Place"), "!")
        self.assertEqual(seg.get("ROOT"), "!")
        self.assertIsNone(seg.get("Glottal"))
        self.assertIsNone(seg.get("Coronal"))
        # Place assimilation across the deep geometry: the whole Place subtree is copied.
        f = fs.feature
        n = fs.segment(nas="+", ant="+", cons="+")
        k = fs.segment(nas="-", hi="+", bk="+", cons="+")
        env = SegmentSpec(fs, [Var(f("Place"), "p")]).match(k)[0]
        out = SegmentSpec(fs, [Var(f("Place"), "p")], output=True).apply(n, env)
        self.assertEqual(out, fs.segment(nas="+", hi="+", bk="+", cons="+"))


def binary_system(*names):
    fs = FeatureSystem()
    for n in names:
        fs.add_feature(n, FeatureType.binary())
    return fs


def spec(fs, *items, strict=False):
    """Tiny test helper: items are (value, name) for Eq, ('_', name) for Absent,
    ('(a)', name) for Var."""
    cons = []
    for value, name in items:
        feat = fs.feature(name)
        if value == "_":
            cons.append(Absent(feat))
        elif value.startswith("("):
            cons.append(Var(feat, value[1:-1]))
        else:
            cons.append(Eq(feat, value))
    return SegmentSpec(fs, cons, strict)


class ImplicationTests(unittest.TestCase):
    """Spec §4.5; design §3."""

    def test_strong_and_weak(self):
        fs = binary_system("Syll", "Voice", "Nasal")
        fs.add_implication(spec(fs, ("+", "Syll")), spec(fs, ("+", "Voice")), weak=True)
        fs.add_implication(spec(fs, ("+", "Nasal")), spec(fs, ("+", "Voice")))
        fs.seal()
        # weak: fills only unspecified features
        self.assertEqual(fs.close_new(fs.segment(Syll="+")), fs.segment(Syll="+", Voice="+"))
        self.assertEqual(fs.close_new(fs.segment(Syll="+", Voice="-")), fs.segment(Syll="+", Voice="-"))
        # strong: forces the value
        self.assertEqual(fs.close_new(fs.segment(Nasal="+", Voice="-")), fs.segment(Nasal="+", Voice="+"))
        seg = fs.segment(Syll="-", Nasal="-")
        self.assertIs(fs.close_new(seg), seg)
        self.assertEqual(fs.apply_implications(fs.segment(Nasal="+"), [2]), fs.segment(Nasal="+", Voice="+"))
        self.assertEqual(fs.implications[0].canonical(), "{+Syll} ~~> {+Voice}")
        self.assertEqual(fs.implications[1].canonical(), "{+Nasal} --> {+Voice}")

    def test_only_trigger_features_trigger(self):
        """A change to a feature outside the trigger does not re-fire the implication."""
        fs = binary_system("Voice", "Nasal")
        fs.add_implication(spec(fs, ("+", "Nasal")), spec(fs, ("+", "Voice")))
        fs.seal()
        seg = fs.segment(Nasal="+", Voice="-")  # e.g. a rule just devoiced a nasal
        self.assertIs(fs.close(seg, {fs.index("Voice")}), seg)
        self.assertEqual(fs.close(seg, {fs.index("Nasal")}), fs.segment(Nasal="+", Voice="+"))
        self.assertIs(fs.close(seg, ()), seg)

    def test_high_low(self):
        fs = binary_system("High", "Low")
        fs.add_implication(spec(fs, ("+", "High")), spec(fs, ("-", "Low")))
        fs.add_implication(spec(fs, ("+", "Low")), spec(fs, ("-", "High")))
        fs.seal()
        # A rule lowered a high vowel: only Low changed, so the rule's change wins.
        seg = fs.segment(High="+", Low="+")
        self.assertEqual(fs.close(seg, {fs.index("Low")}), fs.segment(High="-", Low="+"))
        # A fresh segment: declaration order decides.
        self.assertEqual(fs.close_new(seg), fs.segment(High="+", Low="-"))

    def test_chain_needs_second_pass(self):
        fs = binary_system("A", "B", "C", "D")
        fs.add_implication(spec(fs, ("+", "C")), spec(fs, ("+", "D")))
        fs.add_implication(spec(fs, ("+", "B")), spec(fs, ("+", "C")))
        fs.add_implication(spec(fs, ("+", "A")), spec(fs, ("+", "B")))
        fs.seal()
        out = fs.close(fs.segment(A="+"), {fs.index("A")})
        self.assertEqual(out, fs.segment(A="+", B="+", C="+", D="+"))

    def test_chain_in_one_pass(self):
        fs = binary_system("A", "B", "C")
        fs.add_implication(spec(fs, ("+", "A")), spec(fs, ("+", "B")))
        fs.add_implication(spec(fs, ("+", "B")), spec(fs, ("-", "C")), weak=True)
        fs.seal()
        self.assertEqual(fs.close(fs.segment(A="+"), [0]), fs.segment(A="+", B="+", C="-"))

    def test_absent_trigger_equals_weak(self):
        """{+Syll _Voice} --> {+Voice} is exactly {+Syll} ~~> {+Voice} (spec §4.5)."""
        strong = binary_system("Syll", "Voice")
        strong.add_implication(spec(strong, ("+", "Syll"), ("_", "Voice")), spec(strong, ("+", "Voice")))
        strong.seal()
        weak = binary_system("Syll", "Voice")
        weak.add_implication(spec(weak, ("+", "Syll")), spec(weak, ("+", "Voice")), weak=True)
        weak.seal()
        cases = [{}, {"Syll": "+"}, {"Syll": "-"}, {"Syll": "+", "Voice": "-"},
                 {"Syll": "+", "Voice": "+"}, {"Voice": "-"}, {"Syll": "-", "Voice": "-"}]
        for case in cases:
            a = strong.close_new(strong.segment(case))
            b = weak.close_new(weak.segment(case))
            self.assertEqual(a.values, b.values, case)
            for changed in ([0], [1], [0, 1]):
                a = strong.close(strong.segment(case), changed)
                b = weak.close(weak.segment(case), changed)
                self.assertEqual(a.values, b.values, (case, changed))
        # In particular a rule that unsets Voice on a vowel gets it refilled by both forms.
        self.assertEqual(weak.close(weak.segment(Syll="+"), [1]).values, ("+", "+"))
        self.assertEqual(strong.implications[0].trigger_features, weak.implications[0].trigger_features)

    def test_variables_in_implications(self):
        fs = binary_system("Syll", "Voice", "Nasal")
        fs.add_implication(spec(fs, ("+", "Syll"), ("(a)", "Voice")), spec(fs, ("(a)", "Nasal")))
        fs.seal()
        self.assertEqual(fs.close_new(fs.segment(Syll="+", Voice="-")), fs.segment(Syll="+", Voice="-", Nasal="-"))
        self.assertEqual(fs.close_new(fs.segment(Syll="+")), fs.segment(Syll="+"))  # (a) needs a value

    def test_target_variable_must_be_bound(self):
        fs = binary_system("Syll", "Voice")
        with self.assertRaises(YascDefinitionError) as cm:
            fs.add_implication(spec(fs, ("+", "Syll")), spec(fs, ("(a)", "Voice")))
        self.assertIn("'a'", cm.exception.message)

    def test_empty_trigger_always_applies(self):
        fs = binary_system("A", "B")
        fs.add_implication(SegmentSpec(fs, []), spec(fs, ("-", "B")), weak=True)
        fs.seal()
        self.assertEqual(fs.close(fs.segment(A="+"), [0]), fs.segment(A="+", B="-"))

    def test_cycle_error(self):
        fs = binary_system("X", "Y")
        fs.add_implication(spec(fs, ("+", "X")), spec(fs, ("-", "Y")))
        fs.add_implication(spec(fs, ("-", "Y")), spec(fs, ("-", "X")))
        fs.add_implication(spec(fs, ("-", "X")), spec(fs, ("+", "Y")))
        fs.add_implication(spec(fs, ("+", "Y")), spec(fs, ("+", "X")))
        fs.seal()
        with self.assertRaises(YascRuntimeError) as cm:
            fs.close(fs.segment(X="+", Y="+"), {0})
        self.assertIn("implication cycle", cm.exception.message)
        self.assertIn("16 passes", cm.exception.message)
        self.assertIn("-->", cm.exception.hint)

    def test_self_toggle_is_not_a_cycle(self):
        fs = binary_system("X")
        fs.add_implication(spec(fs, ("+", "X")), spec(fs, ("-", "X")))
        fs.seal()
        self.assertEqual(fs.close_new(fs.segment(X="+")), fs.segment(X="-"))

    def test_bidirectional(self):
        fs = binary_system("Syll", "Voice")
        a, b = fs.add_bidirectional(spec(fs, ("+", "Syll")), spec(fs, ("+", "Voice")))
        fs.seal()
        self.assertEqual(a.canonical(), "{+Syll} --> {+Voice}")
        self.assertEqual(b.canonical(), "{+Voice} --> {+Syll}")
        self.assertEqual(fs.close_new(fs.segment(Voice="+")), fs.segment(Syll="+", Voice="+"))
        self.assertEqual(a.origin, "{+Syll} <----> {+Voice}")

    def test_bidirectional_mixed_halves(self):
        fs = binary_system("S", "T")
        # S <~~--> T: left half (T -> S) weak, right half (S -> T) strong.
        a, b = fs.add_bidirectional(spec(fs, ("+", "S")), spec(fs, ("+", "T")), backward_weak=True)
        self.assertFalse(a.weak)
        self.assertTrue(b.weak)
        self.assertEqual(a.origin, "{+S} <~~--> {+T}")
        c, d = fs.add_bidirectional(spec(fs, ("-", "S")), spec(fs, ("-", "T")), forward_weak=True,
                                    backward_weak=True)
        self.assertTrue(c.weak and d.weak)
        self.assertEqual(c.origin, "{-S} <~~~~> {-T}")
        self.assertEqual(len(fs.implications), 4)
        fs.seal()
        self.assertEqual(fs.close_new(fs.segment(S="+", T="-")), fs.segment(S="+", T="+"))
        self.assertEqual(fs.close_new(fs.segment(T="+", S="-")), fs.segment(S="-", T="+"))

    def test_bidirectional_with_op(self):
        """{(a)High} <--(-)--> {(a)Low}: -High implies +Low, literally (spec §4.5 caution)."""
        fs = binary_system("High", "Low")
        a, b = fs.add_bidirectional(spec(fs, ("(a)", "High")), spec(fs, ("(a)", "Low")), op="-")
        self.assertEqual(a.canonical(), "{(a)High} --> {-(a)Low}")
        self.assertEqual(b.canonical(), "{(a)Low} --> {-(a)High}")
        self.assertEqual(a.origin, "{(a)High} <--(-)--> {(a)Low}")
        fs.seal()
        self.assertEqual(fs.close_new(fs.segment(High="-")), fs.segment(High="-", Low="+"))
        self.assertEqual(fs.close_new(fs.segment(Low="-")), fs.segment(High="+", Low="-"))
        self.assertEqual(fs.close(fs.segment(High="+", Low="-"), [1]), fs.segment(High="+", Low="-"))

    def test_bidirectional_op_must_exist(self):
        fs = FeatureSystem()
        fs.add_feature("S", FeatureType.scalar(0, 2))
        fs.add_feature("T", FeatureType.scalar(0, 2))
        with self.assertRaises(YascDefinitionError):
            fs.add_bidirectional(spec(fs, ("(a)", "S")), spec(fs, ("(a)", "T")), op="-")
        a, _ = fs.add_bidirectional(spec(fs, ("(a)", "S")), spec(fs, ("(a)", "T")), op="++")
        fs.seal()
        self.assertEqual(a.canonical(), "{(a)S} --> {++(a)T}")

    def test_system_canonical(self):
        fs = small_system()
        fs.add_implication(spec(fs, ("+", "High")), spec(fs, ("-", "Syll")))
        text = fs.canonical()
        self.assertIn("Place Node(Labial Coronal)", text)
        self.assertIn("  == Hi hi", text)
        self.assertIn("  (<Max>) 2 2 2", text)
        self.assertIn("Stress Scalar(0,2) Scope(Syllable)", text)
        self.assertIn("{+High} --> {-Syll}", text)
        self.assertNotIn("(++)", text)


if __name__ == "__main__":
    unittest.main()
