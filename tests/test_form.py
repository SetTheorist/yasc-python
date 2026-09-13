"""Tests for yasc.form: Form values, FormLike conformance and Form.replace
(spec §5.1–§5.3, §8.2.4; design §4.3)."""

import unittest

from yasc.features import FeatureSystem, FeatureType
from yasc.form import ATTACH_RIGHT, Form, validate_brackets
from yasc.marks import Bracket, Mark, gap_satisfies
from yasc.segment import PlainView


def _system():
    fs = FeatureSystem()
    fs.add_feature("Syll", FeatureType.binary())
    fs.add_feature("Voice", FeatureType.binary())
    fs.add_feature("Stress", FeatureType.scalar(0, 2), scope="syllable")
    return fs.seal()


FS = _system()
C = FS.segment(Syll="-")
V = FS.segment(Syll="+")
X = FS.segment(Syll="-", Voice="+")
Y = FS.segment(Syll="+", Voice="-")
STRESS = FS.segment(Stress=2)


class TestConstruction(unittest.TestCase):
    def test_basic_properties(self):
        f = Form.from_segments([C, V, C], {1: [Mark.MORPHEME]})
        self.assertEqual(f.n, 3)
        self.assertEqual(len(f), 3)
        self.assertIs(f.seg(1), V)
        self.assertIsInstance(f.view(0), PlainView)
        self.assertIs(f.view(0).segment, C)
        self.assertEqual(len(f.gaps), 4)
        self.assertIsNone(f.syllables)
        self.assertEqual(f.tiers, ())
        self.assertEqual(f.pending_syllable_marks, ())

    def test_gap_marks_are_effective(self):
        f = Form.from_segments([C, V], {1: [Mark.SYLLABLE]})
        self.assertEqual(f.gaps[0], frozenset())  # stored
        self.assertEqual(f.gap_marks(0), frozenset({Mark.PHRASE}))  # effective
        self.assertEqual(f.gap_marks(2), frozenset({Mark.PHRASE}))
        self.assertEqual(f.gap_marks(1), frozenset({Mark.SYLLABLE}))
        empty = Form.from_segments([])
        self.assertEqual(empty.gap_marks(0), frozenset({Mark.PHRASE}))

    def test_marks_as_sequence(self):
        f = Form.from_segments([C, V], [[], [Mark.WORD], ()])
        self.assertEqual(f.gaps[1], frozenset({Mark.WORD}))
        with self.assertRaises(ValueError):
            Form.from_segments([C, V], [[], []])
        with self.assertRaises(ValueError):
            Form.from_segments([C], {5: [Mark.WORD]})

    def test_type_checks(self):
        with self.assertRaises(TypeError):
            Form.from_segments(["p"])
        with self.assertRaises(TypeError):
            Form.from_segments([C], {0: ["#"]})

    def test_value_semantics(self):
        a = Form.from_segments([C, V], {1: [Mark.WORD]}, [Bracket("N", 0, 1)])
        b = Form.from_segments([C, V], {1: [Mark.WORD]}, [Bracket("N", 0, 1)])
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertNotEqual(a, a.with_marks(1, []))
        self.assertTrue(a.segments_equal(a.with_marks(1, [])))
        self.assertFalse(a.segments_equal(Form.from_segments([V, C])))
        with self.assertRaises(Exception):
            a.segs = ()  # frozen

    def test_repr(self):
        f = Form.from_segments([C, V], {1: [Mark.WORD]}, [Bracket("N", 0, 2)], [(0, STRESS)])
        text = repr(f)
        self.assertTrue(text.startswith("Form("))
        self.assertIn("<N:", text)
        self.assertIn("#", text)
        self.assertIn("{-Syll}", text)


class TestBrackets(unittest.TestCase):
    def test_nested_and_sorted(self):
        inner, outer = Bracket("N", 0, 3), Bracket("V", 0, 6)
        # Brackets opening at the same gap keep their given relative order.
        self.assertEqual(validate_brackets([outer, inner], 6), (outer, inner))
        # Different opening gaps are put in document order.
        a, b = Bracket("A", 0, 6), Bracket("B", 2, 4)
        self.assertEqual(validate_brackets([b, a], 6), (a, b))

    def test_crossing_rejected(self):
        with self.assertRaises(ValueError):
            Form.from_segments([C] * 4, brackets=[Bracket("A", 0, 2), Bracket("B", 1, 3)])
        with self.assertRaises(ValueError):
            Form.from_segments([C] * 2, brackets=[Bracket("A", 0, 3)])
        with self.assertRaises(ValueError):
            Form.from_segments([C] * 2, brackets=[Bracket("A", 2, 1)])

    def test_zero_width_and_adjacent(self):
        f = Form.from_segments([C, V], brackets=[Bracket("A", 1, 1), Bracket("B", 1, 2)])
        self.assertEqual(f.brackets, (Bracket("A", 1, 1), Bracket("B", 1, 2)))

    def test_erase_and_with_brackets(self):
        f = Form.from_segments([C] * 6, brackets=[Bracket("V", 0, 6), Bracket("N", 0, 3)])
        g = f.erase_brackets(lambda b: b.label == "N")
        self.assertEqual(g.brackets, (Bracket("V", 0, 6),))
        self.assertEqual(f.erase_brackets(lambda b: True).brackets, ())
        self.assertEqual(g.with_brackets([Bracket("X", 1, 2)]).brackets, (Bracket("X", 1, 2),))


