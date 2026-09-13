"""Tests for yasc.segment: segments, views, constraints, specs and environments (spec §4.2,
§4.3, §6.2; design §4.1, §4.2)."""

import gc
import unittest

from yasc.errors import YascDefinitionError, YascRuntimeError
from yasc.features import SYLLABLE, UNSPEC, BundleValue, FeatureSystem, FeatureType, TierDecl
from yasc.segment import (
    EMPTY,
    Absent,
    Cmp,
    Env,
    Eq,
    In,
    OpVar,
    PlainView,
    Segment,
    SegmentSpec,
    Var,
    WeakVar,
)


def build():
    fs = FeatureSystem("test")
    fs.add_feature("Syll", FeatureType.binary())                                   # 0
    fs.add_feature("Voice", FeatureType.binary())                                  # 1
    fs.add_feature("Nasal", FeatureType.binary())                                  # 2
    fs.add_feature("Place", FeatureType.node(), children=("Labial", "Coronal", "Dorsal"))  # 3
    fs.add_feature("Labial", FeatureType.unary())                                  # 4
    fs.add_feature("Coronal", FeatureType.unary())                                 # 5
    fs.add_feature("Dorsal", FeatureType.unary())                                  # 6
    fs.add_feature("High", FeatureType.binary(), aliases=("Hi",))                  # 7
    fs.add_feature("Low", FeatureType.binary())                                    # 8
    fs.add_feature("Len", FeatureType.scalar(0, 3))                                # 9
    fs.define_op("Len", "<Max>", ("3", "3", "3", "3"))
    fs.define_op("Len", "<Half>", ("0", "_", "1", "_"))
    fs.add_feature("Stress", FeatureType.scalar(0, 2), scope=SYLLABLE)             # 10
    fs.add_feature("Tone", FeatureType.enum(["[H]", "[L]", "[HL]"]), tier=TierDecl())  # 11
    fs.add_feature("Round", FeatureType.enum(["+", "-"]))                          # 12
    return fs.seal()


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = build()
        self.F = self.fs.feature

    def seg(self, **kw):
        return self.fs.segment(kw)

    def spec(self, *cons, strict=False, output=False):
        return SegmentSpec(self.fs, cons, strict=strict, output=output)


