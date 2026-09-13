"""Tests for yasc.compile and yasc.ir: canonical round trip, static checks, macros, working
orthography, !include, placeholders, error snapshots and the acceptance files
(spec §2–§11; design §7.3)."""

import os
import tempfile
import unittest

from yasc.compile import compile_source
from yasc.errors import NotImplementedYet, YascLoadError, YascSyntaxError, YascWarning
from yasc.ir import IRAssign, IRBasicRule, IRCommand, IRGroup, IRInvoke, IRPlaceholder
from yasc.parser import parse
from yasc.pattern import Alt, Capture, Ortho, Spec, walk

PRE = """$P := Phonology [[
  Syll Binary
  Voice Binary
  Nasal Binary
  Place Node(Labial Coronal Dorsal)
  Labial Unary
  Coronal Unary
  Dorsal Unary
  High Binary
    == Hi
  Low Binary
  Back Binary
  Stress Scalar(0,2)
  Tone [H] [L] [M]
  {+Syll} ~~> {+Voice}
]]
$O := Orthography [[
  [p t k] {-Syll -Voice -Nasal}
  [b d g] {-Syll +Voice -Nasal}
  [m n] {-Syll +Nasal}
  [p b m] {!Labial}
  [t d n] {!Coronal}
  [k g] {!Dorsal}
  [i u] {+Syll +High -Low}
  [e o] {+Syll -High -Low}
  [a] {+Syll +Low -High}
  [i e] {-Back}
  [u o a] {+Back}
  {-Voice} ==> [#_0]
]]
V === {+Syll}
C === {-Syll}
"""

#: The line number of the first snippet line after PRE.
N = PRE.count("\n") + 1


def comp(snippet, allow=False, filename="t.yasc"):
    return compile_source(PRE + snippet, filename, allow_unimplemented=allow)


def comp_errors(snippet, allow=False):
    try:
        comp(snippet, allow)
    except YascLoadError as e:
        return e.errors
    raise AssertionError("no error for %r" % snippet)


def only_rule(snippet, allow=False) -> IRBasicRule:
    rules = comp(snippet, allow).rules
    assert len(rules) == 1, rules
    return rules[0]


CORPUS = [
    "V --> 0 / ___# /! #C*___",
    "{+Nasal} --> {(p)Place} / ___ C:{(p)Place}",
    "V --> {+Nasal} / {+Nasal} ___ /*",
    "'[t] --> '[d] / #___",
    "V C --> $2 $1",
    "C --> $1 $1 / V ___ V",
    "{} $1 --> $1 /*/:>",
    "{}'$1 --> $1",
    "V --> 0 / ___# /:i- #(C)*V#",
    "V --> 0 / ___# /:o+ V",
    "V --> {(a)Back} / V:{(a)Back} C* ___ /:F- V /*",
    "0 --> [e] / #___ C",
    "<< [p] | [t] | [k] >> --> << [b] | [d] | [g] >>",
    "C:[p t k] --> {+Voice} / V ___ V",
    "V --> ~{+High} / ___ . /:<",
    "{-(a)High (a)Low} --> {(a)High} /:Raw",
    "{(s)Stress >=1Stress {0 1}Stress _Tone} --> {++(s)Stress}",
    "(C)+ V? ... --> 0 / <:N ___ >:*",
    '[ia] --> [i] /:L+ {!N} /" YI /:@300',
    "V --> 0 /%50:each /???",
    "V --> 0 /:D+ (A B) /:C- N /:C*",
    "V --> 0 /~ /:$ /:: /:1 /:*",
    "V^0 --> V^=h / V^*=h C* ___ /*",
    "^[H]=h --> 0 / V^[H] ___",
    "{+Syll}^[H]=a {+Syll}^[H]=b --> $1 {+Syll}^=a",
    "V V --> V^+[L] V^-=h",
    "[H] --> [M] / [H] ___ /:T Tone",
    "||[[\n  '[t] --> '[d] / #___\n  '[d] --> '[t] / #___\n]]",
    '&&[[ /:< / V ___\n  C --> {+Voice}\n  V --> 0\n]] /:* /" Both',
    "$R := Rules [[ /:1\n  X === << # | C >>\n  $a := $_\n  !date 10\n  V --> 0 / X ___\n  $a := [pa]\n]] /:L- {!Loan}",
    '!print "%O{0} > %O{1}\\n" $a $_',
    "!set DefaultMode = once",
    "$S := Syllabification [[\n  Onset (C)\n  Nucleus V\n  Coda (C)\n  Algorithm MaxOnset\n]]",
    "$N := Paradigm [[\n  Nom.Sg : $_\n  Dat.Pl : <N: $_ > - [ibus] /:L- {!Irregular}\n]]",
    "$Q := Phonology [[\n  A + -\n    (-) - +\n  B Scalar(1,3) Scope(Syllable)\n    == b\n    (<Max>) 3 3 3\n"
    "  T [H] [L] Tier(TBU={+A}, Stray=delete, OCP=merge)\n  N Node(A B)\n  {(a)A} <--(-)--> {(a)B}\n"
    "  {+A} <~~--> {3B}\n  {+A} <--> {-A}\n  Constraint * {+A} {+A}\n]]",
    "$O2 := Orthography [[\n  *{Nasal}\n  [x y] {-Syll ...}\n  {+Voice} ==> [#_v]\n  SyllableMark {2Stress} == [']\n"
    "  PhoneSeparator == [|]\n]]",
]


