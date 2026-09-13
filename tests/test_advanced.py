"""Tests for plan P9, advanced rule features (spec §4.6, §8.6–§8.8, §8.11, §10.4;
decisions in docs/decisions/P9.md): categories and cyclic application, optional and
stochastic rules, variants and labels, dialects, constraints and group modifiers."""

import unittest

import yasc
from yasc.compile import compile_source
from yasc.errors import YascRuntimeError
from yasc.lexicon import read_records
from yasc.rules import ApplyContext, BasicRule, Group, run_section
from yasc.runtime import Runtime

CAD = """$P := Phonology [[
  Cons Binary
  Hi Binary
  Bk Binary
]]
$O := Orthography [[
  [C] {+Cons -Hi -Bk}
  [D] {+Cons +Hi -Bk}
  [A] {-Cons -Hi -Bk}
  [B] {-Cons +Hi -Bk}
  [E] {-Cons -Hi +Bk}
]]
"""

HDR = """$P := Phonology [[
  Syll Binary
  Voice Binary
  Cont Binary
  Lab Binary
  Dor Binary
  High Binary
  Back Binary
]]
$O := Orthography [[
  [p t k b d g s z] {-Syll}
  [p t k s] {-Voice}
  [b d g z] {+Voice}
  [p t k b d g] {-Cont}
  [s z] {+Cont}
  [p b] {+Lab -Dor}
  [t d s z] {-Lab -Dor}
  [k g] {-Lab +Dor}
  [a e i o u] {+Syll}
  [i u] {+High}
  [a e o] {-High}
  [e i] {-Back -Lab}
  [a] {+Back -Lab}
  [o u] {+Back +Lab}
]]
V === {+Syll}
C === {-Syll}
"""


def _body(rules):
    return "$R := Rules [[\n" + "\n".join("  " + x for x in rules.strip("\n").split("\n")) + "\n]]\n"


def cad(rules, text):
    """Run section R of ``rules`` (CAD alphabet) through the engine; the rendered output."""
    c = compile_source(CAD + _body(rules))
    return c.orthography.render(run_section(c, "R", c.orthography.parse(text), ApplyContext()).form)


def script(rules, prelude="", **kw):
    """A :class:`yasc.runtime.Script` of HDR + prelude + section R."""
    return yasc.loads(HDR + prelude + _body(rules), **kw)


def outs(res):
    """``[(text, label, dialect)]`` of a result's variants."""
    return [(v.text, v.label, v.dialect) for v in res.outputs]


CYCLIC = """[[
  [[
    [A] --> [B] / [C] ___ [D]
  ]] /:C+ V
  [[
    [A] --> [E] / [C] ___ [D]
  ]] /:C+ N
]] /:C*"""


class CategoryTests(unittest.TestCase):
    """Spec §8.6: /:C± domains and /:C* cyclic application."""

    def test_spec_example_cedcbd(self):
        self.assertEqual(cad(CYCLIC, "<V:<N:CAD>CAD>"), "CEDCBD")

    def test_cyclic_single_rule_erases_brackets(self):
        self.assertEqual(cad("[A] --> [E] / [C] ___ [D] /:C* /:C+ N", "<V:<N:CAD>CAD>"), "CEDCAD")

    def test_final_cycle_is_unlabelled(self):
        self.assertEqual(cad("[A] --> [B] /:C* /:C+ N", "<N:A>A"), "BA")
        # The N bracket is erased before the final cycle, so /:C- N then admits both A's.
        self.assertEqual(cad("[A] --> [B] /:C* /:C- N", "<N:A>A"), "BB")
        self.assertEqual(cad("[A] --> [B] /:C*", "<N:A>A"), "BB")

    def test_focus_belongs_to_innermost_bracket(self):
        self.assertEqual(cad("[A] --> [B] / [C] ___ [D] /:C+ V", "<V:<N:CAD>CAD>"), "<V:<N:CAD>CBD>")
        self.assertEqual(cad("[A] --> [B] /:C- N", "<V:<N:CAD>CAD>A"), "<V:<N:CAD>CBD>B")

    def test_outside_material_is_invisible(self):
        # The D before the bracket cannot serve as the context inside the N domain.
        self.assertEqual(cad("[A] --> [B] / [D] ___ /:C+ N", "CAD<N:ACD>"), "CAD<N:ACD>")
        self.assertEqual(cad("[A] --> [B] / [D] ___", "CAD<N:ACD>"), "CAD<N:BCD>")

    def test_domain_edges_are_form_edges(self):
        self.assertEqual(cad("[D] --> [B] / ___ # /:C+ N", "<N:CAD>CAD"), "<N:CAB>CAD")
        self.assertEqual(cad("[C] --> [B] / # ___ /:C+ N", "CA<N:CAD>"), "CA<N:BAD>")

    def test_no_bracket_means_no_domain(self):
        self.assertEqual(cad("[A] --> [B] /:C+ N", "CAD"), "CAD")
        self.assertEqual(cad("[A] --> [B] /:C- N", "CAD"), "CBD")

    def test_modes_in_a_domain(self):
        self.assertEqual(cad("[A] --> [B] /:1 /:C+ N", "A<N:AA>"), "A<N:BA>")
        self.assertEqual(cad("[A] --> [B] / [B] ___ /* /:C+ N", "B<N:BAA>A"), "B<N:BBB>A")
        self.assertEqual(cad("[A] --> 0 /:C+ N", "A<N:CAD>"), "A<N:CD>")
        self.assertEqual(cad("0 --> [E] / [D] ___ # /:C+ N", "<N:CD>A"), "<N:CDE>A")