class SegmentTests(Base):
    """Design §4.1."""

    def test_interning_equality_hash(self):
        a = self.seg(Syll="+", High="+")
        b = self.fs.segment({"Hi": "+", self.F("Syll"): "+"})
        self.assertIs(a, b)
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        c = Segment.make(self.fs, list(a.values))
        self.assertIs(c, a)
        self.assertNotEqual(a, self.seg(Syll="+"))
        self.assertEqual(len({a, b, c, self.seg(Syll="+")}), 2)
        # A segment built after the interned one is gone is still equal and hashes the same.
        values = a.values
        h = hash(a)
        del a, b, c
        gc.collect()
        d = Segment.make(self.fs, values)
        self.assertEqual(hash(d), h)
        self.assertEqual(d, self.seg(Syll="+", High="+"))

    def test_segments_of_different_systems_differ(self):
        other = build()
        a = self.seg(Syll="+")
        b = other.segment(Syll="+")
        self.assertEqual(a.values, b.values)
        self.assertNotEqual(a, b)

    def test_immutable(self):
        a = self.seg(Syll="+")
        with self.assertRaises(AttributeError):
            a.extra = 1
        self.assertIsInstance(a.values, tuple)

    def test_make_checks_width(self):
        with self.assertRaises(ValueError):
            Segment.make(self.fs, (None,))

    def test_get_and_repr(self):
        s = self.seg(Syll="+", Voice="-", Labial="!", Len=2, Tone="[HL]")
        self.assertEqual(s.get("Syll"), "+")
        self.assertEqual(s.get(1), "-")
        self.assertEqual(s.get(self.F("Len")), "2")
        self.assertIsNone(s.get("Nasal"))
        self.assertEqual(s.get("Place"), "!")
        self.assertEqual(repr(s), "{+Syll -Voice !Labial 2Len [HL]Tone}")
        self.assertEqual(repr(self.fs.empty), "{}")
        self.assertEqual(s.to_dict(), {"Syll": "+", "Voice": "-", "Labial": "!", "Len": "2", "Tone": "HL"})
        with self.assertRaises(YascDefinitionError):
            self.seg(Place="!")
        with self.assertRaises(YascDefinitionError):
            self.seg(Syll="x")

    def test_node_presence(self):
        v = self.seg(Syll="+")
        self.assertFalse(v.present("Place"))
        self.assertIsNone(v.get("Place"))
        p = self.seg(Syll="-", Dorsal="!")
        self.assertTrue(p.present("Place"))
        self.assertTrue(PlainView(p).present("Place"))
        self.assertEqual(PlainView(p).get("Dorsal"), "!")
        self.assertEqual(p.bundle("Place"), BundleValue(((4, None), (5, None), (6, "!"))))
        self.assertEqual(p.bundle("Place").format(self.fs), "<_Labial _Coronal !Dorsal>")
        with self.assertRaises(YascDefinitionError):
            p.bundle("Syll")

    def test_replace_merge_diff(self):
        a = self.seg(Syll="-", Voice="-", Labial="!")
        b = a.replace({"Voice": "+", "Syll": None})
        self.assertEqual(b, self.seg(Voice="+", Labial="!"))
        self.assertEqual(a.replace({"Place": None}), self.seg(Syll="-", Voice="-"))
        self.assertEqual(a.diff(b), frozenset({0, 1}))
        self.assertEqual(a.diff(a), frozenset())
        m = a.merge(self.seg(Voice="+", Nasal="+"))
        self.assertEqual(m, self.seg(Syll="-", Voice="+", Nasal="+", Labial="!"))


class EnvTests(Base):
    """Design §4.2."""

    def test_persistent_binding(self):
        e1 = EMPTY.bind_var("a", "+")
        e2 = e1.bind_var("b", "-")
        self.assertIsNone(EMPTY.var("a"))
        self.assertEqual(e1.var("a"), "+")
        self.assertIsNone(e1.var("b"))
        self.assertEqual(e2.vars, {"a": "+", "b": "-"})
        self.assertIs(e1.bind_var("a", "+"), e1)
        self.assertEqual(e1.bind_var("a", "-").var("a"), "-")
        self.assertEqual(e1.var("a"), "+")
        self.assertTrue(EMPTY.is_empty)
        with self.assertRaises(ValueError):
            EMPTY.bind_var("a", None)

    def test_equality_order_independent(self):
        x = EMPTY.bind_var("b", "1").bind_var("a", "2")
        y = EMPTY.bind_var("a", "2").bind_var("b", "1")
        self.assertEqual(x, y)
        self.assertEqual(hash(x), hash(y))
        self.assertEqual(x, Env.of(vars={"a": "2", "b": "1"}))
        self.assertNotEqual(x, EMPTY)
        self.assertEqual(len({x, y, EMPTY}), 2)

    def test_unify(self):
        e = EMPTY.unify_var("a", "+")
        self.assertEqual(e.var("a"), "+")
        self.assertIs(e.unify_var("a", "+"), e)
        self.assertIsNone(e.unify_var("a", "-"))
        u = EMPTY.unify_var("a", UNSPEC)
        self.assertIs(u.var("a"), UNSPEC)
        self.assertTrue(u.has_var("a"))

    def test_namespaces(self):
        e = EMPTY.bind_cap(1, (0, 2)).bind_alt(7, 1).bind_auto("h", 42).bind_var("a", "+")
        self.assertEqual(e.cap(1), (0, 2))
        self.assertIsNone(e.cap(2))
        self.assertEqual(e.alt(7), 1)
        self.assertEqual(e.auto("h"), 42)
        self.assertEqual(e.caps, {1: (0, 2)})
        self.assertEqual(e.alts, {7: 1})
        self.assertEqual(e.autos, {"h": 42})
        # namespaces are separate
        self.assertIsNone(e.var("h"))
        self.assertNotEqual(EMPTY.bind_cap(1, (0, 1)), EMPTY.bind_alt(1, (0, 1)))
        self.assertIn("caps", repr(e))

    def test_agrees_op(self):
        t = self.F("Len").type
        e = EMPTY.bind_var("a", "1")
        self.assertTrue(e.agrees_op("a", t, ("++",), "2"))
        self.assertFalse(e.agrees_op("a", t, ("++",), "1"))
        self.assertTrue(e.agrees_op("a", t, ("<Max>",), "3"))
        self.assertFalse(e.agrees_op("a", t, ("<Half>",), "0"))  # undefined result
        self.assertFalse(e.agrees_op("b", t, ("++",), "2"))
        self.assertFalse(EMPTY.bind_var("a", UNSPEC).agrees_op("a", t, ("++",), "1"))


