"""Tests for yasc.lexer: logical lines, comments, continuation, locations and the
mode-sensitive tokenizer (spec §3; design §7.1)."""

import unittest

from yasc.errors import YascSyntaxError
from yasc.lexer import Lexer, escape_string, split_lines, tokenize, unescape_string


def kinds(text, mode="pattern"):
    return [t.kind for t in tokenize(text, mode)]


def texts(text, mode="pattern"):
    return [t.text for t in tokenize(text, mode)]


class LogicalLineTests(unittest.TestCase):
    """Spec §3.2 comments, §3.3 blocks and continuation."""

    def test_percent_comments(self):
        lines = split_lines("V --> 0 %% comment\n%%% whole line\nC --> 0 %%%x")
        self.assertEqual([l.text for l in lines], ["V --> 0", "C --> 0"])

    def test_hash_comment_lines(self):
        src = "# comment\n#\n   #\tindented comment\n#C --> 0\n#_ --> 0\n  # V --> 0"
        self.assertEqual([l.text for l in split_lines(src)], ["#C --> 0", "#_ --> 0"])

    def test_hash_mid_line_is_not_a_comment(self):
        lines = split_lines("V --> 0 / ___ # %% end")
        self.assertEqual(lines[0].text, "V --> 0 / ___ #")
        self.assertEqual(texts("V --> 0 / ___#")[-1], "#")

    def test_comment_markers_inside_brackets_and_strings(self):
        lines = split_lines('[%%] {+Syll}\n!print "a %% b" $_\n{F} ==> [#/\\]')
        self.assertEqual([l.text for l in lines], ['[%%] {+Syll}', '!print "a %% b" $_', '{F} ==> [#/\\]'])

    def test_continuation(self):
        lines = split_lines("A --> B /\\ ignored rest\n  / C ___\nD --> E")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].text, "A --> B    / C ___")
        toks = tokenize("A --> B /\\ junk\n  / C ___")
        c = [t for t in toks if t.text == "C"][0]
        self.assertEqual((c.loc.line, c.loc.col), (2, 5))
        self.assertEqual(toks[0].loc.line, 1)

    def test_physical_locations(self):
        lines = split_lines("\n\n   V --> {+Voice}  %% x\n")
        lex = Lexer(lines[0])
        toks = [lex.next("pattern") for _ in range(3)]
        self.assertEqual([(t.loc.line, t.loc.col, t.loc.end_col) for t in toks],
                         [(3, 4, 5), (3, 6, 9), (3, 10, 18)])

    def test_block_tokens_split_lines(self):
        lines = split_lines("$R := Rules [[ /:<\n  x --> y ]] /:1\n]]")
        self.assertEqual([l.text for l in lines], ["$R := Rules [[ /:<", "x --> y", "]] /:1", "]]"])
        self.assertEqual([(l.opens, l.closes) for l in lines],
                         [(True, False), (False, False), (False, True), (False, True)])

    def test_slash_quote_is_not_a_string(self):
        lines = split_lines('V --> 0 /" R1 %% named')
        self.assertEqual(lines[0].text, 'V --> 0 /" R1')


class PatternModeTests(unittest.TestCase):
    """Spec §3.4 ambiguities (design §7.2)."""

    def test_minus_versus_arrow(self):
        self.assertEqual(kinds("V - --> -"), ["IDENT", "BOUNDARY", "ARROW", "BOUNDARY"])
        self.assertEqual(texts("V-->-"), ["V", "-->", "-"])

    def test_dot_versus_ellipsis(self):
        self.assertEqual(kinds(". ... ...."), ["BOUNDARY", "ANYTHING", "ANYTHING", "BOUNDARY"])

    def test_zero(self):
        self.assertEqual(kinds("0 $0"), ["NOTHING", "BACKREF"])
        spec = tokenize("{0Stress}")[0]
        self.assertEqual(spec.kind, "SPEC")
        self.assertEqual((spec.value[0].kind, spec.value[0].value), ("value", "0"))
        with self.assertRaises(YascSyntaxError):
            tokenize("01")

    def test_hash_and_double_hash(self):
        self.assertEqual(texts("## # ###"), ["##", "#", "##", "#"])

    def test_alternation_versus_bracket_assertions(self):
        toks = tokenize("<< <:N >:* >>")
        self.assertEqual([t.kind for t in toks], ["LALT", "BRACKET_OPEN", "BRACKET_CLOSE", "RALT"])
        self.assertEqual([toks[1].value, toks[2].value], ["N", "*"])

    def test_locus(self):
        self.assertEqual(kinds("_ __ ___ #_"), ["LOCUS", "LOCUS", "LOCUS", "BOUNDARY", "LOCUS"])
        self.assertEqual(texts("V___C_x"), ["V", "___", "C_x"])

    def test_modifiers(self):
        vals = [t.value for t in tokenize('/ /! /* /:* /:1 /:> /:< /~ /:~ /:Raw /:$ /:T /:@-40 /%12.5 /%5:each '
                                          '/??? /???:each /" R1 /:: /:C* /:i+ /:F- /:L+ /:D- /:C+')]
        self.assertEqual(vals, [
            ("/", None), ("/!", None), ("/*", None), ("/:*", None), ("/:1", None), ("/:>", None), ("/:<", None),
            ("/:~", None), ("/:~", None), ("/:Raw", None), ("/:$", None), ("/:T", None), ("/:@", -40),
            ("/%", (12.5, False)), ("/%", (5.0, True)), ("/???", False), ("/???", True), ('/"', "R1"),
            ("/::", None), ("/:C*", None), ("/:i+", None), ("/:F-", None), ("/:L+", None), ("/:D-", None),
            ("/:C+", None)])
        self.assertEqual(texts("/*/:>"), ["/*", "/:>"])

    def test_unknown_modifier(self):
        with self.assertRaises(YascSyntaxError) as cm:
            tokenize("V --> 0 /:Q")
        self.assertIn("unknown modifier '/:Q'", cm.exception.message)

    def test_bracketed_orthography_is_literal(self):
        toks = tokenize("[#\\'] '[t] [{#}] C:[p t k]")
        self.assertEqual([t.value for t in toks if t.kind == "ORTHO"], ["#\\'", "t", "{#}", "p t k"])
        with self.assertRaises(YascSyntaxError):
            tokenize("[abc")

    def test_tier_elements(self):
        t = tokenize("V^Tone.[H]'=h")[1]
        self.assertEqual((t.kind, t.value["tier"], t.value["x"], t.value["exact"], t.value["name"]),
                         ("TIER", "Tone", "[H]", "'", "h"))
        vals = [t.value["x"] for t in tokenize("^0 ^* ^(a) ^=h") if t.kind == "TIER"]
        self.assertEqual(vals, ["0", "*", "(a)", None])
        self.assertEqual(tokenize("V^+[L]")[1].value["op"], "+")

    def test_backref_and_var(self):
        toks = tokenize("$12 $name")
        self.assertEqual([(t.kind, t.value) for t in toks], [("BACKREF", 12), ("VAR", "name")])


