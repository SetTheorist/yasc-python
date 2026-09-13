"""Tests for yasc.errors: SourceLoc, caret diagnostics and the error hierarchy (spec §12)."""

import unittest

import yasc
from yasc.errors import (
    MAX_REPORTED_ERRORS,
    NotImplementedYet,
    SourceLoc,
    YascDefinitionError,
    YascError,
    YascLoadError,
    YascRuntimeError,
    YascSyntaxError,
    format_excerpt,
    location_from_offset,
)


class SourceLocTests(unittest.TestCase):
    def test_str(self):
        self.assertEqual(str(SourceLoc("script.yasc", 3, 12)), "script.yasc:3:12")

    def test_str_ignores_end_col(self):
        self.assertEqual(str(SourceLoc("a.yasc", 1, 2, end_col=5)), "a.yasc:1:2")

    def test_frozen_and_hashable(self):
        loc = SourceLoc("a", 1, 1)
        with self.assertRaises(AttributeError):
            loc.line = 2  # type: ignore[misc]
        self.assertEqual(loc, SourceLoc("a", 1, 1))
        self.assertEqual(hash(loc), hash(SourceLoc("a", 1, 1)))

    def test_rejects_zero_based_positions(self):
        with self.assertRaises(ValueError):
            SourceLoc("a", 0, 1)
        with self.assertRaises(ValueError):
            SourceLoc("a", 1, 0)
        with self.assertRaises(ValueError):
            SourceLoc("a", 1, 5, end_col=4)


class LocationFromOffsetTests(unittest.TestCase):
    TEXT = "Syll Binary\nV --> {+Hi} / ___#\r\nlast"

    def test_first_line(self):
        loc, line = location_from_offset(self.TEXT, 0, "s.yasc")
        self.assertEqual(loc, SourceLoc("s.yasc", 1, 1))
        self.assertEqual(line, "Syll Binary")

    def test_span_on_later_line_strips_cr(self):
        offset = self.TEXT.index("Hi")
        loc, line = location_from_offset(self.TEXT, offset, "s.yasc", offset + 2)
        self.assertEqual(loc, SourceLoc("s.yasc", 2, 9, 11))
        self.assertEqual(line, "V --> {+Hi} / ___#")

    def test_end_of_input(self):
        loc, line = location_from_offset(self.TEXT, len(self.TEXT))
        self.assertEqual(loc, SourceLoc("<string>", 3, 5))
        self.assertEqual(line, "last")

    def test_span_clipped_to_line(self):
        loc, _ = location_from_offset(self.TEXT, 5, "s", len(self.TEXT))
        self.assertEqual(loc, SourceLoc("s", 1, 6, 12))

    def test_bad_offsets(self):
        with self.assertRaises(ValueError):
            location_from_offset("abc", 4)
        with self.assertRaises(ValueError):
            location_from_offset("abc", 2, end_offset=1)


class CaretFormattingTests(unittest.TestCase):
    def test_full_format(self):
        err = YascDefinitionError(
            "unknown feature 'Hi'",
            loc=SourceLoc("script.yasc", 3, 9, 11),
            hint="did you mean 'High'?",
            source_line="V --> {+Hi} / ___#",
        )
        self.assertEqual(
            err.format(),
            "script.yasc:3:9: error: unknown feature 'Hi'\n"
            "  V --> {+Hi} / ___#\n"
            "          ^^\n"
            "hint: did you mean 'High'?",
        )
        self.assertEqual(str(err), err.format())

    def test_at_offset_matches_explicit_location(self):
        text = "$P := Phonology [[\nV --> {+Hi} / ___#\n]]\n"
        start = text.index("Hi")
        err = YascDefinitionError.at_offset("unknown feature 'Hi'", text, start, "script.yasc", start + 2)
        self.assertEqual(err.loc, SourceLoc("script.yasc", 2, 9, 11))
        self.assertIn("\n  V --> {+Hi} / ___#\n          ^^", err.format())

    def test_single_caret_without_end_col(self):
        self.assertEqual(format_excerpt("abc", 2), "  abc\n   ^")

    def test_caret_at_end_of_line(self):
        err = YascSyntaxError(
            "expected '-->'", loc=SourceLoc("s.yasc", 1, 6), source_line="V {+}"
        )
        self.assertEqual(err.format(), "s.yasc:1:6: error: expected '-->'\n  V {+}\n       ^")

    def test_caret_at_end_of_line_from_offset(self):
        text = "V {+}\nnext"
        err = YascSyntaxError.at_offset("expected '-->'", text, 5, "s.yasc")
        self.assertEqual(err.loc, SourceLoc("s.yasc", 1, 6))
        self.assertTrue(err.format().endswith("  V {+}\n       ^"))

    def test_tabs_are_expanded_and_carets_aligned(self):
        line = "\tV\t--> {+Hi}"
        # Tab stops every 8 columns: "\t" -> 8 spaces, "V" at width 8, "\t" -> 7 spaces.
        self.assertEqual(
            format_excerpt(line, line.index("Hi") + 1, line.index("Hi") + 3),
            "  " + " " * 8 + "V" + " " * 7 + "--> {+Hi}\n"
            "  " + " " * 22 + "^^",
        )

    def test_caret_under_a_tab(self):
        self.assertEqual(format_excerpt("a\tb", 2, 3), "  a       b\n   ^^^^^^^")

    def test_combining_and_wide_characters(self):
        # "e" + COMBINING ACUTE takes one column; a CJK character takes two.
        self.assertEqual(format_excerpt("éx", 3), "  éx\n   ^")
        self.assertEqual(format_excerpt("中x", 2), "  中x\n    ^")

    def test_span_past_end_of_line_is_clipped(self):
        self.assertEqual(format_excerpt("abc", 2, 99), "  abc\n   ^^")

    def test_no_location(self):
        self.assertEqual(YascError("boom").format(), "error: boom")
        self.assertEqual(YascError("boom", hint="try again").format(), "error: boom\nhint: try again")

    def test_location_without_source_line(self):
        err = YascSyntaxError("bad token", loc=SourceLoc("f", 2, 4))
        self.assertEqual(err.format(), "f:2:4: error: bad token")


