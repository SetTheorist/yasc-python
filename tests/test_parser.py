"""Tests for yasc.parser and yasc.syntax: one parse test per row of the spec's syntax tables,
error recovery and messages (spec §3–§10; design §7.2)."""

import unittest

from yasc.errors import YascLoadError
from yasc.marks import Mark
from yasc.parser import parse
from yasc.pattern import (
    CLOSE, OPEN, Alt, Anything, AutoFloat, BackRef, BracketAssert, Linked, Locus, Macro, Nothing, Opt,
    Plus, Seq, Star,
)
from yasc.syntax import (
    Assign, BasicRule, Combine, Command, ConstraintDecl, DiacriticLine, Group, IgnoreLine,
    ImplicationDecl, Invoke, LinkOp, MacroDef, OrthographySection, ParadigmSection, PhonesLine, PhonologySection,
    RawOrtho, RawSpec, RulesSection, SettingLine, SyllabificationSection, SyllableMarkLine,
)


def stmt(text):
    s = parse(text)
    return s.stmts[0]


def rule(text) -> BasicRule:
    r = stmt(text)
    assert isinstance(r, BasicRule), r
    return r


def lhs(text):
    return rule(text + " --> 0").lhs


def phon(*lines):
    return stmt("Phonology [[\n" + "\n".join(lines) + "\n]]").items


def ortho(*lines):
    return stmt("Orthography [[\n" + "\n".join(lines) + "\n]]").items


def errors(text):
    try:
        parse(text, "t.yasc")
    except YascLoadError as e:
        return e.errors
    raise AssertionError("no error for %r" % text)


class PhonologyTests(unittest.TestCase):
    """Spec §4.1 declaration forms, §4.3, §4.5 and §4.6."""

    def test_feature_forms(self):
        items = phon("High + -", "High2 Binary", "Labial Unary", "Lab2 !", "Stress Scalar(1,3)", "Len Scalar",
                     "Tone [H] [L] [M] [HL] [LH]", "Place Node(Labial Coronal Dorsal)",
                     "T2 [H] [L] Tier(TBU={+Syll}, Stray=float, OCP=merge)")
        types = [(d.name, d.type.kind) for d in items]
        self.assertEqual(types, [("High", "values"), ("High2", "binary"), ("Labial", "unary"), ("Lab2", "unary"),
                                 ("Stress", "scalar"), ("Len", "scalar"), ("Tone", "values"), ("Place", "node"),
                                 ("T2", "values")])
        self.assertEqual(items[0].type.values, ("+", "-"))
        self.assertEqual(items[4].type.bounds, (1, 3))
        self.assertIsNone(items[5].type.bounds)
        self.assertEqual(items[6].type.values, ("[H]", "[L]", "[M]", "[HL]", "[LH]"))
        self.assertEqual(items[7].type.children, ("Labial", "Coronal", "Dorsal"))
        tier = items[8].tier
        self.assertEqual((tier.stray, tier.ocp, tier.tbu.canonical()), ("float", "merge", "{+Syll}"))

    def test_scope_aliases_and_ops(self):
        d, = phon("Stress Scalar(0,2) Scope(Syllable)", "  == stress Str", "  (<Max>) 2 2 2", "  (-!) _ 0 1")
        self.assertEqual(d.scope, "Syllable")
        self.assertEqual(d.aliases[0].names, ("stress", "Str"))
        self.assertEqual([(o.op, o.results) for o in d.ops], [("<Max>", ("2", "2", "2")), ("-!", ("_", "0", "1"))])
        d, = phon("Labial Unary == labial lab")
        self.assertEqual(d.aliases[0].names, ("labial", "lab"))

    def test_implication_arrows(self):
        rows = {"-->": ("--", None, None), "~~>": ("~~", None, None), "<-->": ("--", "--", None),
                "<--(-)-->": ("--", "--", "-"), "<~~-->": ("--", "~~", None), "<--~~>": ("~~", "--", None),
                "<~~~~>": ("~~", "~~", None), "<~~(<M->)-->": ("--", "~~", "<M->")}
        for arrow, (fwd, bwd, op) in rows.items():
            with self.subTest(arrow=arrow):
                imp, = phon("{(a)High} %s {(a)Low}" % arrow)
                self.assertIsInstance(imp, ImplicationDecl)
                self.assertEqual((imp.forward, imp.backward, imp.op), (fwd, bwd, op))
                self.assertEqual(imp.left.canonical(), "{(a)High}")

    def test_constraint(self):
        c, = phon("Constraint * {-Syll +Voice}{-Syll -Voice}")
        self.assertIsInstance(c, ConstraintDecl)
        self.assertEqual(c.canonical(), "Constraint * {-Syll +Voice} {-Syll -Voice}")

    def test_phonetics_synonym(self):
        self.assertIsInstance(stmt("$P := Phonetics [[\nA Binary\n]]"), PhonologySection)

    def test_errors(self):
        errs = errors("Phonology [[\nTone [H] ... [L]\nPlace Node{A B}\n(-) + -\nHigh Binary + -\n]]")
        msgs = [e.message for e in errs]
        self.assertIn("'...' is not allowed in value or operation lists", msgs[0])
        self.assertIn("expected '(' after Node", msgs[1])
        self.assertIn("operation line with no feature", msgs[2])
        self.assertIn("takes no value list", msgs[3])