class RoundTripTests(unittest.TestCase):
    """Spec §11.3: parse(canonical(parse(x))) == parse(x)."""

    def test_corpus(self):
        for snippet in CORPUS:
            with self.subTest(snippet=snippet):
                s = parse(PRE + snippet)
                c = s.canonical()
                s2 = parse(c)
                self.assertEqual(s2, s)
                self.assertEqual(s2.canonical(), c)

    def test_compiled_sources_survive_the_round_trip(self):
        text = PRE + "\n".join(CORPUS[:22])
        a = compile_source(text, allow_unimplemented=True)
        b = compile_source(parse(text).canonical(), allow_unimplemented=True)
        self.assertEqual([r.source for r in a.rules], [r.source for r in b.rules])
        self.assertEqual([r.lhs_pattern.canonical() for r in a.rules if isinstance(r, IRBasicRule)],
                         [r.lhs_pattern.canonical() for r in b.rules if isinstance(r, IRBasicRule)])


class PhonologyCompileTests(unittest.TestCase):
    """Spec §4: feature system from declarations; every error reported."""

    def test_feature_system(self):
        fs = comp("").phonology
        self.assertTrue(fs.sealed)
        self.assertEqual(fs.feature("Hi").name, "High")
        self.assertEqual(fs.feature("Place").type.kind, "node")
        self.assertEqual(fs.feature("Stress").type.values, ("0", "1", "2"))
        self.assertEqual(fs.feature("Tone").type.values, ("H", "L", "M"))
        self.assertEqual(len(fs.implications), 1)

    def test_ops_and_bidirectional(self):
        c = compile_source("$P := Phonology [[\n  High + -\n    (-) - +\n  Low Binary\n  S Scalar(1,3)\n"
                           "    (<Max>) 3 3 3\n  {(a)High} <--(-)--> {(a)Low}\n]]", allow_unimplemented=True)
        fs = c.phonologies["P"]
        self.assertEqual(fs.apply_op("S", "<Max>", "1"), "3")
        self.assertEqual([i.canonical() for i in fs.implications], ["{(a)High} --> {-(a)Low}", "{(a)Low} --> {-(a)High}"])

    def test_all_errors_reported(self):
        try:
            compile_source("$P := Phonology [[\n  A Binary\n  A Unary\n  S Scalar(3,1)\n  T [H] [L]\n"
                           "    (+) [H]\n  N Node(A Zed)\n  {+A} --> {+Nope}\n  {+A} --> {(x)A}\n]]", "p.yasc")
        except YascLoadError as e:
            msgs = [x.message for x in e.errors]
        self.assertEqual(len(msgs), 6, msgs)
        self.assertIn("'A' is already declared", msgs[0])
        self.assertIn("minimum exceeds the maximum", msgs[1])
        self.assertIn("T: operation (+) lists 1 result", msgs[2])
        self.assertIn("unknown feature 'Nope'", msgs[3])
        self.assertIn("not bound by the trigger", msgs[4])
        self.assertIn("unknown feature 'Zed'", msgs[5])