class HierarchyTests(unittest.TestCase):
    def test_subclasses(self):
        for cls in (YascLoadError, YascSyntaxError, YascDefinitionError, YascRuntimeError, NotImplementedYet):
            self.assertTrue(issubclass(cls, YascError), cls)
        self.assertTrue(issubclass(NotImplementedYet, NotImplementedError))

    def test_package_reexports(self):
        self.assertIs(yasc.YascError, YascError)
        self.assertIs(yasc.SourceLoc, SourceLoc)
        self.assertIs(yasc.NotImplementedYet, NotImplementedYet)
        self.assertEqual(yasc.__version__, "0.0.1")

    def test_runtime_error_notes(self):
        err = YascRuntimeError(
            "iteration cap reached",
            loc=SourceLoc("s.yasc", 7, 3),
            source_line="  V --> {+Nasal} / {+Nasal} ___  /*",
            record="pater",
            rule_id=4,
            rule_name="nasal-spread",
        )
        self.assertEqual((err.record, err.rule_id, err.rule_name), ("pater", 4, "nasal-spread"))
        self.assertEqual(
            err.format(),
            "s.yasc:7:3: error: iteration cap reached\n"
            "    V --> {+Nasal} / {+Nasal} ___  /*\n"
            "    ^\n"
            "note: in rule 4 'nasal-spread' for record 'pater'",
        )

    def test_runtime_error_fields_optional(self):
        err = YascRuntimeError("oops")
        self.assertIsNone(err.record)
        self.assertEqual(err.format(), "error: oops")

    def test_not_implemented_yet(self):
        err = NotImplementedYet("tier-only rules", loc=SourceLoc("s", 1, 1), phase="P8")
        self.assertEqual(err.phase, "P8")
        self.assertEqual(
            err.format(), "s:1:1: error: tier-only rules\nnote: not implemented until plan phase P8"
        )


class LoadErrorTests(unittest.TestCase):
    def make(self, n):
        return [YascSyntaxError("error %d" % i, loc=SourceLoc("s.yasc", i, 1)) for i in range(1, n + 1)]

    def test_aggregates_in_order(self):
        errors = self.make(3)
        load = YascLoadError(errors)
        self.assertEqual(load.errors, errors)
        self.assertEqual(load.omitted, 0)
        self.assertEqual(load.message, "3 errors while loading")
        self.assertEqual(
            load.format(),
            "s.yasc:1:1: error: error 1\ns.yasc:2:1: error: error 2\ns.yasc:3:1: error: error 3",
        )

    def test_exactly_the_limit_is_not_truncated(self):
        load = YascLoadError(self.make(MAX_REPORTED_ERRORS))
        self.assertEqual(len(load.reported), 20)
        self.assertNotIn("not shown", load.format())

    def test_truncates_at_20(self):
        self.assertEqual(MAX_REPORTED_ERRORS, 20)
        load = YascLoadError(self.make(25))
        self.assertEqual(len(load.errors), 25)
        self.assertEqual(len(load.reported), 20)
        self.assertEqual(load.omitted, 5)
        lines = load.format().split("\n")
        self.assertEqual(len(lines), 21)
        self.assertEqual(lines[19], "s.yasc:20:1: error: error 20")
        self.assertEqual(lines[20], "note: 5 more errors not shown (at most 20 are reported)")
        self.assertNotIn("error 21", load.format())

    def test_one_omitted_is_singular(self):
        load = YascLoadError(self.make(21))
        self.assertTrue(load.format().endswith("note: 1 more error not shown (at most 20 are reported)"))

    def test_flattens_nested_load_errors(self):
        inner = YascLoadError(self.make(2))
        extra = YascDefinitionError("late", loc=SourceLoc("t.yasc", 9, 9))
        load = YascLoadError([inner, extra])
        self.assertEqual(len(load.errors), 3)
        self.assertIs(load.errors[2], extra)

    def test_rejects_empty_and_non_errors(self):
        with self.assertRaises(ValueError):
            YascLoadError([])
        with self.assertRaises(TypeError):
            YascLoadError([ValueError("x")])  # type: ignore[list-item]

    def test_is_raisable(self):
        with self.assertRaises(YascError) as cm:
            raise YascLoadError(self.make(1))
        self.assertEqual(str(cm.exception), "s.yasc:1:1: error: error 1")


if __name__ == "__main__":
    unittest.main()