class GroupModifierTests(unittest.TestCase):
    """Spec §8.8: modifiers after ]] apply to the group as a rule."""

    def test_group_category(self):
        self.assertEqual(cad("[[\n  [A] --> [B]\n  [C] --> [D]\n]] /:C+ N", "CA<N:CA>"), "CA<N:DB>")

    def test_group_stochastic_extremes(self):
        self.assertEqual(cad("[[\n  [A] --> [B]\n]] /%100", "CA"), "CB")
        self.assertEqual(cad("[[\n  [A] --> [B]\n]] /%0", "CA"), "CA")

    def test_group_optional_forks_once(self):
        res = script("[[\n  [p] --> [b]\n  [t] --> [d]\n]] /??? /\" Lenis").apply("pata")
        self.assertEqual(outs(res), [("bada", "Lenis:yes", None), ("pata", "Lenis:no", None)])

    def test_group_dialect_and_cyclic_flags_route_through_p9(self):
        c = compile_source(CAD + _body(CYCLIC))
        g = ApplyContext().builder.build(c.rule_sections["R"].members[0])
        self.assertIsInstance(g, Group)
        self.assertTrue(g.p9)

    def test_plain_rules_keep_the_fast_path(self):
        c = compile_source(CAD + _body("[A] --> [B] / [C] ___\n[[\n  [C] --> [D]\n]] /:1"))
        ctx = ApplyContext()
        rule, group = (ctx.builder.build(m) for m in c.rule_sections["R"].members)
        self.assertIsInstance(rule, BasicRule)
        self.assertFalse(rule.p9 or group.p9 or ctx.p9)