class StaticCheckTests(unittest.TestCase):
    """Spec §6.2, §6.4, §8.2.4; design §13 decision 13."""

    def test_unbound_rhs_variable(self):
        e, = comp_errors("V --> {(a)High}")
        self.assertIn("variable 'a' on the right-hand side is not bound", e.message)
        only_rule("V --> {(a)Back} / V:{(a)Back} ___")
        only_rule("V --> {(a)Back} /:i+ {(a)Back}")
        e, = comp_errors("V --> {(a)Back} /! {(a)Back} ___")
        self.assertIn("not bound", e.message)

    def test_incompatible_variable_sharing(self):
        e, = comp_errors("{(a)High} --> {(a)Stress}")
        self.assertEqual(e.message, "variable 'a' is used on High (Binary) and Stress (Scalar(0,2)), whose values differ")
        only_rule("{(a)High} --> {(a)Low}")
        e, = comp_errors("{(t)Tone} --> 0 / {(t)Stress} ___")
        self.assertIn("variable 't'", e.message)

    def test_backrefs(self):
        e, = comp_errors("V --> $2")
        self.assertIn("$2 on the right-hand side refers to LHS item 2, but the LHS has 1 item", e.message)
        e, = comp_errors("V --> 0 / ___ $3")
        self.assertIn("$3 in a context", e.message)
        e, = comp_errors("$2 V --> 0")
        self.assertIn("must refer to an earlier item", e.message)
        r = only_rule("V C --> $2 $1 / $0 ___")
        self.assertEqual([i.kind for i in r.rhs], ["backref", "backref"])
        self.assertEqual(r.n_items, 2)

    def test_output_specs(self):
        e, = comp_errors("V --> {{1 2}Stress}")
        self.assertIn("cannot be an output", e.message)
        e, = comp_errors("V --> {+Stress}")
        self.assertIn("'+' is not a value of feature 'Stress'", e.message)
        e, = comp_errors("V --> {High}")
        self.assertIn("needs a value", e.message)

    def test_class_correspondence(self):
        r = only_rule("K === <<[p]|[t]|[k]>>\nG === <<[b]|[d]|[g]>>\nK --> G")
        item, = r.rhs
        self.assertEqual(item.kind, "classcorr")
        alts = [x for x in walk(r.lhs_pattern) if isinstance(x, Alt)]
        self.assertEqual(item.alt, alts[0].id)
        self.assertEqual([a[0].kind for a in item.alternatives], ["replace", "replace", "replace"])
        r = only_rule("K === <<[p]|[t]|[k]>>\nG === <<[b]|[d]|[g]>>\nK --> +G")
        self.assertEqual([a[0].kind for a in r.rhs[0].alternatives], ["merge", "merge", "merge"])
        e, = comp_errors("K === <<[p]|[t]|[k]>>\nG === <<[b]|[d]>>\nK --> G")
        self.assertIn("the RHS disjunction has 2 alternatives but the aligned LHS disjunction has 3", e.message)
        e, = comp_errors("V --> <<[b]|[d]>>")
        self.assertIn("needs an aligned LHS disjunction", e.message)
        r = only_rule("C:[p t k] --> << {+Voice} | {+Voice} | [g] >>")
        self.assertEqual(r.rhs[0].kind, "classcorr")