class ConstraintTableTests(Base):
    """Every row of the spec §6.2 table, for match and output."""

    # Row 1: vF
    def test_eq(self):
        s = self.spec(Eq(self.F("High"), "+"), Eq(self.F("Len"), 2), Eq(self.F("Tone"), "[H]"),
                      Eq(self.F("Labial")))
        good = self.seg(High="+", Len=2, Tone="H", Labial="!", Syll="+")
        self.assertEqual(s.match(good), (EMPTY,))
        self.assertEqual(s.match(good.replace({"High": "-"})), ())
        self.assertEqual(s.match(good.replace({"High": None})), ())
        out = s.for_output().apply(self.fs.empty)
        self.assertEqual(out, self.seg(High="+", Len=2, Tone="H", Labial="!"))
        self.assertEqual(s.canonical(), "{!Labial +High 2Len [H]Tone}")
        with self.assertRaises(YascDefinitionError):
            Eq(self.F("High"), "!")

    # Row 2: _F
    def test_absent(self):
        s = self.spec(Absent(self.F("Voice")))
        self.assertTrue(s.matches(self.seg(Syll="+")))
        self.assertFalse(s.matches(self.seg(Voice="-")))
        self.assertEqual(s.apply(self.seg(Voice="-", Syll="+")), self.seg(Syll="+"))
        self.assertEqual(s.canonical(), "{_Voice}")

    # Row 3: {v1 v2}F
    def test_in(self):
        s = self.spec(In(self.F("Len"), [3, "1"]))
        self.assertTrue(s.matches(self.seg(Len=1)))
        self.assertTrue(s.matches(self.seg(Len=3)))
        self.assertFalse(s.matches(self.seg(Len=2)))
        self.assertFalse(s.matches(self.seg()))
        self.assertEqual(s.canonical(), "{{1 3}Len}")
        self.assertEqual(self.spec(In(self.F("Tone"), ["L", "H"])).canonical(), "{{[H] [L]}Tone}")
        with self.assertRaises(YascDefinitionError):
            s.for_output()
        with self.assertRaises(YascDefinitionError):
            self.spec(In(self.F("Len"), [1]), output=True)
        with self.assertRaises(YascDefinitionError):
            In(self.F("Len"), [7])
        with self.assertRaises(YascDefinitionError):
            In(self.F("Place"), ["!"])

    # Row 4: >n F etc.
    def test_cmp(self):
        L = self.F("Len")
        cases = {">": ["2", "3"], "<": ["0", "1"], ">=": ["1", "2", "3"], "<=": ["0", "1", "2"]}
        n = {">": 1, "<": 2, ">=": 1, "<=": 2}
        for op, expected in cases.items():
            s = self.spec(Cmp(L, op, n[op]))
            got = [v for v in L.type.values if s.matches(self.seg(Len=v))]
            self.assertEqual(got, expected, op)
            self.assertFalse(s.matches(self.seg()))
            with self.assertRaises(YascDefinitionError):
                s.for_output()
        self.assertEqual(self.spec(Cmp(L, ">=", 2)).canonical(), "{>=2Len}")
        self.assertFalse(self.spec(Cmp(L, ">", 7)).matches(self.seg(Len=3)))
        with self.assertRaises(YascDefinitionError):
            Cmp(self.F("High"), ">", 0)
        with self.assertRaises(YascDefinitionError):
            Cmp(L, "=", 1)

    # Row 5: (a)F
    def test_var(self):
        s = self.spec(Var(self.F("High"), "a"))
        (env,) = s.match(self.seg(High="-"))
        self.assertEqual(env.var("a"), "-")
        self.assertEqual(s.match(self.seg()), ())  # F must be specified
        self.assertEqual(s.match(self.seg(High="-"), env), (env,))
        self.assertEqual(s.match(self.seg(High="+"), env), ())
        # output: set F := a
        self.assertEqual(s.for_output().apply(self.seg(High="+", Syll="+"), env), self.seg(High="-", Syll="+"))
        self.assertEqual(s.apply(self.seg(High="+"), EMPTY.bind_var("a", UNSPEC)), self.seg())
        with self.assertRaises(YascRuntimeError) as cm:
            s.apply(self.seg(), EMPTY)
        self.assertIn("unbound", cm.exception.message)
        self.assertEqual(s.canonical(), "{(a)High}")

    # Row 6: (?a)F
    def test_weak_var(self):
        s = self.spec(WeakVar(self.F("Voice"), "a"))
        (e1,) = s.match(self.seg(Voice="+"))
        self.assertEqual(e1.var("a"), "+")
        (e2,) = s.match(self.seg())
        self.assertIs(e2.var("a"), UNSPEC)
        self.assertEqual(s.match(self.seg(), e2), (e2,))
        self.assertEqual(s.match(self.seg(Voice="+"), e2), ())
        self.assertEqual(s.match(self.seg(), e1), ())
        # output copies exactly, including unspecification
        self.assertEqual(s.for_output().apply(self.seg(Voice="-"), e2), self.seg())
        self.assertEqual(s.apply(self.seg(Voice="-"), e1), self.seg(Voice="+"))
        self.assertEqual(s.canonical(), "{(?a)Voice}")
        # an ordinary (a) occurrence cannot match an UNSPEC binding
        self.assertEqual(self.spec(Var(self.F("Voice"), "a")).match(self.seg(Voice="+"), e2), ())

    # Row 7: op(a)F
    def test_op_var_bound(self):
        s = self.spec(OpVar(self.F("High"), "a", ("-",)))
        env = EMPTY.bind_var("a", "+")
        self.assertEqual(s.match(self.seg(High="-"), env), (env,))
        self.assertEqual(s.match(self.seg(High="+"), env), ())
        self.assertEqual(s.match(self.seg(), env), ())
        self.assertEqual(s.for_output().apply(self.seg(High="+"), env), self.seg(High="-"))
        self.assertEqual(s.canonical(), "{-(a)High}")
        self.assertIsInstance(Var(self.F("High"), "a", ("-",)), OpVar)

    def test_op_var_unbound_binds_preimages(self):
        s = self.spec(OpVar(self.F("High"), "a", ("-",)))
        (env,) = s.match(self.seg(High="+"))
        self.assertEqual(env.var("a"), "-")  # R7: bound to the preimage, not the value
        s2 = self.spec(OpVar(self.F("Len"), "n", ("++",)))
        envs = s2.match(self.seg(Len=3))
        self.assertEqual([e.var("n") for e in envs], ["2", "3"])
        self.assertEqual(s2.match(self.seg(Len=0)), ())

    def test_non_injective_op(self):
        s = self.spec(OpVar(self.F("Len"), "a", ("<Max>",)))
        envs = s.match(self.seg(Len=3))
        self.assertEqual([e.var("a") for e in envs], ["0", "1", "2", "3"])
        self.assertEqual(s.match(self.seg(Len=2)), ())
        # bound: any value maps to 3
        self.assertTrue(s.matches(self.seg(Len=3), EMPTY.bind_var("a", "0")))
        self.assertEqual(s.apply(self.seg(Len=0), EMPTY.bind_var("a", "1")), self.seg(Len=3))

    def test_op_composition_in_spec(self):
        # ++#<Half>(a): Half first, then ++.  a=2 -> Half 1 -> ++ 2
        s = self.spec(OpVar(self.F("Len"), "a", ("++", "<Half>")))
        self.assertEqual(s.canonical(), "{++#<Half>(a)Len}")
        self.assertEqual(s.apply(self.seg(), EMPTY.bind_var("a", "2")), self.seg(Len=2))
        self.assertEqual([e.var("a") for e in s.match(self.seg(Len=1))], ["0"])
        r = self.spec(OpVar(self.F("Len"), "a", ("<Half>", "++")))  # ++ first: a=1 -> 2 -> Half 1
        self.assertEqual(r.apply(self.seg(), EMPTY.bind_var("a", "1")), self.seg(Len=1))
        self.assertEqual([e.var("a") for e in r.match(self.seg(Len=1))], ["1"])
        # ++ first then <Half>: 0->1->_, 1->2->1, 2->3->_, 3->3->_; nothing reaches 0.
        self.assertEqual([e.var("a") for e in r.match(self.seg(Len=0))], [])

    def test_undefined_op_result(self):
        s = self.spec(OpVar(self.F("Len"), "a", ("<Half>",)))
        env = EMPTY.bind_var("a", "1")  # <Half>(1) is undefined
        # match: fails whatever the value
        for v in (None, 0, 1, 2, 3):
            seg = self.seg() if v is None else self.seg(Len=v)
            self.assertEqual(s.match(seg, env), ())
        # output: leaves the feature unspecified
        self.assertEqual(s.apply(self.seg(Len=2, Syll="+"), env), self.seg(Syll="+"))
        self.assertEqual(s.apply(self.seg(Len=2), EMPTY.bind_var("a", "2")), self.seg(Len=1))
        self.assertEqual(s.apply(self.seg(Len=2), EMPTY.bind_var("a", UNSPEC)), self.seg())

    def test_unknown_op(self):
        with self.assertRaises(YascDefinitionError) as cm:
            OpVar(self.F("High"), "a", ("++",))
        self.assertIn("High", cm.exception.message)
        with self.assertRaises(YascDefinitionError):
            OpVar(self.F("Place"), "a", ("-",))

    # Row 8: F with no value = !F on Unary
    def test_bare_unary(self):
        self.assertEqual(Eq(self.F("Labial")).value, "!")
        with self.assertRaises(YascDefinitionError):
            Eq(self.F("Voice"))

    def test_empty_spec_matches_anything(self):
        s = self.spec()
        for seg in (self.fs.empty, self.seg(Syll="+", Dorsal="!")):
            self.assertEqual(s.match(seg), (EMPTY,))
        self.assertIs(s.apply(self.seg(Syll="+")), self.seg(Syll="+"))
        self.assertEqual(s.canonical(), "{}")

    def test_foreign_feature_rejected(self):
        other = build()
        with self.assertRaises(YascDefinitionError):
            SegmentSpec(self.fs, [Eq(other.feature("Syll"), "+")])