class TestFormLike(unittest.TestCase):
    def test_gap_satisfies_conformance(self):
        """Form works with yasc.marks.gap_satisfies through FormLike.gap_marks (spec §5.2)."""
        f = Form.from_segments([C, V, C, V, C], {1: [Mark.SYLLABLE], 2: [Mark.MORPHEME], 3: [Mark.WORD]})
        self.assertTrue(gap_satisfies(f.gap_marks(0), Mark.WORD))
        self.assertTrue(gap_satisfies(f.gap_marks(5), Mark.SYLLABLE))
        self.assertTrue(gap_satisfies(f.gap_marks(1), Mark.SYLLABLE))
        self.assertFalse(gap_satisfies(f.gap_marks(1), Mark.MORPHEME))
        self.assertTrue(gap_satisfies(f.gap_marks(2), Mark.MORPHEME))
        self.assertFalse(gap_satisfies(f.gap_marks(2), Mark.SYLLABLE))
        self.assertTrue(gap_satisfies(f.gap_marks(3), Mark.MORPHEME))
        self.assertFalse(gap_satisfies(f.gap_marks(4), Mark.WORD))

    def test_protocol_members(self):
        f = Form.from_segments([C], brackets=[Bracket("N", 0, 1)])
        for name in ("n", "seg", "view", "gap_marks", "brackets"):
            self.assertTrue(hasattr(f, name), name)
        self.assertEqual(f.brackets, (Bracket("N", 0, 1),))