class MacroTests(unittest.TestCase):
    """Spec §7: scope, shadowing, cycles, refinement."""

    def test_cycles(self):
        e, = comp_errors("A === B\nB === A\nA --> 0")
        self.assertEqual(e.message, "macro cycle: A -> B -> A")
        e, = comp_errors("Z === Z\nZ --> 0")
        self.assertEqual(e.message, "macro cycle: Z -> Z")

    def test_unknown_macro(self):
        e, = comp_errors("Vowel === {+Syll}\nVowl --> 0")
        self.assertEqual((e.message, e.hint), ("unknown macro 'Vowl'", "did you mean 'Vowel'?"))

    def test_block_scope(self):
        errs = comp_errors("$R := Rules [[\n  Q === {+High}\n  Q --> 0\n  [[\n    Q --> 0\n  ]]\n]]\nQ --> 0")
        self.assertEqual([(e.message, e.loc.line) for e in errs], [("unknown macro 'Q'", N + 7)])

    def test_globals_are_visible_before_their_definition(self):
        r = only_rule("W --> 0\nW === {+High}")
        self.assertEqual(r.lhs_pattern.canonical(), "{+High}")

    def test_shadowing_warns(self):
        c = comp("$R := Rules [[\n  V === {+High}\n  V --> 0\n]]\nV --> 0")
        self.assertEqual([w.message for w in c.warnings], ["macro V shadows an outer definition"])
        self.assertEqual([r.lhs_pattern.canonical() for r in c.rules], ["{+High}", "{+Syll}"])

    def test_duplicates_and_keywords(self):
        e, = comp_errors("V === {+High}")
        self.assertIn("defined twice", e.message)
        with self.assertRaises(YascLoadError):
            parse("Constraint === {}")

    def test_refinement(self):
        r = only_rule("V:{+High} --> 0")
        self.assertEqual(r.lhs_pattern.canonical(), "{+Syll +High}")
        r = only_rule("K === <<[p]|[t]>>\nK:{+Voice} --> 0")
        alt = [x for x in walk(r.lhs_pattern) if isinstance(x, Alt)][0]
        self.assertEqual(len(alt.items), 2)
        e, = comp_errors("S === V C\nS:{+High} --> 0")
        self.assertIn("single segment spec or a disjunction", e.message)


class OrthographyCompileTests(unittest.TestCase):
    """Spec §2, §5.6, §10.1, §10.3: [...] is parsed with the working orthography."""

    def test_ortho_strings(self):
        r = only_rule("[pa] --> 0 / '[t] ___")
        o = r.lhs_pattern.pattern.pattern
        self.assertIsInstance(o, Ortho)
        self.assertEqual(len(o.specs), 2)
        self.assertTrue(all(s.strict for s in r.contexts[0].left_pattern.specs))

    def test_rhs_merge_and_replace(self):
        # Spec §8.2.4: [x] (and '[x]) replaces; +[x] merges x's declared features.
        r = only_rule("V V V --> [a] '[a] +[a]")
        plain, strict, merge = r.rhs
        self.assertEqual((plain.kind, strict.kind, merge.kind), ("replace", "replace", "merge"))
        self.assertEqual(plain.segment.get("Voice"), "+")   # implications applied
        self.assertEqual(strict.segment, plain.segment)
        self.assertIsNone(merge.segment.get("Voice"))       # declared features only

    def test_rhs_merge_syntax(self):
        # +Macro and +<< >> distribute the merge over the alternatives; the canonical form
        # round-trips.
        r = only_rule("K === <<[p]|[t]>>\nG === <<[b]|[d]>>\nK V --> +G +[a]")
        self.assertEqual([a[0].kind for a in r.rhs[0].alternatives], ["merge", "merge"])
        self.assertEqual(r.rhs[1].kind, "merge")
        self.assertIn("+G +[a]", r.source)
        for bad, msg in (("V --> +{+Voice}", "'+' (merge) applies only to"),
                         ("V --> ~+[a]", "cannot be combined"),
                         ("V --> + [a]", "must be followed directly"),
                         ("V --> +'[a]", "'+' (merge) applies only to")):
            e = comp_errors(bad)[0]
            self.assertIn(msg, e.message, bad)
        # On the left-hand side '+' is still the postfix Plus, with or without spaces.
        for lhs in ("C+V --> 0", "C+ V --> 0"):
            r = only_rule(lhs)
            self.assertTrue(any(type(x).__name__ == "Plus" for x in walk(r.lhs_pattern)), lhs)

    def test_working_orthography_switch(self):
        errs = comp_errors("$O2 := Orthography [[\n  [q] {-Syll}\n]]\n[q] --> 0\n[a] --> 0\n!use $O\n[a] --> 0\n"
                           "!orthography input $O2 output $O\n[a] --> 0")
        self.assertEqual([e.loc.line for e in errs], [N + 4, N + 8])
        self.assertIsInstance(errs[0], YascSyntaxError)

    def test_assignment_form(self):
        c = comp("$w := [pat]")
        a = c.statements[0]
        self.assertIsInstance(a, IRAssign)
        self.assertEqual(len(a.form.segs), 3)

    def test_no_orthography(self):
        try:
            compile_source("$P := Phonology [[\n  Syll Binary\n]]\n[a] --> 0")
        except YascLoadError as e:
            self.assertIn("needs an orthography", e.errors[0].message)

    def test_orthography_errors(self):
        errs = comp_errors("$X := Orthography [[\n  [z] {+Nosal}\n  [z] {-Syll}\n  [z] {+Syll}\n"
                           "  {+Voice} ==> [#]\n]]")
        self.assertEqual(len(errs), 3)
        self.assertIn("unknown feature 'Nosal'", errs[0].message)
        self.assertIn("conflicting values", errs[1].message)