class VariableSharingTests(Base):
    """Spec §6.2: a variable may span features with identical value sets, or Binary ones."""

    def test_binary_sharing(self):
        s = self.spec(Var(self.F("High"), "a"), OpVar(self.F("Low"), "a", ("-",)))
        self.assertTrue(s.matches(self.seg(High="+", Low="-")))
        self.assertFalse(s.matches(self.seg(High="+", Low="+")))
        env = EMPTY.bind_var("a", "+")
        out = self.spec(Var(self.F("Voice"), "a"), output=True).apply(self.seg(), env)
        self.assertEqual(out, self.seg(Voice="+"))

    def test_identical_sets_binary_and_adhoc(self):
        # Round is an ad-hoc "+ -" enum: same value set as Binary.
        s = self.spec(Var(self.F("High"), "a"), Var(self.F("Round"), "a"))
        self.assertTrue(s.matches(self.seg(High="-", Round="-")))
        self.assertFalse(s.matches(self.seg(High="-", Round="+")))

    def test_incompatible_value_fails_to_match(self):
        env = EMPTY.bind_var("a", "+")
        self.assertFalse(self.spec(Var(self.F("Len"), "a")).matches(self.seg(Len=1), env))
        self.assertFalse(self.spec(OpVar(self.F("Len"), "a", ("++",))).matches(self.seg(Len=1), env))
        self.assertFalse(self.spec(WeakVar(self.F("Len"), "a")).matches(self.seg(Len=1), env))
        # Output of an invalid value is a run-time error (a compiler check should prevent it).
        with self.assertRaises(YascRuntimeError):
            self.spec(Var(self.F("Len"), "a")).apply(self.seg(), env)
        with self.assertRaises(YascRuntimeError):
            self.spec(OpVar(self.F("Len"), "a", ("++",))).apply(self.seg(), env)

    def test_two_unbound_op_vars_multiply(self):
        s = self.spec(OpVar(self.F("Len"), "a", ("<Max>",)), OpVar(self.F("Syll"), "b", ("-",)))
        envs = s.match(self.seg(Len=3, Syll="+"))
        self.assertEqual(len(envs), 4)
        self.assertEqual([(e.var("b"), e.var("a")) for e in envs],
                         [("-", "0"), ("-", "1"), ("-", "2"), ("-", "3")])