class SpecModeTests(unittest.TestCase):
    """Spec §6.2 constraint forms inside {}."""

    def test_every_constraint_form(self):
        cons = tokenize("{+High !Labial [H]Tone 2Stress _Voice {1 2}Stress >=1Stress < 2 Stress (a)Back "
                        "(?b)Nasal -(a)Low <M->#<-M>(t)Tone Coronal ...}")[0].value
        self.assertEqual([c.kind for c in cons], ["value", "value", "value", "value", "absent", "in", "cmp", "cmp",
                                                  "var", "weakvar", "var", "var", "bare", "ellipsis"])
        self.assertEqual(cons[2].value, "[H]")
        self.assertEqual(cons[5].values, ("1", "2"))
        self.assertEqual((cons[6].cmp, cons[6].n), (">=", 1))
        self.assertEqual((cons[7].cmp, cons[7].n, cons[7].feature), ("<", 2, "Stress"))
        self.assertEqual(cons[11].ops, ("<M->", "<-M>"))
        self.assertEqual([c.canonical() for c in cons[:12]],
                         ["+High", "!Labial", "[H]Tone", "2Stress", "_Voice", "{1 2}Stress", ">=1Stress",
                          "<2Stress", "(a)Back", "(?b)Nasal", "-(a)Low", "<M->#<-M>(t)Tone"])

    def test_constraint_locations(self):
        cons = tokenize("{+Syll  -Voice}")[0].value
        self.assertEqual([(c.loc.col, c.loc.end_col) for c in cons], [(2, 7), (9, 15)])

    def test_errors(self):
        for bad in ("{+Syll", "{-(?a)F}", "{{1 2} F}", "{+Syll }Voice}}"):
            with self.subTest(bad=bad):
                with self.assertRaises(YascSyntaxError):
                    tokenize(bad)


class OtherModeTests(unittest.TestCase):
    """Phonology, orthography, top and string modes (design §7.1)."""

    def test_phon_mode(self):
        toks = tokenize("Tone [H] [L] + - ! 1 _ == To", "phon")
        self.assertEqual([t.kind for t in toks],
                         ["IDENT", "VALUE", "VALUE", "VALUE", "VALUE", "VALUE", "VALUE", "VALUE", "ALIAS", "IDENT"])
        self.assertEqual(toks[1].value, "H")

    def test_implication_arrows(self):
        for arrow in ("-->", "~~>", "<-->", "<~~>", "<~~-->", "<--~~>", "<~~~~>", "<--(-)-->", "<~~(<M->)-->",
                      "<---->"):
            with self.subTest(arrow=arrow):
                toks = tokenize("{+A} %s {+B}" % arrow, "phon")
                self.assertEqual([t.kind for t in toks], ["SPEC", "IARROW", "SPEC"])
                self.assertEqual(toks[1].text, arrow)

    def test_ortho_mode(self):
        self.assertEqual(kinds('{F} ==> [#_h] *{F} X == [.]', "ortho"),
                         ["SPEC", "DARROW", "ORTHO", "STAR", "SPEC", "IDENT", "ALIAS", "ORTHO"])

    def test_top_mode(self):
        toks = tokenize('!print "a\\tb\\"c\\n" $x $field[2] $_ 12 -3', "top")
        self.assertEqual([t.kind for t in toks], ["COMMAND", "STRING", "VAR", "VAR", "VAR", "NUM", "NUM"])
        self.assertEqual(toks[1].value, 'a\tb"c\n')
        self.assertEqual(toks[3].value, ("field", 2))
        self.assertEqual(toks[4].value, ("_", None))
        with self.assertRaises(YascSyntaxError):
            tokenize('!print "abc', "top")

    def test_string_escapes_round_trip(self):
        for s in ('plain', 'tab\there', 'quote"q', 'back\\slash', 'nl\n', '%O{0} > %O{1}\n'):
            self.assertEqual(unescape_string(escape_string(s)[1:-1]), s)
        self.assertEqual(unescape_string("a\\qb"), "a\\qb")


if __name__ == "__main__":
    unittest.main()