class IncludeTests(unittest.TestCase):
    """Spec §10.5 !include: relative to the including file, at most once."""

    def test_include(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sub"))
            with open(os.path.join(d, "sub", "defs.yasc"), "w") as fh:
                fh.write('M === {+High}\n!include "more.yasc"\n')
            with open(os.path.join(d, "sub", "more.yasc"), "w") as fh:
                fh.write('!include "defs.yasc"\nM2 === {-High}\n')
            with open(os.path.join(d, "rules.yasc"), "w") as fh:
                fh.write("M --> 0\n")
            main = os.path.join(d, "main.yasc")
            text = PRE + '!include "sub/defs.yasc"\n!include "sub/defs.yasc"\nM2 --> 0\n$R := Rules [[\n' \
                         '  !include "rules.yasc"\n]]\n'
            c = compile_source(text, main)
            self.assertEqual(len(c.included), 3)
            self.assertEqual([r.lhs_pattern.canonical() for r in c.rules], ["{-High}", "{+High}"])
            self.assertIsInstance(c.statements[1].members[0], IRBasicRule)
            try:
                compile_source(PRE + '!include "nope.yasc"', main)
            except YascLoadError as e:
                self.assertIn("cannot include 'nope.yasc': file not found", e.errors[0].message)
                self.assertEqual(e.errors[0].loc.line, N)


class ModifierTests(unittest.TestCase):
    """Spec §8.3–§8.8: per-rule and per-group validation, inheritance."""

    def test_duplicates_and_conflicts(self):
        msgs = [e.message for e in comp_errors("V --> 0 /* /*\nV --> 0 /:1 /*\nV --> 0 /:> /:<\nV --> 0 /:F+ V /:F- C")]
        self.assertEqual(msgs, ["modifier /* is given twice on this rule", "modifiers /:1 and /* conflict on this rule",
                                "modifiers /:> and /:< conflict on this rule",
                                "modifiers /:F+ and /:F- conflict on this rule"])

    def test_group_trailing_modifiers(self):
        errs = comp_errors("[[\n  C --> 0\n]] / V ___\n[[\n  C --> 0\n]] /*\n[[ /\" G\n  C --> 0\n]]\n"
                           "[[\n  C --> 0\n]] /:o- ___ V")
        self.assertEqual(len(errs), 4)
        self.assertIn("not allowed after ]]", errs[0].message)
        self.assertIn("put contexts", errs[0].hint)
        self.assertIn("named after its ]]", errs[2].message)
        self.assertIn("cannot use ___", errs[3].message)

    def test_group_ir(self):
        c = comp('&&[[\n  C --> {+Voice}\n]] /:* /:L- {!Loan} /:o- {+Nasal} /" G1 /:~ /::')
        g = c.statements[0]
        self.assertIsInstance(g, IRGroup)
        self.assertEqual((g.kind, g.repeat, g.name, g.weak, g.persistent), ("and", True, "G1", True, True))
        self.assertEqual(g.restrictions.lexical[0].positive, False)
        self.assertFalse(g.out_filters[0].has_locus)
        self.assertIsNotNone(g.out_filters[0].anywhere)

    def test_inheritance(self):
        c = comp("[[ /:< / V ___ /:F- V /:Raw\n  C --> {+Voice}\n  C --> {-Voice} /:> / ___ V\n  [[\n"
                 "    C --> {+Nasal}\n  ]]\n]]")
        r1, r2, r3 = c.rules
        self.assertEqual((r1.direction, len(r1.contexts), r1.raw), (-1, 1, True))
        self.assertIsNotNone(r1.visibility)
        self.assertEqual((r2.direction, len(r2.contexts)), (1, 2))
        self.assertEqual((r3.direction, len(r3.contexts), r3.raw), (-1, 1, True))

    def test_rule_ir(self):
        r = only_rule('V --> 0 / C ___ # /! # ___ /:i+ ___ C /:o- V V /:F+ C /:L+ {!N} /" Apo /*')
        ctx, = r.contexts
        self.assertTrue(ctx.left.is_reversed)
        self.assertFalse(ctx.right.is_reversed)
        self.assertIsNone(r.neg_contexts[0].right)
        self.assertTrue(r.in_filters[0].has_locus)
        self.assertIsNone(r.in_filters[0].left)
        self.assertIsNotNone(r.out_filters[0].anywhere)
        self.assertEqual((r.mode, r.name, r.direction), ("iterative", "Apo", 1))
        self.assertTrue(r.visibility.positive)
        self.assertEqual(r.restrictions.lexical[0].constraints[0].feature, "N")
        self.assertEqual(r.first_specs[0].canonical(), "{+Syll}")
        self.assertIsInstance(r.lhs_pattern, Capture)

    def test_visibility_must_be_single_segment(self):
        e, = comp_errors("V --> 0 /:F+ C C")
        self.assertIn("single-segment pattern", e.message)

    def test_dates(self):
        c = comp("V --> 0\n!date 100\nV --> 0\nV --> 0 /:@150\n!date 200\nV --> 0")
        self.assertEqual([r.date for r in c.rules], [None, 100, 150, 200])
        e, = comp_errors("!date 100\nV --> 0 /:@50")
        self.assertIn("date 50 is earlier than the previous date 100", e.message)

    def test_invocation(self):
        c = comp('$A := Rules [[\n  V --> 0\n]]\n[[\n  C --> 0\n]] /" G\n$B := Rules [[\n  $A\n  $G\n]]')
        inv = c.rule_sections["B"].members
        self.assertEqual([type(x) for x in inv], [IRInvoke, IRInvoke])
        self.assertIs(inv[0].target, c.rule_sections["A"])
        self.assertIs(inv[1].target, c.named["G"])
        e, = comp_errors("$R := Rules [[\n  $Nope\n]]")
        self.assertEqual(e.message, "unknown rule block $Nope")

    def test_commands(self):
        c = comp('!set Seed = 3\n!print "%O{0} %O[$O]{1}" $_ $_\n!use $O\n!only R1\n!assert "pa"')
        self.assertEqual([s.name for s in c.statements], ["set", "print", "use", "only", "assert"])
        self.assertIs(c.statements[1].refs["O"], c.orthographies["O"])
        self.assertEqual(c.statements[2].refs[0], "orthography")
        msgs = [e.message for e in comp_errors('!print "%O{2}" $_\n!print "%X{0}" $_\n!print "%O[$Q]{0}" $_\n'
                                               '!set Sede = 1\n!set MaxIterations = lots\n!use $Nope\n'
                                               '$R := Rules [[\n]]\n!use $R')]
        self.assertEqual(len(msgs), 7)
        self.assertIn("refers to argument 2, but only 1 argument given", msgs[0])
        self.assertIn("unknown !print directive", msgs[1])
        self.assertIn("unknown definition $Q", msgs[2])
        self.assertIn("unknown setting 'Sede'", msgs[3])
        self.assertIn("needs an integer", msgs[4])


class UnimplementedTests(unittest.TestCase):
    """Plan P4: later-phase constructs raise NotImplementedYet or become placeholders."""

    def test_modifier_placeholders(self):
        # Plan P9: /% and /??? compile for real: no pending phase and no warning.
        c = comp("V --> 0 /%50 /???:each")
        r, = c.rules
        self.assertEqual(r.pending, ())
        self.assertEqual((r.stochastic.percent, r.optional.each), (50.0, True))
        self.assertFalse([w for w in c.warnings if w.phase == "P9"])

    def test_tier_rules_compile(self):
        # Plan P8: autosegmental rules and /:T tier-only rules compile for real (spec §6.5).
        pre = PRE.replace("  Tone [H] [L] [M]\n", "  Tone [H] [L] [M] Tier(TBU={+Syll})\n")
        c = compile_source(pre + "!date 5\nV^0 --> V^[H] /\" T1\n[H] --> [M] / [H] ___ /:T Tone", "t.yasc")
        self.assertEqual([type(r) for r in c.rules], [IRBasicRule, IRBasicRule])
        self.assertEqual((c.rules[0].name, c.rules[0].date, c.rules[0].id), ("T1", 5, 1))
        self.assertEqual([it.kind for it in c.rules[0].rhs], ["tier"])
        self.assertEqual(c.rules[1].tier, "Tone")
        self.assertFalse(c.warnings)

    def test_cyclic_rule_is_wrapped(self):
        g = comp("V --> 0 /:C* /:C+ N", allow=True).statements[0]
        self.assertIsInstance(g, IRGroup)
        self.assertTrue(g.cyclic)
        self.assertEqual(g.members[0].restrictions.category[0].names, ("N",))

    def test_sections_and_commands(self):
        snippet = ("$S := Syllabification [[\n  Onset (C)\n  Nucleus V\n  Coda C\n]]\n"
                   "$N := Paradigm [[\n  Nom : $_\n  Gen : $_ - [in]\n]]\n!syllabify $S\n!paradigm $N\n"
                   "!dialects (A B)\n!associate Tone dir=>\n!ocp Tone")
        try:
            comp(snippet)
            errs = []
        except YascLoadError as e:
            errs = e.errors
        # P7 and P9: Syllabification, !syllabify, Paradigm, !paradigm and !dialects compile for real.
        self.assertNotIn("P9", [e.phase for e in errs])
        c = comp(snippet, allow=True)
        sd = c.syllabifications["S"]
        self.assertEqual(sorted(sd.templates), ["Coda", "Nucleus", "Onset"])
        self.assertEqual(c.paradigms["N"].cells[1].items[1][0], "boundary")
        cmds = [s for s in c.statements if isinstance(s, IRCommand)]
        self.assertEqual([(x.name, x.pending) for x in cmds][:3],
                         [("syllabify", None), ("paradigm", None), ("dialects", None)])
        self.assertIs(cmds[0].refs, sd)

    def test_syllable_template_restrictions(self):
        errs = comp_errors("Syllabification [[\n  Onset C*\n  Coda # C\n]]", allow=True)
        self.assertEqual(len(errs), 2)
        self.assertIn("not allowed in a syllable template", errs[0].message)

    def test_constraints(self):
        c = compile_source("$P := Phonology [[\n  S Binary\n  Constraint * {+S} {+S}\n]]", allow_unimplemented=True)
        self.assertEqual(len(c.constraints), 1)
        self.assertEqual(c.constraints[0].source, "Constraint * {+S} {+S}")


class SnapshotTests(unittest.TestCase):
    """Spec §12: full caret text of representative errors; several errors per load."""

    def check(self, snippet, expected, allow=False):
        try:
            comp(snippet, allow)
        except YascLoadError as e:
            self.assertEqual(e.format(), expected % {"n": N, "n1": N + 1, "n2": N + 2})
            return
        self.fail("no error")

    def test_unknown_feature(self):
        self.check("V --> 0 / ___ {+Nasl}",
                   "t.yasc:%(n)d:16: error: unknown feature 'Nasl'\n"
                   "  V --> 0 / ___ {+Nasl}\n"
                   "                 ^^^^^\n"
                   "hint: did you mean 'Nasal'?")

    def test_unterminated_spec(self):
        self.check("V --> {+Voice / ___ #",
                   "t.yasc:%(n)d:7: error: unterminated segment spec: missing '}'\n"
                   "  V --> {+Voice / ___ #\n"
                   "        ^^^^^^^^^^^^^^^")

    def test_elided_value_list(self):
        self.check("$Q := Phonology [[\n  Tone [H] ... [L]\n]]",
                   "t.yasc:%(n1)d:12: error: '...' is not allowed in value or operation lists\n"
                   "    Tone [H] ... [L]\n"
                   "             ^^^\n"
                   "hint: list every value and result in full (spec §4.1, A9)")

    def test_macro_cycle(self):
        self.check("A === B\nB === A\nA --> 0",
                   "t.yasc:%(n)d:1: error: macro cycle: A -> B -> A\n"
                   "  A === B\n"
                   "  ^\n"
                   "hint: a macro may not use itself, directly or indirectly (spec §7)")

    def test_date_decrease(self):
        self.check("!date 200\n!date 100",
                   "t.yasc:%(n1)d:7: error: date 100 is earlier than the previous date 200 (t.yasc:%(n)d:7)\n"
                   "  !date 100\n"
                   "        ^^^\n"
                   "hint: dates must be non-decreasing in file order (spec §8.10)")

    def test_unparsable_orthographic_string(self):
        self.check("[pq] --> 0",
                   "t.yasc:%(n)d:3: error: cannot parse 'q' in 'pq': no grapheme, diacritic or separator matches\n"
                   "  [pq] --> 0\n"
                   "    ^\n"
                   "hint: declare the grapheme, or use !set OnUnparsable = skip|keep")

    def test_several_errors_in_one_load(self):
        # Plan P9: /% compiles for real, so the fourth error is an out-of-range probability.
        errs = comp_errors("V --> {(a)High}\nV --> {+Stress}\nNope --> 0\nV --> 0 /%500")
        self.assertEqual([e.loc.line for e in errs], [N, N + 1, N + 2, N + 3])
        self.assertEqual([type(e).__name__ for e in errs],
                         ["YascDefinitionError", "YascDefinitionError", "YascDefinitionError", "YascDefinitionError"])


class AcceptanceTests(unittest.TestCase):
    """Plan P4 acceptance: Appendix B and the fixed-up notes example."""

    def load(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_revised_example(self):
        text = self.load("examples/revised-example.yasc")
        parse(text)
        # Plan P8: the script compiles strictly, with no warnings.
        c = compile_source(text, "examples/revised-example.yasc")
        self.assertEqual(c.warnings, [])
        sec = c.rule_sections["R"]
        self.assertEqual([type(m).__name__ for m in sec.members],
                         ["IRAssign", "IRBasicRule", "IRBasicRule", "IRBasicRule", "IRBasicRule", "IRGroup",
                          "IRCommand"])
        self.assertEqual([r.date for r in c.rules], [100, 100, 400, 400, 400, 400])
        self.assertIs(c.orthography, c.orthographies["O"])
        self.assertIs(c.phonology, c.phonologies["P"])

    def test_fixed_notes_example(self):
        path = "tests/data/yasc-example-fixed.yasc"
        text = self.load(path)
        c = compile_source(text, path, allow_unimplemented=True)
        self.assertEqual(len(c.rules), 18)
        # P7: the notes' empty Syllabification section now compiles, with a warning.
        self.assertTrue(all(w.phase == "P9" or "no Nucleus template" in w.message for w in c.warnings))
        self.assertIn("Rules", c.rule_sections)


if __name__ == "__main__":
    unittest.main()
