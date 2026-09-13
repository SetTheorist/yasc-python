# YASC — Implementation Plan

## Status
**Every phase, P0 to P10, is complete** (2026-09-12). The language of `docs/specification.md`
is fully implemented; the standard library (`yasc/lib/ipa.yasc`), the tools (`yasc/tools/`),
the user manual (`docs/manual.md`) and the LLM guide (`docs/llm-guide.md`) are written, and
every example has an expected-output test. Decisions are in `docs/design.md` §13 (entries
1–173; P10 is 163–173). The plan below is kept as a record.

Specification: `docs/specification.md`. Design: `docs/design.md`. The work is split into phases,
each small enough for one subagent. Every phase ends with passing tests
(`python3 -m unittest discover -s tests`) and freezes the interfaces that later phases rely on.
Only the standard library may be used, and Python 3.9 or later is required.

## Ground rules for every phase (give these to each subagent)
1. Read `docs/specification.md` and `docs/design.md` in full before coding. Phase-specific
   sections are listed below.
2. Keep to the module layout in design §2. Do not change a frozen interface without recording
   it in `docs/design.md` §12 ("Interface changes").
3. Every public function has a docstring that cites the spec section it implements.
4. Tests go in `tests/test_<module>.py` and must be deterministic.
5. Any spec ambiguity you resolve goes into `docs/design.md` §13 ("Decisions log"), with the
   decision and the reason.
6. Finish by running the full test suite. Report what was built, which tests exist, and any
   deviations from the spec.

## Phase overview and dependencies

```
P0 skeleton ─┬─ P1 features/segments ─┬─ P2 patterns+matcher ─┐
             │                        └─ P3 forms+orthography ─┼─ P4 parser ─ P5 rule engine ─ P6 runtime/CLI
             │                                                 │                                 │
             │                                                 └──────────────── P7 syllables ───┤
             │                                                                    P8 tiers/tone ─┤
             │                                                                    P9 advanced ───┤
             └──────────────────────────────────────────────────────────────── P10 tools/docs ───┘
```

P2 and P3 can run in parallel once P1 is done. P7, P8 and P9 can run in parallel once P6 is
done.

---

### P0 — Skeleton
- **Create:**
  - the package `yasc/` containing `__init__.py`, `errors.py` (exception hierarchy,
    `SourceLoc`, formatted caret messages) and `__main__.py` (a stub);
  - `tests/`;
  - `examples/` (copy the revised Appendix B script);
  - `README.md` (short).
- **Acceptance:** `python3 -m yasc --help` runs, and the test suite runs (empty).

### P1 — Feature system and segments
- **Spec:** §4 and §6.2. **Design:** §3 and §4.
- **Modules:** `yasc/features.py` and `yasc/segment.py`.
- **Build:**
  - `FeatureType` (Unary, Binary, Scalar, Enumerated, Node), with operations and the `_`
    (undefined) result;
  - `Feature` (aliases, parent/children, scope, tier flag);
  - `FeatureSystem`:
    - registration, alias resolution, geometry validation;
    - `apply_op`, `preimages(op, value)`;
    - implication objects, and `apply_implications(seg, changed_features)` with fixed-point
      iteration and cycle detection.
  - `Segment` (immutable, interned, hashable; stores values in a fixed feature order).
  - `SegmentSpec`, compiled constraints: `Eq`, `In`, `Absent`, `Cmp`, `Var`, `WeakVar`,
    `OpVar`.
  - `Env`, a persistent binding environment.
  - The functions `spec.match(seg, env) -> iterator[Env]`, `spec.apply(seg, env) -> Segment`
    (output semantics) and `weak_apply`.
  - Node semantics: derived presence, subtree binding and delinking.
- **Tests:**
  - every row of the constraint table in §6.2;
  - op-variable preimages, including non-injective ops;
  - binding a Node subtree (place assimilation at the segment level);
  - implication chains and cycle errors;
  - bidirectional implication expansion.
- **Acceptance:** everything above is covered, with no parser involved (Python constructors
  only).

### P2 — Pattern AST, NFA compiler and matcher
- **Spec:** §6.1, §6.3, §6.4, §5.2 and §8.5. **Design:** §5 and §6.
- **Modules:** `yasc/pattern.py` (AST and `canonical()`), `yasc/nfa.py` (Thompson construction
  and reversal) and `yasc/matcher.py`.