class NodeTests(Base):
    """Spec §4.3."""

    def test_presence_match(self):
        present = self.spec(Eq(self.F("Place")))
        absent = self.spec(Absent(self.F("Place")))
        v = self.seg(Syll="+")
        c = self.seg(Syll="-", Coronal="!")
        self.assertFalse(present.matches(v))
        self.assertTrue(present.matches(c))
        self.assertTrue(absent.matches(v))
        self.assertFalse(absent.matches(c))
        self.assertEqual(present.canonical(), "{!Place}")

    def test_node_present_output_is_illegal(self):
        with self.assertRaises(YascDefinitionError) as cm:
            self.spec(Eq(self.F("Place")), output=True)
        self.assertIn("daughter", cm.exception.hint)
        with self.assertRaises(YascDefinitionError):
            Eq(self.F("Place"), "+")

    def test_delink(self):
        s = self.spec(Absent(self.F("Place")), output=True)
        out = s.apply(self.seg(Syll="-", Nasal="+", Labial="!", Dorsal="!"))
        self.assertEqual(out, self.seg(Syll="-", Nasal="+"))
        self.assertFalse(out.present("Place"))

    def test_bind_bundle_including_unspecified(self):
        s = self.spec(Var(self.F("Place"), "p"))
        (env,) = s.match(self.seg(Syll="-", Coronal="!"))
        self.assertEqual(env.var("p"), BundleValue(((4, None), (5, "!"), (6, None))))
        self.assertEqual(s.match(self.seg(Syll="+")), ())  # (p)Place needs a present node
        self.assertEqual(s.match(self.seg(Coronal="!", Syll="+"), env), (env,))
        self.assertEqual(s.match(self.seg(Coronal="!", Labial="!"), env), ())

    def test_place_assimilation(self):
        """{+Nasal} --> {(p)Place} / ___ {-Syll (p)Place}, at the segment level."""
        n = self.seg(Syll="-", Nasal="+", Voice="+", Coronal="!")
        k = self.seg(Syll="-", Nasal="-", Voice="-", Dorsal="!")
        lhs = self.spec(Eq(self.F("Nasal"), "+"))
        ctx = self.spec(Eq(self.F("Syll"), "-"), Var(self.F("Place"), "p"))
        rhs = self.spec(Var(self.F("Place"), "p"), output=True)
        (env,) = lhs.match(n)
        (env,) = ctx.match(k, env)
        out = rhs.apply(n, env)
        self.assertEqual(out, self.seg(Syll="-", Nasal="+", Voice="+", Dorsal="!"))
        self.assertIsNone(out.get("Coronal"))  # replaced, not merged
        # A vowel has no Place: the context fails.
        self.assertEqual(ctx.match(self.seg(Syll="-")), ())

    def test_weak_var_on_node(self):
        s = self.spec(WeakVar(self.F("Place"), "p"))
        (env,) = s.match(self.seg(Syll="+"))
        self.assertIs(env.var("p"), UNSPEC)
        self.assertEqual(s.apply(self.seg(Labial="!", Syll="-"), env), self.seg(Syll="-"))
        (env2,) = s.match(self.seg(Labial="!"))
        self.assertEqual(s.apply(self.seg(Dorsal="!"), env2), self.seg(Labial="!"))

    def test_bundle_on_wrong_feature(self):
        env = EMPTY.bind_var("p", self.seg(Labial="!").bundle("Place"))
        self.assertFalse(self.spec(Var(self.F("High"), "p")).matches(self.seg(High="+"), env))
        with self.assertRaises(YascRuntimeError):
            self.spec(Var(self.F("High"), "p")).apply(self.seg(), env)
        with self.assertRaises(YascRuntimeError):
            self.spec(Var(self.F("Place"), "p")).apply(self.seg(), EMPTY.bind_var("p", "+"))
        with self.assertRaises(YascRuntimeError):
            self.spec(Var(self.F("Place"), "p")).apply(self.seg(), EMPTY.bind_var("p", BundleValue(((4, "!"),))))