class OrthographyTests(unittest.TestCase):
    """Every row of the spec §5.6 table."""

    def test_rows(self):
        items = ortho("[p t k] {-Syll -Voice ...}", "{-Voice} ==> [#_0]", "{+Nasal} ==> [~#]", "{+Asp} ==> [(#)]",
                      "*{Place ATR}", "PhoneSeparator == [|]", "SyllableSeparator == [.]", "MorphemeSeparator == [+]",
                      "CliticSeparator == [=]", "WordSeparator == [#]", "PhraseSeparator == [##]",
                      "BracketOpen == [<]", "BracketClose == [>]", "BracketLabelEnd == [:]",
                      'SyllableMark {2Stress} == ["]', "FloatingPrefix == [^]", "Escape == [\\]")
        self.assertIsInstance(items[0], PhonesLine)
        self.assertEqual(items[0].graphemes, ("p", "t", "k"))
        self.assertEqual(items[0].spec.canonical(), "{-Syll -Voice}")
        self.assertEqual([d.template for d in items[1:4]], ["#_0", "~#", "(#)"])
        self.assertIsInstance(items[4], IgnoreLine)
        settings = [(s.name, s.text) for s in items if isinstance(s, SettingLine)]
        self.assertEqual(settings, [("PhoneSeparator", "|"), ("SyllableSeparator", "."), ("MorphemeSeparator", "+"),
                                    ("CliticSeparator", "="), ("WordSeparator", "#"), ("PhraseSeparator", "##"),
                                    ("BracketOpen", "<"), ("BracketClose", ">"), ("BracketLabelEnd", ":"),
                                    ("FloatingPrefix", "^"), ("Escape", "\\")])
        mark = [s for s in items if isinstance(s, SyllableMarkLine)][0]
        self.assertEqual((mark.spec.canonical(), mark.text), ("{2Stress}", '"'))

    def test_no_escapes_inside_brackets(self):
        d, = ortho("{[H]Tone} ==> [#\\']")
        self.assertIsInstance(d, DiacriticLine)
        self.assertEqual(d.template, "#\\'")
        p, = ortho('[u" J\\ #] {+Syll}')
        self.assertEqual(p.graphemes, ('u"', "J\\", "#"))

    def test_errors(self):
        errs = errors("Orthography [[\nPhoneSeperator == [.]\n{F} ==> [ab]\n[p]\n]]")
        self.assertEqual(len(errs), 3)
        self.assertEqual(errs[0].hint, "did you mean 'PhoneSeparator'?")
        self.assertIn("exactly one '#'", errs[1].message)