class TestReplace(unittest.TestCase):
    def test_substitution(self):
        f = Form.from_segments([C, V, C])
        g, m = f.replace_with_map(1, 2, [Y])
        self.assertEqual(g.segs, (C, Y, C))
        self.assertEqual(m, (0, 1, 2))
        self.assertEqual(f.replace(1, 2, [Y]), g)

    def test_deletion_merges_gaps(self):
        # C . V - C  -> delete V: gaps 1 and 2 merge.
        f = Form.from_segments([C, V, C], {1: [Mark.SYLLABLE], 2: [Mark.MORPHEME]})
        g, m = f.replace_with_map(1, 2, [])
        self.assertEqual(g.segs, (C, C))
        self.assertEqual(g.gaps[1], frozenset({Mark.SYLLABLE, Mark.MORPHEME}))
        self.assertEqual(m, (0, None, 1))

    def test_deletion_of_everything(self):
        f = Form.from_segments([C, V], {1: [Mark.WORD]}, [Bracket("N", 0, 2)])
        g, m = f.replace_with_map(0, 2, [])
        self.assertEqual(g.n, 0)
        self.assertEqual(g.gaps, (frozenset({Mark.WORD}),))
        self.assertEqual(g.brackets, (Bracket("N", 0, 0),))  # kept, zero width
        self.assertEqual(m, (None, None))

    def test_marks_at_edges_and_interior(self):
        # C # V . V + C : replace V.V (1..3) by one X. Edge marks stay at the new edges,
        # the interior syllable mark moves to the left edge.
        f = Form.from_segments([C, V, V, C], {1: [Mark.WORD], 2: [Mark.SYLLABLE], 3: [Mark.MORPHEME]})
        g = f.replace(1, 3, [X])
        self.assertEqual(g.segs, (C, X, C))
        self.assertEqual(g.gaps[1], frozenset({Mark.WORD, Mark.SYLLABLE}))
        self.assertEqual(g.gaps[2], frozenset({Mark.MORPHEME}))

    def test_expansion_keeps_edge_marks(self):
        f = Form.from_segments([C, V, C], {1: [Mark.WORD], 2: [Mark.MORPHEME]})
        g, m = f.replace_with_map(1, 2, [X, Y, X])
        self.assertEqual(g.segs, (C, X, Y, X, C))
        self.assertEqual(g.gaps[1], frozenset({Mark.WORD}))
        self.assertEqual(g.gaps[2], frozenset())
        self.assertEqual(g.gaps[3], frozenset())
        self.assertEqual(g.gaps[4], frozenset({Mark.MORPHEME}))
        self.assertEqual(m, (0, 1, 4))

    def test_insertion_attach(self):
        # a # b : insert X at gap 1.
        f = Form.from_segments([C, V], {1: [Mark.WORD]})
        left, m = f.replace_with_map(1, 1, [X])  # default: joins the left word, "C X # V"
        self.assertEqual(left.segs, (C, X, V))
        self.assertEqual(left.gaps[1], frozenset())
        self.assertEqual(left.gaps[2], frozenset({Mark.WORD}))
        self.assertEqual(m, (0, 2))
        right = f.replace(1, 1, [X], attach=ATTACH_RIGHT)  # "C # X V"
        self.assertEqual(right.gaps[1], frozenset({Mark.WORD}))
        self.assertEqual(right.gaps[2], frozenset())
        with self.assertRaises(ValueError):
            f.replace(1, 1, [X], attach="middle")

    def test_insertion_at_edges(self):
        f = Form.from_segments([C, V])
        self.assertEqual(f.replace(0, 0, [X]).segs, (X, C, V))
        self.assertEqual(f.replace(2, 2, [X]).segs, (C, V, X))

    def test_bad_span(self):
        f = Form.from_segments([C, V])
        for i, j in ((-1, 0), (1, 0), (0, 3)):
            with self.assertRaises(IndexError):
                f.replace(i, j, [])

    def test_bracket_shifting(self):
        # <V: <N: C A D > C A D >
        f = Form.from_segments([C, V, C, C, V, C], brackets=[Bracket("V", 0, 6), Bracket("N", 0, 3)])
        # Insert two segments inside N: everything after shifts by 2.
        g = f.replace(1, 2, [X, X, X])
        self.assertEqual(g.brackets, (Bracket("V", 0, 8), Bracket("N", 0, 5)))
        # Delete the second "CAD": N unchanged, V shrinks.
        h = f.replace(3, 6, [])
        self.assertEqual(h.brackets, (Bracket("V", 0, 3), Bracket("N", 0, 3)))
        # Delete the whole N content: N stays as a zero-width bracket.
        k = f.replace(0, 3, [])
        self.assertEqual(k.brackets, (Bracket("V", 0, 3), Bracket("N", 0, 0)))

    def test_bracket_ends_inside_span(self):
        # <A: C V > <B: C V > ; replace V C (1..3) by X: A's end is interior -> left edge.
        f = Form.from_segments([C, V, C, V], brackets=[Bracket("A", 0, 2), Bracket("B", 2, 4)])
        g = f.replace(1, 3, [X])
        self.assertEqual(g.brackets, (Bracket("A", 0, 1), Bracket("B", 1, 3)))

    def test_insertion_at_bracket_boundary(self):
        f = Form.from_segments([C, V], brackets=[Bracket("A", 0, 1), Bracket("B", 1, 2)])
        g = f.replace(1, 1, [X])  # joins A
        self.assertEqual(g.brackets, (Bracket("A", 0, 2), Bracket("B", 2, 3)))
        h = f.replace(1, 1, [X], attach=ATTACH_RIGHT)  # joins B
        self.assertEqual(h.brackets, (Bracket("A", 0, 1), Bracket("B", 1, 3)))

    def test_align(self):
        f = Form.from_segments([C, V, C])
        _g, m = f.replace_with_map(0, 2, [X, Y], align=[1, 0])  # metathesis
        self.assertEqual(m, (1, 0, 2))
        _g, m = f.replace_with_map(0, 2, [X], align=[None, 0])
        self.assertEqual(m, (None, 0, 1))
        with self.assertRaises(ValueError):
            f.replace_with_map(0, 2, [X], align=[0])
        with self.assertRaises(ValueError):
            f.replace_with_map(0, 2, [X], align=[0, 1])

    def test_pending_syllable_marks_move_like_marks(self):
        f = Form.from_segments([C, V, C, V], pending_syllable_marks=[(2, STRESS)])
        self.assertEqual(f.replace(0, 1, []).pending_syllable_marks, ((1, STRESS),))
        self.assertEqual(f.replace(1, 3, [X]).pending_syllable_marks, ((1, STRESS),))
        self.assertEqual(f.replace(3, 4, [X, X]).pending_syllable_marks, ((2, STRESS),))

    def test_tier_hooks(self):
        """P7/P8 hooks: after_replace(i, j, k, index_map) is called and its result stored."""
        calls = []

        class Tier:
            def __init__(self, tag):
                self.tag = tag

            def after_replace(self, i, j, k, index_map):
                calls.append((self.tag, i, j, k, index_map))
                return Tier(self.tag + "'")

        f = Form(tuple([C, V, C]), (frozenset(),) * 4, (), Tier("syl"), (Tier("tone"),))
        g = f.replace(1, 2, [])
        self.assertEqual(calls, [("syl", 1, 2, 0, (0, None, 1)), ("tone", 1, 2, 0, (0, None, 1))])
        self.assertEqual(g.syllables.tag, "syl'")
        self.assertEqual(g.tiers[0].tag, "tone'")

    def test_original_unchanged(self):
        f = Form.from_segments([C, V], {1: [Mark.WORD]})
        f.replace(0, 2, [])
        self.assertEqual(f.segs, (C, V))
        self.assertEqual(f.gaps[1], frozenset({Mark.WORD}))

    def test_with_marks(self):
        f = Form.from_segments([C, V])
        g = f.with_marks(1, [Mark.CLITIC])
        self.assertEqual(g.gap_marks(1), frozenset({Mark.CLITIC}))
        self.assertEqual(f.gap_marks(1), frozenset())
        with self.assertRaises(IndexError):
            f.with_marks(3, [])


if __name__ == "__main__":
    unittest.main()