class StrictAndWeakTests(Base):
    """Spec §6.1 ('S), §8.2.4 (~{...}, '{...})."""

    def test_strict_match(self):
        s = self.spec(Eq(self.F("Syll"), "+"), Eq(self.F("High"), "+"), strict=True)
        self.assertTrue(s.matches(self.seg(Syll="+", High="+")))
        self.assertFalse(s.matches(self.seg(Syll="+", High="+", Low="-")))
        # Syllable-scope and tier features are not the segment's own: ignored by strictness.
        self.assertTrue(s.matches(self.seg(Syll="+", High="+", Stress=2, Tone="H")))
        self.assertEqual(s.canonical(), "'{+Syll +High}")

    def test_strict_with_node_var(self):
        s = self.spec(Var(self.F("Place"), "p"), Eq(self.F("Nasal"), "+"), strict=True)
        self.assertTrue(s.matches(self.seg(Nasal="+", Labial="!", Dorsal="!")))
        self.assertFalse(s.matches(self.seg(Nasal="+", Labial="!", Voice="+")))

    def test_strict_from_segment(self):
        p = self.seg(Syll="-", Voice="-", Labial="!")
        s = SegmentSpec.from_segment(p, strict=True)
        self.assertTrue(s.matches(p))
        self.assertFalse(s.matches(p.replace({"Nasal": "-"})))
        self.assertTrue(SegmentSpec.from_segment(p).matches(p.replace({"Nasal": "-"})))

    def test_strict_output_replaces(self):
        s = self.spec(Eq(self.F("Syll"), "-"), Eq(self.F("Labial")), strict=True, output=True)
        out = s.apply(self.seg(Syll="+", High="+", Voice="+"))
        self.assertEqual(out, self.seg(Syll="-", Labial="!"))

    def test_weak_apply(self):
        s = self.spec(Eq(self.F("Voice"), "+"), Eq(self.F("High"), "-"), Absent(self.F("Syll")),
                      Eq(self.F("Dorsal")), output=True)
        seg = self.seg(Syll="+", Voice="-")
        self.assertEqual(s.weak_apply(seg), self.seg(Syll="+", Voice="-", High="-", Dorsal="!"))
        # a present node is not filled
        env = EMPTY.bind_var("p", self.seg(Dorsal="!").bundle("Place"))
        w = self.spec(Var(self.F("Place"), "p"))
        self.assertEqual(w.weak_apply(self.seg(Labial="!"), env), self.seg(Labial="!"))
        self.assertEqual(w.weak_apply(self.seg(), env), self.seg(Dorsal="!"))
        # weak (?a) with UNSPEC does nothing
        self.assertEqual(self.spec(WeakVar(self.F("Voice"), "a")).weak_apply(self.seg(), EMPTY.bind_var("a", UNSPEC)),
                         self.seg())