class SyllabificationAndParadigmTests(unittest.TestCase):
    """Spec §5.7 and §9."""

    def test_syllabification(self):
        sec = stmt("$S := Syllabification [[\n Onset << C | {-Son}{+Son -Syll} | [s]{-Son -Cont} >>\n Nucleus V\n"
                   " Coda (C)\n OnsetRequired no\n Algorithm Canon\n Canons CV > CVC > V > VC\n"
                   " NucleusPreference last\n Persistent yes\n Domain phrase\n AllowUnsyllabified yes\n]]")
        self.assertIsInstance(sec, SyllabificationSection)
        d = {s.key: s.value for s in sec.settings}
        self.assertIsInstance(d["Onset"], Alt)
        self.assertIsInstance(d["Coda"], Opt)
        self.assertEqual(d["Canons"], ("CV", "CVC", "V", "VC"))
        self.assertEqual((d["Algorithm"], d["NucleusPreference"], d["Domain"]), ("Canon", "last", "phrase"))
        empty = stmt("Syllabification [[\n Onset\n]]").settings[0]
        self.assertIsInstance(empty.value, Nothing)

    def test_syllabification_errors(self):
        errs = errors("Syllabification [[\n Onst C\n Algorithm Best\n Nucleus V\n Nucleus V\n]]")
        self.assertEqual([e.message for e in errs][0], "unknown syllabification setting 'Onst'")
        self.assertIn("Algorithm must be one of", errs[1].message)
        self.assertIn("given twice", errs[2].message)

    def test_paradigm(self):
        sec = stmt("$Noun := Paradigm [[\n Nom.Sg : $_\n Gen.Sg : $_ - [is]\n Nom.Pl : $_ - [es] /:L- {!Irregular}\n"
                   " Dat.Pl : <N: $_ > - [ibus] /:D+ (A B)\n]]")
        self.assertIsInstance(sec, ParadigmSection)
        self.assertEqual([c.label for c in sec.cells], ["Nom.Sg", "Gen.Sg", "Nom.Pl", "Dat.Pl"])
        self.assertEqual([i.kind for i in sec.cells[3].items], ["open", "stem", "close", "boundary", "ortho"])
        self.assertEqual(sec.cells[2].mods[0].key, "/:L-")
        self.assertEqual(sec.cells[3].canonical(), "Dat.Pl : <N: $_ > - [ibus] /:D+ (A B)")
        errs = errors("Paradigm [[\n A : $_ > \n B : $_ /:1\n]]")
        self.assertEqual(len(errs), 2)


