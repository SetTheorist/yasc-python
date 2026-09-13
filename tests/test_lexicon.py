"""Tests for yasc.lexicon: record readers (spec §10.2; plan P6)."""

import os
import tempfile
import unittest

from yasc.lexicon import LexiconError, parse_features, parse_format, read_file, read_records, word_records


def recs(text, fmt="tsv"):
    return list(read_records(text.split("\n"), fmt, "lex.tsv"))


class TsvTests(unittest.TestCase):
    def test_no_header_first_column_is_form(self):
        r = recs("pater\tfather\textra\nmater\tmother")
        self.assertEqual([x.form for x in r], ["pater", "mater"])
        self.assertEqual(r[0].fields, ("pater", "father", "extra"))
        self.assertIsNone(r[0].names)
        self.assertEqual((r[0].line, r[1].line, r[0].nr, r[1].nr), (1, 2, 1, 2))
        self.assertEqual(r[0].lexical, {})
        self.assertIsNone(r[0].date)

    def test_header_names_columns_and_special_columns(self):
        text = "#! gloss form features date dialect paradigm\n" \
               "father\tpater\t+Romance !N -Common\t1954\tWest\tNoun\n"
        (r,) = recs(text)
        self.assertEqual(r.form, "pater")
        self.assertEqual(r.names, ("gloss", "form", "features", "date", "dialect", "paradigm"))
        self.assertEqual(r.lexical, {"Romance": "+", "N": "!", "Common": "-"})
        self.assertEqual((r.date, r.dialect, r.paradigm), (1954, "West", "Noun"))
        self.assertEqual(r.form_col, len("father") + 2)
        self.assertEqual(r.column("gloss"), "father")
        self.assertIsNone(r.column("nothing"))
        self.assertEqual(r.field(1), "father")

    def test_skips_blank_and_comment_lines(self):
        r = recs("%% a comment\n\n   \n  %% indented comment\npa\n\nta\n")
        self.assertEqual([(x.form, x.line, x.nr) for x in r], [("pa", 5, 1), ("ta", 7, 2)])

    def test_empty_special_columns(self):
        (r,) = recs("#! form date features dialect\npa\t\t\t\n")
        self.assertIsNone(r.date)
        self.assertEqual(r.lexical, {})
        self.assertIsNone(r.dialect)
        self.assertIsNone(r.error)

    def test_bad_date_is_record_error(self):
        r = recs("#! form date\npa\tlate\nta\t12\n")
        self.assertIsInstance(r[0].error, LexiconError)
        self.assertIn("integer", r[0].error.message)
        self.assertEqual((r[0].error.loc.line, r[0].error.loc.col), (2, 4))
        self.assertIsNone(r[1].error)
        self.assertEqual(r[1].date, 12)

    def test_negative_date(self):
        (r,) = recs("#! form date\npa\t-50\n")
        self.assertEqual(r.date, -50)

    def test_missing_field_is_empty_like_awk(self):
        (r,) = recs("pa\tx\n")
        self.assertEqual((r.field(2), r.field(3)), ("x", ""))
        with self.assertRaises(LexiconError):
            r.field(0)

    def test_crlf_and_bom(self):
        r = recs("﻿pa\tx\r\nta\r\n")
        self.assertEqual([x.form for x in r], ["pa", "ta"])
        self.assertEqual(r[0].raw, "pa\tx")

    def test_form_is_stripped_and_column_tracked(self):
        (r,) = recs("x\t  pa \n".replace("x\t", ""))
        self.assertEqual(r.form, "pa")
        self.assertEqual(r.form_col, 3)

    def test_header_can_be_repeated(self):
        r = recs("#! form gloss\npa\tx\n#! gloss form\ny\tta\n")
        self.assertEqual([x.form for x in r], ["pa", "ta"])


class OtherFormatTests(unittest.TestCase):
    def test_csv_with_quotes(self):
        (r,) = recs('#! form,gloss,features\n"pa,ta","a, b",!N\n', "csv")
        self.assertEqual(r.form, "pa,ta")
        self.assertEqual(r.fields, ("pa,ta", "a, b", "!N"))
        self.assertEqual(r.lexical, {"N": "!"})

    def test_lines_format_keeps_tabs(self):
        (r,) = recs("pa ta\tka\n", "lines")
        self.assertEqual(r.form, "pa ta\tka")
        self.assertEqual(r.fields, ("pa ta\tka",))

    def test_regex_named_groups(self):
        fmt = r"regex:(?P<gloss>\w+)=(?P<form>\w+)(?: (?P<date>-?\d+))?"
        r = recs("father=pater 1954\nmother=mater\n", fmt)
        self.assertEqual([x.form for x in r], ["pater", "mater"])
        self.assertEqual(r[0].names, ("gloss", "form", "date"))
        self.assertEqual((r[0].date, r[1].date), (1954, None))
        self.assertEqual(r[0].form_col, 8)
        self.assertEqual(r[1].fields, ("mother", "mater", ""))

    def test_regex_unnamed_groups_first_is_form(self):
        (r,) = recs("pa:x\n", r"regex:(\w+):(\w+)")
        self.assertEqual((r.form, r.fields), ("pa", ("pa", "x")))

    def test_regex_mismatch_is_record_error(self):
        r = recs("father=pater\n!!!\n", r"regex:(?P<gloss>\w+)=(?P<form>\w+)")
        self.assertIsNone(r[0].error)
        self.assertIsInstance(r[1].error, LexiconError)
        self.assertEqual(r[1].error.loc.line, 2)

    def test_parse_format(self):
        self.assertEqual(parse_format("tsv"), "tsv")
        self.assertEqual(parse_format(r"regex:(\w+)").pattern, r"(\w+)")
        for bad in ("xml", "regex:("):
            with self.assertRaises(ValueError):
                parse_format(bad)

    def test_read_file_and_words(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lex.tsv")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("#! form gloss\nbʰrater\tbrother\n")
            (r,) = list(read_file(p))
            self.assertEqual((r.form, r.source, r.line), ("bʰrater", p, 2))
        w = list(word_records(["pa", " ta "]))
        self.assertEqual([(x.form, x.source, x.line, x.nr) for x in w], [("pa", "<word>", 1, 1), ("ta", "<word>", 2, 2)])


class FeatureColumnTests(unittest.TestCase):
    def test_items(self):
        self.assertEqual(parse_features("+Romance !N -Common 2Class [H]Tone Irregular"),
                         {"Romance": "+", "N": "!", "Common": "-", "Class": "2", "Tone": "H", "Irregular": "!"})

    def test_commas_and_empty(self):
        self.assertEqual(parse_features("+A, -B"), {"A": "+", "B": "-"})
        self.assertEqual(parse_features("  "), {})

    def test_bad_item(self):
        for bad in ("+", "a-b", "{N}", "+_N"):
            with self.assertRaises(ValueError):
                parse_features(bad)


if __name__ == "__main__":
    unittest.main()