- **Build:**
  - edge kinds: `Eps`, `Seg(spec)`, `Assert(gap-predicate)`, `CapOpen(n)` / `CapClose(n)`,
    `AltIndex(k, i)`, `BackRef(n)`;
  - anchored matching, forward and backward, which returns every `(end_gap, Env)` in the
    deterministic order of §6.3;
  - skipping gaps, and invisible segments through a visibility mask;
  - memoisation of `spec.match` per (spec, segment) for specs without variables.
- **Form stub:** P3 provides the real `Form`, but only a minimal interface is needed:
  `n`, `seg(i)` and `gap_marks(i)`. Code against a `Protocol` so that P2 can proceed in
  parallel with P3.
- **Tests:**
  - every construct in the §6.1 table;
  - backward matching equals forward matching on the reversed form;
  - boundary strength (§5.2);
  - back-references and captures;
  - disjunction indices;
  - a regression test for each `ret.py` bug listed in design §8.
- **Acceptance:** 10k matches of a 5-element pattern on 8-segment forms take under 1 s.

### P3 — Forms and orthography
- **Spec:** §5.1–§5.3 and §5.6. **Design:** §4.3 and §9.
- **Modules:** `yasc/form.py` and `yasc/orthography.py`.
- **Build:**
  - `Form`, an immutable value with segments, gap marks, brackets, tiers (placeholders), record
    data, and `replace(i, j, new_segments)` that keeps boundaries and brackets correct;
  - `Orthography`:
    - additive phone declarations, diacritics (prefix, postfix, circumfix), the ignore set,
      separators and bracket delimiters;
    - `parse(text) -> Form` using the DP tokenizer and `OnUnparsable`;
    - `render(form)`, with a nearest-base search plus greedy diacritics, memoised, and
      reporting approximate renderings.
- **Tests:**
  - an X-SAMPA table round trip, using `orig-notes/xsampa` graphemes with a small feature set;
  - ambiguous tokenisations;
  - diacritic stacking;
  - circumfixes;
  - separators becoming gap marks;
  - brackets `<V:<N:CAD>CAD>`;
  - error messages for unparsable input.

### P4 — Parser
- **Spec:** §3, the syntax parts of §4–§10, and Appendix B. **Design:** §7.
- **Modules:** `yasc/lexer.py` (line-oriented, context-sensitive modes: top, phonology,
  orthography, pattern, bracketed-orthographic, string) and `yasc/parser.py` (recursive
  descent, producing an AST with a `SourceLoc` on every node). There is also `yasc/compile.py`,
  which turns the AST into P1–P3 objects, resolves macros, and checks the feature and value
  types.
- **Scope:**
  - covers every construct in the spec, including those whose semantics come in later phases.
    The parser must accept them, and the compiler may raise `NotImplementedYet` with the
    location;
  - error recovery at line level, with up to 20 errors reported per load.
- **Tests:**
  - a parse test for each syntax table row;
  - the `canonical()` round trip (§11.3);
  - an error-message snapshot test;
  - parsing `orig-notes/Yasc-Example.yasc` after the §A1 comment fix-ups.

### P5 — Rule engine
- **Spec:** §8.1–§8.5, §8.7 (excluding `/:T`, `/???` and `/%`), §8.8 and §8.9.
  **Design:** §8 and §10.
- **Module:** `yasc/rules.py`.
- **Carry-over from P2/P3** (design §13):
  - Pass `first_specs=nfa.first_specs` to `matcher.find_all`; the pre-filter is off by
    default (entry 42).
  - Use `matcher.match_context` for `C ___ D`. It is lazy, so a negative context can stop at
    the first match (entry 44).
  - Choose `Form.replace(..., attach=)` for pure insertions. `"left"` is the default, which
    gives `C e #`. Use `"right"` when the context has a boundary immediately left of the
    locus, as in `# ___ C` prothesis (entry 50).
  - Pass `align=` for `$n` spans and metathesis (entry 51).
- **Build:**
  - `BasicRule`: focus search, contexts, negative contexts, filters, RHS construction
    (alignment, `~`, `'`, `$n`, class correspondence), and the implications hook;
  - the modes simultaneous, once, iterative and repeat, in both directions;
  - visibility;
  - the groups `Seq`, `And` (with undo) and `Or`, modifier inheritance, and persistent rules;
  - `ApplyContext`, which carries settings, the RNG, trace sink, record data and date filter.
- **Tests:**
  - a "golden derivation" table: (rule text, input, expected output), at least 80 cases. It
    must include each example from spec §8, including the three equivalent formulations in
    §8.4;
  - spreading with `/*` compared with simultaneous mode;
  - metathesis and gemination;
  - epenthesis at word edges;
  - `&&` undo;
  - chain shift with `||`;
  - iteration cap errors.