class PatternTests(unittest.TestCase):
    """Every row of the spec §6.1 table, §5.2 boundary symbols and §6.5 notation."""

    def test_leaves(self):
        self.assertIsInstance(lhs("{+Syll}"), RawSpec)
        o = lhs("[abc]")
        self.assertEqual((type(o), o.text, o.strict), (RawOrtho, "abc", False))
        self.assertTrue(lhs("'{+Syll}").strict)
        self.assertTrue(lhs("'[t]").strict)
        self.assertEqual(lhs("RoundedFrontVowel"), Macro("RoundedFrontVowel"))
        self.assertIsInstance(lhs("0"), Nothing)

    def test_combined(self):
        self.assertEqual(lhs("V:{-Voice}").canonical(), "V:{-Voice}")
        self.assertIsInstance(lhs("V:{-Voice}"), Macro)
        c = lhs("C:[p t k]")
        self.assertEqual((c.name, c.refine.text), ("C", "p t k"))
        self.assertIsInstance(lhs("{+Syll}:[a]"), Combine)

    def test_boundaries(self):
        seq = lhs(". - = # ## {}")
        self.assertEqual([b.mark for b in seq.items[:5]], [Mark.SYLLABLE, Mark.MORPHEME, Mark.CLITIC, Mark.WORD,
                                                          Mark.PHRASE])

    def test_brackets(self):
        seq = lhs("<:N {} >:*")
        self.assertEqual(seq.items[0], BracketAssert(OPEN, "N"))
        self.assertEqual(seq.items[2], BracketAssert(CLOSE, "*"))

    def test_sequence_alt_optional_repetition(self):
        self.assertEqual([type(x) for x in lhs("V C V").items], [Macro, Macro, Macro])
        a = lhs("<< # | C | >>")
        self.assertEqual((type(a), len(a.items), a.id), (Alt, 3, None))
        self.assertIsInstance(a.items[2], Nothing)
        self.assertEqual(lhs("(C)"), Opt(Macro("C")))
        self.assertEqual(lhs("(C)?"), Opt(Macro("C")))
        self.assertEqual(lhs("C?"), Opt(Macro("C")))
        self.assertEqual(lhs("(C V)*"), Star(Seq((Macro("C"), Macro("V")))))
        self.assertEqual(lhs("C*"), Star(Macro("C")))
        self.assertEqual(lhs("(C)+"), Plus(Macro("C")))
        self.assertEqual(lhs("C+"), Plus(Macro("C")))
        self.assertIsInstance(lhs("..."), Anything)

    def test_postfix_binds_tighter_than_sequence(self):
        self.assertEqual(lhs("V C*"), Seq((Macro("V"), Star(Macro("C")))))
        self.assertEqual(lhs("#(C)*V#").canonical(), "# C* V #")

    def test_locus_and_backref(self):
        ctx = rule("V --> 0 / ___ $1").mods[0].arg
        self.assertEqual(ctx.items, (Locus(), BackRef(1)))
        self.assertEqual(rule("{} $1 --> $1").lhs.items[1], BackRef(1))
        self.assertEqual(rule("{}'$1 --> $1").lhs.items[1], BackRef(1))

    def test_tier_patterns(self):
        items = rule("V^[H] V^(a) V^* V^0 ^[L] V^Tone.[H]'=h --> 0").lhs.items
        self.assertEqual([(type(x).__name__, x.x) for x in items],
                         [("Linked", "[H]"), ("Linked", "(a)"), ("Linked", "*"), ("Linked", "0"), ("AutoFloat", "[L]"),
                          ("Linked", "[H]")])
        self.assertEqual((items[5].tier, items[5].exact, items[5].name), ("Tone", True, "h"))
        self.assertEqual(items[5].canonical(), "V^Tone.[H]'=h")

    def test_tier_rhs(self):
        items = [i.pattern for i in rule("V V V V V V V --> V^[L] V^+[L] V^=h V^+=h V^0 V^-=h ^[H]").rhs.items]
        self.assertEqual([type(x) for x in items], [Linked, LinkOp, Linked, LinkOp, Linked, LinkOp, AutoFloat])
        self.assertEqual([x.canonical() for x in items], ["V^[L]", "V^+[L]", "V^=h", "V^+=h", "V^0", "V^-=h", "^[H]"])
        self.assertEqual(rule("^[H]=h --> 0 / V^[H] ___").lhs, AutoFloat(None, "[H]", "h"))

    def test_pattern_errors(self):
        msgs = [e.message for e in errors("V^+[L] --> 0\nV --> 0 / C\nV --> 0 / ___ ___\n(C --> 0\n<< C --> 0\n"
                                          "V --> 0 / (___)\n --> 0\nV --> $x\nV -->\nV --> # V\nV --> 0 V")]
        self.assertEqual(len(msgs), 11)
        self.assertIn("only allowed on the right-hand side", msgs[0])
        self.assertIn("exactly once (found 0)", msgs[1])
        self.assertIn("exactly once (found 2)", msgs[2])
        self.assertIn("top-level item", msgs[5])
        self.assertIn("empty left-hand side", msgs[6])
        self.assertIn("empty right-hand side", msgs[8])
        self.assertIn("not allowed on the right-hand side", msgs[9])
        self.assertIn("must be the whole right-hand side", msgs[10])


