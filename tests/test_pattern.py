"""Tests for yasc.pattern: AST nodes, canonical(), split_at_locus, number_lhs, first_specs
(spec §6.1, §6.4, §7, §8.1; design §5.1)."""

import dataclasses
import unittest

from yasc.errors import SourceLoc, YascDefinitionError
from yasc.marks import Mark
from yasc.pattern import (
    CLOSE,
    OPEN,
    Alt,
    Anything,
    AutoFloat,
    BackRef,
    Boundary,
    BracketAssert,
    Capture,
    Linked,
    Locus,
    Macro,
    Nothing,
    Opt,
    Ortho,
    Plus,
    Seq,
    Spec,
    Star,
    first_specs,
    nullable,
    number_lhs,
    split_at_locus,
    walk,
)
from yasc.segment import SegmentSpec

from tests.fakeform import make_inventory, make_system, spec


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = make_system()
        self.inv = make_inventory(self.fs)
        self.v = spec(self.fs, "+Syll")
        self.c = spec(self.fs, "-Syll")
        self.V = Spec(self.v)
        self.C = Spec(self.c)


class CanonicalTests(Base):
    """Spec §6.1 syntax, §11.3 canonical printing."""

    def test_leaves(self):
        self.assertEqual(self.V.canonical(), "{+Syll}")
        self.assertEqual(Spec(spec(self.fs, "+Syll +High", strict=True)).canonical(), "'{+Syll +High}")
        self.assertEqual(Nothing().canonical(), "0")
        self.assertEqual(Anything().canonical(), "...")
        self.assertEqual(Locus().canonical(), "___")
        self.assertEqual(BackRef(2).canonical(), "$2")
        self.assertEqual(Macro("V").canonical(), "V")
        self.assertEqual(Macro("C", Spec(spec(self.fs, "-Voice"))).canonical(), "C:{-Voice}")
        for m in Mark:
            self.assertEqual(Boundary(m).canonical(), m.value)
        self.assertEqual(BracketAssert(OPEN, "N").canonical(), "<:N")
        self.assertEqual(BracketAssert(CLOSE, "*").canonical(), ">:*")

    def test_ortho(self):
        p, a = self.inv["p"], self.inv["a"]
        o = Ortho((SegmentSpec.from_segment(p), SegmentSpec.from_segment(a)), text="pa")
        self.assertEqual(o.canonical(), "[pa]")
        strict = Ortho((SegmentSpec.from_segment(p, strict=True),), text="p")
        self.assertEqual(strict.canonical(), "'[p]")
        bare = Ortho((SegmentSpec.from_segment(a),))
        self.assertEqual(bare.canonical(), a.canonical())
        with self.assertRaises(YascDefinitionError):
            Ortho(())

    def test_composites(self):
        V, C = self.V, self.C
        self.assertEqual(Seq((C, V, Boundary(Mark.WORD))).canonical(), "{-Syll} {+Syll} #")
        self.assertEqual(Seq(()).canonical(), "0")
        self.assertEqual(Seq((C, Seq((V, V)))).canonical(), "{-Syll} {+Syll} {+Syll}")
        self.assertEqual(Alt(1, (C, Seq((V, C)), Seq(()))).canonical(), "<< {-Syll} | {+Syll} {-Syll} | 0 >>")
        self.assertEqual(Opt(Seq((C, V))).canonical(), "({-Syll} {+Syll})")
        self.assertEqual(Star(C).canonical(), "{-Syll}*")
        self.assertEqual(Star(Seq((C, V))).canonical(), "({-Syll} {+Syll})*")
        self.assertEqual(Plus(Macro("C")).canonical(), "C+")
        self.assertEqual(Plus(Opt(C)).canonical(), "(({-Syll}))+")
        self.assertEqual(Star(Alt(None, (C, V))).canonical(), "<< {-Syll} | {+Syll} >>*")
        self.assertEqual(Capture(1, Star(C)).canonical(), "{-Syll}*")
        self.assertEqual(Star(Capture(1, C)).canonical(), "{-Syll}*")
        ctx = Seq((V, Locus(), Boundary(Mark.WORD)))
        self.assertEqual(ctx.canonical(), "{+Syll} ___ #")
        self.assertEqual(str(ctx), ctx.canonical())

    def test_p8_placeholders(self):
        self.assertEqual(AutoFloat(None, "[H]").canonical(), "^[H]")
        self.assertEqual(AutoFloat("Tone", "(a)", "h").canonical(), "^Tone.(a)=h")
        self.assertEqual(Linked(Macro("V"), None, "*", name="h").canonical(), "V^*=h")
        self.assertEqual(Linked(Macro("V"), None, "[H]", exact=True).canonical(), "V^[H]'")