class OptionalTests(unittest.TestCase):
    """Spec §8.7, §8.11: /??? and /???:each fork the derivation."""

    def test_optional_forks(self):
        res = script("[p] --> [b] /???").apply("pa")
        self.assertEqual(outs(res), [("ba", "R1:yes", None), ("pa", "R1:no", None)])
        self.assertEqual(res.text, "pa\tba\tR1:yes\npa\tpa\tR1:no\n")

    def test_no_fork_when_the_rule_does_not_apply(self):
        res = script("[p] --> [b] /???").apply("ta")
        self.assertEqual(outs(res), [("ta", None, None)])
        self.assertEqual(res.text, "ta\tta\n")

    def test_each_gives_all_combinations(self):
        res = script("[p] --> [b] /???:each").apply("papap")
        self.assertEqual(len(res.outputs), 8)
        self.assertEqual(len({v.text for v in res.outputs}), 8)
        self.assertEqual(res.outputs[0].text, "babab")
        self.assertEqual(res.outputs[0].label, "R1:yes R1:yes R1:yes")
        self.assertEqual(res.outputs[-1].text, "papap")

    def test_max_variants(self):
        with self.assertRaises(YascRuntimeError) as cm:
            script("[p] --> [b] /???:each").apply("ppppppp")  # 2^7 = 128 > 64
        self.assertIn("MaxVariants = 64", str(cm.exception))
        sc = script("!set MaxVariants = 2\n[p] --> [b] /???\n[t] --> [d] /???")
        with self.assertRaises(YascRuntimeError):
            sc.apply("pat")
        self.assertEqual(len(sc.apply("pa").outputs), 2)

    def test_identical_variants_merge_and_join_labels(self):
        res = script("[p] --> [b] /???\n[b] --> [p] /???").apply("pa")
        self.assertEqual(outs(res), [("pa", "R1:yes R2:yes|R1:no", None), ("ba", "R1:yes R2:no", None)])

    def test_later_rules_apply_to_every_variant(self):
        res = script("[p] --> [b] /???\n[a] --> [e]").apply("pa")
        self.assertEqual([v.text for v in res.outputs], ["be", "pe"])

    def test_named_rule_label_and_print(self):
        res = script('!print "start\\n"\n[p] --> [b] /??? /" Voicing\n!print "%O{0} %L{0}\\n" $_').apply("pa")
        self.assertEqual(res.printed, "start\nba Voicing:yes\npa Voicing:no\n")

    def test_variants_across_statements_are_merged(self):
        sc = yasc.loads(HDR + _body("[p] --> [b] /???") + "$S := Rules [[\n  [b] --> [p]\n]]\n")
        self.assertEqual(outs(sc.apply("pa")), [("pa", "R1:yes|R1:no", None)])

    def test_json_label(self):
        d = script("[p] --> [b] /???").apply("pa").to_dict()
        self.assertEqual([(o["form"], o["label"], o["dialect"]) for o in d["outputs"]],
                         [("ba", "R1:yes", None), ("pa", "R1:no", None)])


class StochasticTests(unittest.TestCase):
    """Spec §8.7: /%n and /%n:each use the seeded RNG; results are deterministic and
    records are independent (design §13 entry 102)."""

    WORD = "papapapapapapapa"

    def run_seed(self, seed, rule="[p] --> [b] /%50:each"):
        return script("!set Seed = %d\n%s" % (seed, rule)).apply(self.WORD).outputs[0].text

    def test_same_seed_same_output(self):
        first = [self.run_seed(s) for s in range(5)]
        self.assertEqual(first, [self.run_seed(s) for s in range(5)])
        self.assertGreater(len(set(first)), 1)

    def test_records_are_independent(self):
        sc = script("[p] --> [b] /%50:each")
        first = sc.apply(self.WORD).outputs[0].text
        sc.apply("papa")
        sc.apply(self.WORD + "pa")
        self.assertEqual(sc.apply(self.WORD).outputs[0].text, first)

    def test_extremes(self):
        self.assertEqual(script("[p] --> [b] /%100:each").apply("papa").outputs[0].text, "baba")
        self.assertEqual(script("[p] --> [b] /%0").apply("papa").outputs[0].text, "papa")

    def test_whole_invocation_is_all_or_nothing(self):
        seen = {self.run_seed(s, "[p] --> [b] /%50") for s in range(12)}
        self.assertEqual(seen, {self.WORD, self.WORD.replace("p", "b")})


DIALECTS = "!dialects (A B)\n"


class DialectTests(unittest.TestCase):
    """Spec §10.4, §8.6: !dialects and /:D±."""

    def test_split(self):
        res = script("[p] --> [b] /:D+ A\n[t] --> [s] /:D- A", DIALECTS).apply("pata")
        self.assertEqual(outs(res), [("bata", None, "A"), ("pasa", None, "B")])
        self.assertEqual(res.text, "pata\tbata\tA\npata\tpasa\tB\n")
        self.assertEqual([o["dialect"] for o in res.to_dict()["outputs"]], ["A", "B"])

    def test_split_inside_rules(self):
        res = script("[a] --> [e]\n!dialects (A B)\n[p] --> [b] /:D+ A").apply("pata")
        self.assertEqual(outs(res), [("bete", None, "A"), ("pete", None, "B")])

    def test_record_dialect_column(self):
        rt = script("[p] --> [b] /:D+ A", DIALECTS).runtime
        rec_b, rec_z = read_records(["#! form dialect", "pata\tB", "pata\tZ"])
        self.assertEqual(outs(rt.run(rec_b)), [("pata", None, "B")])
        res = rt.run(rec_z)
        self.assertEqual((res.outputs, res.text), ([], ""))
        self.assertIn("record dialect Z not declared", res.warnings[0])

    def test_dialect_option_matches_the_full_run(self):
        sc = script("[p] --> [b] /%50:each /:D+ B\n[t] --> [s] /%50:each", DIALECTS)
        rec, = read_records(["papatatapapatata"])
        full = [v.text for v in sc.runtime.run(rec).outputs if v.dialect == "B"]
        only = Runtime(sc.compiled, dialect="B").run(rec)
        self.assertEqual([(v.text, v.dialect) for v in only.outputs], [(full[0], "B")])

    def test_without_declared_dialects(self):
        sc = script("[p] --> [b] /:D+ A")
        self.assertEqual(outs(sc.apply("pa", dialect="A")), [("ba", None, "A")])
        self.assertEqual(outs(sc.apply("pa")), [("pa", None, None)])
        rec, = read_records(["#! form dialect", "pa\tA B"])
        self.assertEqual(outs(sc.runtime.run(rec)), [("ba", None, "A"), ("pa", None, "B")])

    def test_group_dialect_restriction(self):
        res = script("[[\n  [p] --> [b]\n]] /:D- A", DIALECTS).apply("pa")
        self.assertEqual([v.text for v in res.outputs], ["pa", "ba"])