class RuleTests(unittest.TestCase):
    """Spec §8.1–§8.8 rows."""

    def test_basic_rule_and_rhs_items(self):
        r = rule("V C --> {+High} ~{+Voice} [x] '[y] '{+Syll} $2 $1 << [b] | [d] >> V")
        kinds = [(type(i.pattern).__name__, i.weak) for i in r.rhs.items]
        self.assertEqual(kinds, [("RawSpec", False), ("RawSpec", True), ("RawOrtho", False), ("RawOrtho", False),
                                 ("RawSpec", False), ("BackRef", False), ("BackRef", False), ("Alt", False),
                                 ("Macro", False)])
        self.assertTrue(r.rhs.items[3].pattern.strict)
        self.assertEqual(rule("V --> 0").rhs.items, ())
        self.assertEqual(rule("0 --> [e] / C ___ #").lhs, Nothing())

    def test_contexts(self):
        r = rule("V --> 0 / ___# /! #C*___ / C ___")
        self.assertEqual([m.key for m in r.mods], ["/", "/", "/!"])
        self.assertEqual(r.mods[2].arg.canonical(), "# C* ___")

    def test_modes_and_direction(self):
        for mod in ("/:1", "/*", "/:*", "/:>", "/:<"):
            with self.subTest(mod=mod):
                self.assertEqual(rule("V --> 0 " + mod).mods[0].key, mod)

    def test_filters_visibility_restrictions(self):
        r = rule("V --> 0 / ___# /:i- #C*V# /:i+ V...V /:o- #(C)*# /:o+ V /:F- V /:F+ C /:L+ {!N} /:L- {+Romance} "
                 "/:D+ G /:D- (G1 G2) /:C+ N /:C- (N V) /:C*")
        keys = [m.key for m in r.mods]
        self.assertEqual(keys, ["/", "/:i+", "/:i-", "/:o+", "/:o-", "/:F+", "/:F-", "/:L+", "/:L-", "/:D+", "/:D-",
                                "/:C+", "/:C-", "/:C*"])
        by = {m.key: m.arg for m in r.mods}
        self.assertEqual(by["/:D-"], ("G1", "G2"))
        self.assertEqual(by["/:C+"], ("N",))
        self.assertEqual(by["/:L+"].canonical(), "{!N}")
        self.assertEqual(rule("V --> 0 /:i+ ___ V").mods[0].arg.items[0], Locus())

    def test_other_modifiers(self):
        r = rule('V --> 0 /~ /:Raw /:$ /:T Tone /:@-500 /%50 /???:each /" Lenition /:: /*')
        self.assertEqual([m.canonical() for m in r.mods],
                         ["/*", "/:~", "/:Raw", "/:$", "/:T Tone", "/:@-500", "/%50", "/???:each", '/" Lenition', "/::"])
        self.assertEqual(rule("V --> 0 /%12.5:each /???").mods[0].arg, (12.5, True))

    def test_modifier_order_does_not_matter(self):
        self.assertEqual(rule("V --> 0 /*/:>"), rule("V --> 0 /:> /*"))
        self.assertEqual(rule("V --> 0 /*/:>").canonical(), "V --> 0 /* /:>")
        self.assertNotEqual(rule("V --> 0 /*"), rule("V --> 0 /:*"))

    def test_groups(self):
        s = parse("[[ /:< / V ___\n A --> B\n]] /:*\n&&[[\n A --> B\n C --> D\n]]\n||[[\n A --> B\n]] /\" Shift\n")
        g1, g2, g3 = s.stmts
        self.assertEqual((g1.kind, g2.kind, g3.kind), ("seq", "and", "or"))
        self.assertEqual([m.key for m in g1.lead], ["/", "/:<"])
        self.assertEqual([m.key for m in g1.trail], ["/:*"])
        self.assertEqual(len(g2.body), 2)
        self.assertEqual(g3.trail[0].arg, "Shift")

    def test_nested_groups_and_invocation(self):
        s = parse("$R := Rules [[ /:1\n  [[\n    [[\n      A --> B\n    ]] /:C+V\n  ]] /:C* /:C+(V N)\n  $Other\n"
                  "  X === << # | C >>\n  $mid ::= $_\n  !date 5\n]] /:L+ {!N}")
        sec = s.stmts[0]
        self.assertIsInstance(sec, RulesSection)
        self.assertEqual(sec.name, "R")
        outer = sec.body[0]
        self.assertIsInstance(outer.body[0], Group)
        self.assertEqual([m.key for m in outer.trail], ["/:C+", "/:C*"])
        self.assertEqual(outer.trail[0].arg, ("V", "N"))
        self.assertEqual(sec.body[1], Invoke("Other"))
        self.assertIsInstance(sec.body[2], MacroDef)
        self.assertEqual(sec.body[3], Assign("mid", sec.body[3].expr))
        self.assertEqual(sec.lead[0].key, "/:1")
        self.assertEqual(sec.trail[0].key, "/:L+")