class NodeValueTests(Base):
    """Design §5.1: immutable nodes with an optional loc."""

    def test_immutable(self):
        s = Seq((self.C, self.V))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.items = ()
        self.assertIsInstance(Seq([self.C]).items, tuple)
        self.assertIsInstance(Alt(0, [self.C]).items, tuple)

    def test_equality_ignores_loc(self):
        loc = SourceLoc("f", 1, 2)
        self.assertEqual(Boundary(Mark.WORD, loc), Boundary(Mark.WORD))
        self.assertEqual(hash(Boundary(Mark.WORD, loc)), hash(Boundary(Mark.WORD)))
        self.assertEqual(Seq((self.C,), loc).loc, loc)
        # Specs compare by canonical text, so a re-parsed spec is equal.
        self.assertEqual(Spec(spec(self.fs, "+Syll")), self.V)
        self.assertNotEqual(Spec(spec(self.fs, "-Syll")), self.V)
        self.assertEqual(len({Spec(spec(self.fs, "+Syll")), self.V}), 1)
        self.assertNotEqual(Alt(1, (self.C,)), Alt(2, (self.C,)))

    def test_validation(self):
        with self.assertRaises(ValueError):
            BracketAssert("middle", "N")
        with self.assertRaises(TypeError):
            Boundary("#")
        with self.assertRaises(TypeError):
            Alt("x", (self.C,))
        with self.assertRaises(YascDefinitionError):
            BackRef(-1)
        with self.assertRaises(ValueError):
            Capture(-1, self.C)

    def test_walk(self):
        p = Seq((self.C, Alt(0, (self.V, Star(self.C)))))
        kinds = [type(x).__name__ for x in walk(p)]
        self.assertEqual(kinds, ["Seq", "Spec", "Alt", "Spec", "Star", "Spec"])


class SplitAtLocusTests(Base):
    """Spec §8.1: the locus appears exactly once; §8.2 contexts C ___ D."""

    def test_split(self):
        V, C = self.V, self.C
        left, right = split_at_locus(Seq((V, C, Locus(), Boundary(Mark.WORD))))
        self.assertEqual(left, Seq((V, C)))
        self.assertEqual(right, Boundary(Mark.WORD))
        self.assertEqual(split_at_locus(Locus()), (Nothing(), Nothing()))
        self.assertEqual(split_at_locus(Seq((Locus(), V))), (Nothing(), V))

    def test_errors(self):
        with self.assertRaises(YascDefinitionError):
            split_at_locus(Seq((self.V, self.C)))
        with self.assertRaises(YascDefinitionError) as cm:
            split_at_locus(Seq((Locus(), self.V, Locus(SourceLoc("f", 1, 9)))))
        self.assertEqual(cm.exception.loc.col, 9)
        with self.assertRaises(YascDefinitionError):
            split_at_locus(Seq((self.V, Alt(0, (Locus(), self.C)))))


class NumberLhsTests(Base):
    """Spec §6.4: $1, $2, ... number the top-level LHS items; $0 is the whole match."""

    def test_numbering(self):
        V, C = self.V, self.C
        lhs = Seq((Boundary(Mark.WORD), C, Star(V), Alt(3, (C, V)), Seq((C, V))))
        got = number_lhs(lhs)
        self.assertIsInstance(got, Capture)
        self.assertEqual(got.n, 0)
        items = got.pattern.items
        self.assertEqual(items[0], Boundary(Mark.WORD))
        self.assertEqual([(x.n, x.pattern) for x in items[1:]],
                         [(1, C), (2, Star(V)), (3, Alt(3, (C, V))), (4, Seq((C, V)))])
        self.assertEqual(got.canonical(), lhs.canonical())
        self.assertEqual(number_lhs(V, whole=False), Capture(1, V))
        self.assertEqual(number_lhs(Nothing()), Capture(0, Nothing()))

    def test_backref_checks(self):
        V = self.V
        number_lhs(Seq((V, BackRef(1))))  # legal: refers to an earlier item
        with self.assertRaises(YascDefinitionError):
            number_lhs(Seq((V, BackRef(2))))       # itself
        with self.assertRaises(YascDefinitionError):
            number_lhs(Seq((BackRef(2), V)))       # a later item
        with self.assertRaises(YascDefinitionError):
            number_lhs(Seq((V, BackRef(0))))       # $0 inside the LHS
        with self.assertRaises(YascDefinitionError):
            number_lhs(Seq((V, Locus())))


class FirstSpecsTests(Base):
    """Design §6 pre-filter."""

    def test_first_specs(self):
        v, c = self.v, self.c
        V, C = self.V, self.C
        self.assertEqual(first_specs(V), (v,))
        self.assertEqual(first_specs(Seq((Boundary(Mark.WORD), C, V))), (c,))
        self.assertEqual(first_specs(Seq((Opt(C), V))), (c, v))
        self.assertEqual(first_specs(Seq((Star(C), Star(V), C))), (c, v))
        self.assertEqual(first_specs(Alt(0, (C, Seq((V, C))))), (c, v))
        self.assertEqual(first_specs(Alt(0, (C, C))), (c,))     # deduplicated by identity
        self.assertEqual(first_specs(Plus(V)), (v,))
        self.assertEqual(first_specs(number_lhs(Seq((C, V)))), (c,))
        a = self.inv["a"]
        o = Ortho((SegmentSpec.from_segment(a), SegmentSpec.from_segment(a)))
        self.assertEqual(first_specs(o), (o.specs[0],))

    def test_none_cases(self):
        V, C = self.V, self.C
        for p in (Nothing(), Opt(V), Star(C), Seq((Opt(C), Boundary(Mark.WORD))), Alt(0, (V, Nothing())),
                  Anything(), Seq((Anything(), V)), Seq((BackRef(1), V)), Macro("V"), Boundary(Mark.WORD)):
            with self.subTest(p=p.canonical()):
                self.assertIsNone(first_specs(p))
        self.assertIsNone(first_specs(Alt(0, (V, Anything()))))
        self.assertFalse(nullable(Seq((V, Opt(C)))))
        self.assertTrue(nullable(Seq((Opt(V), Star(C)))))


if __name__ == "__main__":
    unittest.main()