class ReplayTests(unittest.TestCase):
    """P9 decision 5: exploration by replay composes with traces, invocations, domains and
    modes."""

    def test_trace_prefix_is_not_repeated(self):
        res = script("[a] --> [e]\n[p] --> [b] /???").apply("pa", trace=True)
        self.assertEqual([s.rule_id for s in res.trace], [1, 2])

    def test_fork_detection_sees_invocations(self):
        sc = yasc.loads(HDR + "$A := Rules [[\n  [p] --> [b] /???\n]]\n" + _body("$A"))
        self.assertTrue(sc.runtime._forks(sc.compiled.rule_sections["R"]))
        self.assertEqual(outs(sc.apply("pa")), [("ba", "R1:yes|R1:no R1:yes", None), ("pa", "R1:no R1:no", None)])

    def test_optional_rule_in_a_domain(self):
        res = yasc.loads(CAD + _body("[A] --> [B] /??? /:C+ N")).apply("A<N:A>")
        self.assertEqual(outs(res), [("A<N:B>", "R1:yes", None), ("A<N:A>", "R1:no", None)])

    def test_each_in_once_mode_tries_later_foci(self):
        res = script("[p] --> [b] /???:each /:1").apply("pap")
        self.assertEqual(outs(res), [("bap", "R1:yes", None), ("pab", "R1:no R1:yes", None),
                                     ("pap", "R1:no R1:no", None)])


HDR_C = HDR.replace("  Back Binary\n]]", "  Back Binary\n  Constraint * {-Syll} {-Syll}\n]]", 1)


class ConstraintTests(unittest.TestCase):
    """Spec §4.6: constraints are inert unless EnforceConstraints = on; then they filter
    the output of every rule (P9 decision 9)."""

    def run_c(self, rules, text, enforce=True):
        pre = "!set EnforceConstraints = on\n" if enforce else ""
        return yasc.loads(HDR_C + pre + _body(rules)).apply(text).outputs[0].text

    def test_inert_by_default(self):
        self.assertEqual(self.run_c("V --> 0 / C ___ C", "patak", enforce=False), "ptk")

    def test_enforced_constraints_block_outputs(self):
        self.assertEqual(self.run_c("V --> 0 / C ___ C", "patak"), "patak")
        self.assertEqual(self.run_c("0 --> [s] / ___ [t]", "ata"), "ata")
        self.assertEqual(self.run_c("V --> 0 / C ___ C /:1", "pataka"), "pataka")

    def test_existing_violations_elsewhere_do_not_block(self):
        self.assertEqual(self.run_c("[a] --> [e]", "stak"), "stek")

    def test_api(self):
        sc = yasc.loads(HDR_C + _body("[p] --> [b]"))
        self.assertEqual([c.source for c in sc.constraints], ["Constraint * {-Syll} {-Syll}"])
        self.assertEqual(sc.violations("stapt"), [("Constraint * {-Syll} {-Syll}", 0, 2),
                                                  ("Constraint * {-Syll} {-Syll}", 3, 5)])
        self.assertTrue(sc.well_formed("pata"))
        self.assertFalse(sc.well_formed("psa"))


if __name__ == "__main__":
    unittest.main()