class SpecMiscTests(Base):
    """Spec §6.1, §6.2; design §4.1, §4.2."""

    def test_sorted_and_flags(self):
        s = self.spec(Eq(self.F("Low"), "-"), Var(self.F("Syll"), "a"), Eq(self.F("Voice"), "+"))
        self.assertEqual([c.index for c in s.constraints], [0, 1, 8])
        self.assertTrue(s.has_vars)
        self.assertFalse(self.spec(Eq(self.F("Low"), "-")).has_vars)
        self.assertEqual(s.var_names(), frozenset({"a"}))
        self.assertEqual(self.spec(Var(self.F("Place"), "p")).features_read(), frozenset({3, 4, 5, 6}))

    def test_combine(self):
        a = self.spec(Eq(self.F("Syll"), "+"))
        b = self.spec(Eq(self.F("Voice"), "-"), strict=True)
        c = a.combine(b)
        self.assertTrue(c.strict)
        self.assertEqual(c.canonical(), "'{+Syll -Voice}")

    def test_with_op(self):
        s = self.spec(Var(self.F("High"), "a"), Eq(self.F("Syll"), "+"), OpVar(self.F("Len"), "n", ("++",)))
        with self.assertRaises(YascDefinitionError):
            s.with_op("-")  # Len has no (-)
        s2 = self.spec(Var(self.F("High"), "a"), OpVar(self.F("Low"), "a", ("-",)), Eq(self.F("Syll"), "+"))
        self.assertEqual(s2.with_op("-").canonical(), "{+Syll -(a)High -#-(a)Low}")
        with self.assertRaises(YascDefinitionError):
            self.spec(WeakVar(self.F("High"), "a")).with_op("-")

    def test_views(self):
        s = self.spec(Eq(self.F("Syll"), "+"))
        seg = self.seg(Syll="+")
        self.assertEqual(s.match(PlainView(seg)), (EMPTY,))

        stress = self.F("Stress").index

        class StrictView:
            """A Form-like view: syllable features come from elsewhere."""

            def __init__(self, segment, stress_value):
                self.segment = segment
                self.stress_value = stress_value
                self.reads = []

            def __getitem__(self, i):
                self.reads.append(i)
                return self.stress_value if i == stress else self.segment.values[i]

            def get(self, feature):
                return self[feature]

            def present(self, feature):
                return self[feature] is not None

        view = StrictView(seg, "2")
        self.assertTrue(s.is_plain())
        self.assertTrue(s.matches(view))
        self.assertEqual(view.reads, [])  # fast path: plain spec reads the segment directly
        st = self.spec(Eq(self.F("Stress"), 2), Eq(self.F("Syll"), "+"))
        self.assertFalse(st.is_plain())
        self.assertTrue(st.matches(view))
        self.assertIn(stress, view.reads)
        self.assertFalse(st.matches(seg))  # the bare segment has no stress
        self.assertTrue(self.spec(Cmp(self.F("Stress"), ">", 1)).matches(StrictView(seg, "2")))

    def test_apply_returns_interned(self):
        s = self.spec(Eq(self.F("Voice"), "+"), output=True)
        a = s.apply(self.seg(Syll="+"))
        self.assertIs(a, self.seg(Syll="+", Voice="+"))
        self.assertIs(s.apply(a), a)


if __name__ == "__main__":
    unittest.main()