class StatementTests(unittest.TestCase):
    """Spec §3.5 operators, §7 macros, §10.1 variables and every §10.5 command."""

    def test_macros(self):
        m = stmt("K === <<[p]|[t]|[k]>>")
        self.assertIsInstance(m, MacroDef)
        self.assertEqual(m.body.canonical(), "<< [p] | [t] | [k] >>")
        self.assertEqual(stmt("E === 0").body, Nothing())

    def test_assignments(self):
        exprs = [stmt(t).expr for t in ("$a := $_", "$a ::= $b", "$a := [pater]", "$a := $field[2]", "$a := $NF",
                                        "$_ := $in", "$a := $raw", "$a := $NR")]
        self.assertEqual([(e.kind, e.value) for e in exprs],
                         [("var", "_"), ("var", "b"), ("ortho", "pater"), ("field", 2), ("var", "NF"), ("var", "in"),
                          ("var", "raw"), ("var", "NR")])
        self.assertEqual(stmt("$a ::= $b").canonical(), "$a := $b")

    def test_commands(self):
        rows = [
            ('!include "lib/std.yasc"', "include"),
            ('!print "%O{0} > %O[$X]{1} %S{0} %I{0} %F{0} %L{0}\\t%%\\n" $input $_', "print"),
            ("!date -500", "date"),
            ("!set MaxIterations = 50", "set"),
            ("!set InputFormat = regex:^(?P<form>\\S+)$", "set"),
            ("!use $O", "use"),
            ("!orthography input $A output $B", "orthography"),
            ("!orthography output $B", "orthography"),
            ("!syllabify", "syllabify"),
            ("!syllabify $S", "syllabify"),
            ("!dialects (A B C)", "dialects"),
            ("!dialects A B", "dialects"),
            ("!paradigm $Noun", "paradigm"),
            ("!associate Tone dir=< mode=one-to-one spread=none", "associate"),
            ("!ocp Tone merge", "ocp"),
            ("!ocp Tone", "ocp"),
            ("!only R1 R2", "only"),
            ("!skip Lenition", "skip"),
            ('!assert "pa.ter"', "assert"),
        ]
        for text, name in rows:
            with self.subTest(text=text):
                c = stmt(text)
                self.assertIsInstance(c, Command)
                self.assertEqual(c.name, name)
                self.assertEqual(stmt(c.canonical()), c)
        self.assertEqual(stmt("!set InputFormat = regex:^(?P<form>\\S+)$").args[1].value, "regex:^(?P<form>\\S+)$")
        self.assertEqual(stmt("!date -500").args[0].value, -500)
        self.assertEqual(stmt("!dialects A B").args[0].value, ("A", "B"))

    def test_command_errors(self):
        errs = errors('!prnt "x"\n!date x\n!use O\n!orthography sideways $A\n!associate Tone dir=up\n!only\n'
                      '!set Seed\n!ocp Tone fuse')
        self.assertEqual(len(errs), 8)
        self.assertEqual(errs[0].hint, "did you mean 'print'?")

    def test_sections(self):
        self.assertIsInstance(stmt("$O := Orthography [[\n[a] {+Syll}\n]]"), OrthographySection)
        errs = errors("$X := Rules\nPhonology [[ Syll Binary\n]]\nRules [[\n  $P := Phonology [[\n  ]]\n]]")
        self.assertIn("expected '[[' after Rules", errs[0].message)
        self.assertIn("unexpected text after '[['", errs[1].message)
        self.assertIn("sections cannot be defined inside Rules", errs[2].message)


class RecoveryTests(unittest.TestCase):
    """Design §7.2 line-level error recovery and block depth."""

    def test_errors_are_collected_per_line(self):
        errs = errors("V --> {+Voice\nC --> ]\nV --> 0 / ___ #\nwhat is this\nV --> 0 /:Q")
        self.assertEqual([e.loc.line for e in errs], [1, 2, 4, 5])
        self.assertTrue(all(e.source_line is not None for e in errs))

    def test_bad_block_header_skips_the_block(self):
        errs = errors("[[ /:Q\n  A --> B\n  [[\n    bad line\n  ]]\n]]\nC --> D\nalso bad")
        self.assertEqual([e.loc.line for e in errs], [1, 8])

    def test_unbalanced_blocks(self):
        errs = errors("]]\n$R := Rules [[\n  A --> B\n")
        self.assertIn("without a matching", errs[0].message)
        self.assertIn("never closed", errs[1].message)
        self.assertEqual(errs[1].loc.line, 2)

    def test_hints(self):
        e, = errors("Phonology [[\n  Tone [H] [L]   # many-valued\n]]")
        self.assertIn("%%", e.hint)
        e, = errors("A --> B / C __ D /*/>")
        self.assertIn("/:>", e.hint)

    def test_error_limit_note(self):
        try:
            parse("\n".join("x%d" % i for i in range(25)))
        except YascLoadError as e:
            self.assertEqual(len(e.errors), 25)
            self.assertIn("note: 5 more errors not shown", e.format())


class CanonicalParseTests(unittest.TestCase):
    """Spec §11.3: canonical() of each node parses back to an equal node."""

    def test_round_trip_of_the_examples(self):
        for path in ("examples/revised-example.yasc", "tests/data/yasc-example-fixed.yasc"):
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as fh:
                    s = parse(fh.read(), path)
                self.assertEqual(parse(s.canonical()), s)


if __name__ == "__main__":
    unittest.main()