### P6 — Runtime, I/O and CLI
- **Spec:** §8.10, §10 (except §10.4 and paradigms) and §11–§12.
- **Modules:** `yasc/runtime.py` (the script interpreter: two-stage execution, variables,
  commands, `!print` formatting, `!assert`), `yasc/lexicon.py` (TSV, CSV, lines, regex; special
  columns) and `yasc/cli.py`. It also produces the public API in `yasc/__init__.py`.
- **Build:** dates, lexical features, trace, JSON output, `--list-rules`, `--check`, the
  deterministic seed, and error handling per record.
- **Tests:**
  - end-to-end CLI tests on `examples/` with expected output files;
  - the dates wishlist scenario ("television 1954");
  - JSON schema stability.
- **Acceptance:** a lexicon of 1,000 records × 100 simple rules runs in 10 s or less.

### P7 — Syllabification and syllable features
- **Carry-over from the Latin → Spanish example:** see `examples/latin-spanish/notes.md` §4.
  - Resolve the compiler bug C2: the role pseudo-features `SylCoda` and the rest raise an
    "unknown feature" error instead of compiling as P7 placeholders.
  - Settle the spec questions S1–S4: whether stress survives resyllabification, segments left
    unsyllabified after syncope, VV hiatus, and a default value for syllable features.
  - Make the full Latin example run end to end.
- **Spec:** §5.4 and §5.7.
- **Module:** `yasc/syllable.py`.
- **Build:**
  - the MaxOnset and Canon algorithms;
  - the syllable tier;
  - role pseudo-features;
  - `Scope(Syllable)` features;
  - the `.` assertion;
  - upkeep after rules;
  - `Persistent` syllabification, `/:$` and `!syllabify`;
  - `SyllableMark` in the orthography.
- **Tests:**
  - `abamordi` under both algorithms, as in the notes;
  - onset-required behaviour;
  - `NucleusPreference` with `bui`;
  - coda devoicing via `SylCoda`;
  - stress marks round trip.

### P8 — Autosegmental tiers
- **Spec:** §5.5 and §6.5. **Design:** §11.
- **Module:** `yasc/tiers.py`, with extensions to the matcher (the `S^X` and `^X` edges) and to
  the RHS.
- **Build:**
  - autosegments and links;
  - the No-Crossing check;
  - the segment view;
  - writing through the view;
  - stray handling;
  - tier-only rules (`/:T`);
  - `!associate` and `!ocp`;
  - rendering floating tones.
- **Tests:**
  - left-to-right association with last-tone spreading;
  - floating H docking;
  - OCP merge;
  - downstep as a tier rule;
  - rejection of line crossings.

### P9 — Advanced rule features
- **Spec:** §8.6 (categories, `/:C*`), §8.7 (`/%`, `/???`), §8.11, §9, §10.4 and §4.6.
- **Build:**
  - cyclic categorical application;
  - optional and stochastic rules with variants and labels;
  - dialects;
  - paradigms;
  - constraints as global output filters.
- **Tests:**
  - `<V:<N:CAD>CAD>` → `CEDCBD`;
  - variant explosion capped by `MaxVariants`;
  - a dialect split with `--wide`;
  - a Latin-style paradigm.

### P10 — Tools, standard library and documentation
- **Build:**
  - `yasc/lib/ipa.yasc`: a standard feature system (Hayes-style) with an X-SAMPA and IPA
    orthography for the inventory in `orig-notes/xsampa`, including the diacritics from
    `orig-notes/ortho`;
  - `yasc/tools/minimize.py`: a greedy entropy-based minimal feature set, fixing the
    `ret.py` bugs from design §8;
  - `yasc tools make-ortho` (X-SAMPA phone list → orthography);
  - `docs/manual.md`: a user manual that finishes `Yasc-Manual.tex`, written in Markdown;
  - examples: Grimm's law, Romance palatalisation, Bantu tone spreading, and a conlang with
    SCA-style classes;
  - `docs/llm-guide.md`: a compact reference for LLM agents (syntax cheat-sheet, JSON output,
    common pitfalls).
- **Acceptance:** each example has an expected-output test.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Autosegmental notation is the least settled design | P8 is late and isolated; the segment view (P1/P5) already covers basic tone rules |
| Performance of NFA simulation with bindings | memoisation, a pre-filter on the LHS's first spec, deduplicating threads by (node, env) — design §10 |
| Ambiguities between comments, `#` and X-SAMPA | spec §3.2 and §5.6 rules; lexer modes; tests on the X-SAMPA table |
| The spec changes during implementation | decisions log (design §13); canonical round-trip tests catch drift |
