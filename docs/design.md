# YASC — Design

This document explains how `docs/specification.md` is implemented. Section numbers here are
referenced from `plan.md`.

## 1. Principles
- **Immutable values.** `Segment`, `Form` and `Env` never change after creation. A rule
  application returns a new `Form`. Undo (`&&`), variants and traces therefore come for free,
  with no defensive copies. This removes the aliasing bugs in `ret.py`, such as a shared `a`
  list and `Segment.__init__(v={})` with a mutable default.
- **Compile once, run many.** Everything parsed at load time is compiled into plain objects:
  feature indices, NFAs, and specs with pre-resolved features. The run-time loop does no string
  parsing.
- **One matcher.** Rule LHS, contexts, filters, constraints and syllable templates all use the
  same NFA engine (§5–§6).
- **Small modules, explicit data flow.** Modules have no global state; configuration travels in
  an `ApplyContext`.
- **Deterministic everywhere.** Iteration is over ordered structures only (lists, and dicts in
  insertion order), and there is one seeded `random.Random`.

## 2. Package layout

```
yasc/
  __init__.py      public API: load, loads, Script, Result (P6)
  __main__.py      python -m yasc  -> cli.main
  errors.py        SourceLoc, YascError hierarchy, caret formatting (P0)
  features.py      FeatureType, Feature, FeatureSystem, Implication (P1)
  segment.py       Segment, SegmentSpec, constraints, Env (P1)
  pattern.py       pattern AST nodes + canonical() (P2)
  nfa.py           NFA graph, Thompson construction, reversal (P2)
  matcher.py       anchored NFA simulation with environments (P2)
  form.py          Form, gaps/boundaries, brackets, tier containers (P3)
  orthography.py   Orthography: parse/render (P3)
  lexer.py         context-sensitive line lexer (P4)
  syntax.py        statement/section AST nodes with canonical() (P4)
  parser.py        recursive-descent parser -> AST (P4)
  compile.py       AST -> runtime objects, macro expansion, type checks (P4)
  ir.py            data-only rule IR and CompiledScript for P5/P6 (P4)
  rules.py         BasicRule, groups, modes, persistent rules, ApplyContext (P5)
  runtime.py       Script interpreter, variables, commands, printing (P6)
  lexicon.py       record readers (P6)
  cli.py           argument parsing, output formats (P6)
  syllable.py      syllabification algorithms, syllable tier (P7)
  tiers.py         autosegmental tiers (P8)
  paradigm.py      paradigms (P9)
  lib/ipa.yasc     standard library: $IPAPhonology, $IPA, $XSAMPA; !include "lib:ipa.yasc" (P10)
  tools/           minimize.py, make_ortho.py; python3 -m yasc.tools.NAME (P10)
tests/            test_<module>.py; test_lib, test_tools, test_examples, test_docs, test_p10 (P10)
examples/         one directory per example, each with an expected-output file; README.md
docs/             specification.md, design.md, manual.md, llm-guide.md (P10)
```

## 3. Feature system (`features.py`)

```python
class FeatureType:            # immutable
    values: tuple[str, ...]   # declaration order; () for Node
    ops: dict[str, tuple[str|None, ...]]   # op -> result per value index; None = undefined
    kind: Literal["unary","binary","scalar","enum","node"]
    def apply(self, op, v) -> str | None
    def preimages(self, op, v) -> tuple[str, ...]   # precomputed inverse table

class Feature:
    name: str; index: int; aliases: tuple[str, ...]
    type: FeatureType
    parent: Feature | None; children: tuple[Feature, ...]
    scope: Literal["segment","syllable"]; tier: TierDecl | None
    descendants: tuple[int, ...]       # leaf feature indices (Node only)

class FeatureSystem:
    features: list[Feature]; by_name: dict[str, Feature]   # aliases included
    implications: list[Implication]    # bidirectional forms are expanded
    def feature(self, name, loc) -> Feature                # raises with a suggestion (difflib)
    def close(self, seg: Segment, changed: frozenset[int]) -> Segment   # implications
```

- **Implication closure.** Each implication is compiled to `(trigger_spec, output_spec,
  weak: bool, trigger_features: frozenset[int])`. `close()` keeps a work-set of changed feature
  indices and considers only implications whose `trigger_features` intersect it. Applying an
  implication adds the features it changed to the work-set. A pass counter enforces
  `4 × len(implications)` and raises `YascRuntimeError("implication cycle", ...)` when it is
  exceeded.
- **Nodes** store nothing. `Segment.get(node)` computes presence from the descendants.
  `(p)Place` binds a `BundleValue`, which is a frozen tuple of `(index, value)` pairs over the
  descendants.

## 4. Segments, specs, environments, forms

### 4.1 `Segment`
- Values are stored as a tuple with one slot per feature index; `None` means unspecified. The
  tuple gives O(1) access, cheap equality and a cached hash.
- **Interning.** `Segment.make(values)` looks the tuple up in a `WeakValueDictionary`, so equal
  segments are usually the same object. This lets per-(spec, segment) match caches use
  identity.
- The tuple is extended lazily when features are added later, as lexical features are, for
  instance. The feature system is sealed after loading, and lexical features live in a
  separate record bundle, so segments keep a fixed width.
- Syllable-scope and tier features are **not** stored on the segment. Reading them goes through
  the `Form` (§4.3). That is why `SegmentSpec.match` receives a `SegView`: a
  `(form, index)`-aware accessor. For the common case, where the spec mentions only plain
  features, a fast path reads the `Segment` tuple directly.

### 4.2 `SegmentSpec` and `Env`
- A `SegmentSpec` is a tuple of constraints, sorted by feature index, plus the flags
  `strict: bool` and `has_vars: bool`.
- The constraint classes are `Eq`, `In`, `Absent`, `Cmp`, `Var(name, ops)`, `WeakVar(name)` and
  `OpVar`, which is the same as `Var` with ops.
- `match(view, env) -> Iterator[Env]`: specs without variables yield `env` or nothing. An
  `OpVar` with an unbound variable yields one `Env` per preimage.
- `Env` is a persistent map, implemented as an immutable tuple-backed dict with a cached hash.
  It holds four kinds of entry:
  - `vars`: name → value or `BundleValue` or `UNSPEC`;
  - `caps`: n → (i, j), a segment span;
  - `alts`: disjunction id → index;
  - `autos`: name → autosegment id.
- Envs are small (usually fewer than 5 entries), so a sorted tuple with copy-on-extend is
  simpler and faster than a HAMT.

### 4.3 `Form` (`form.py`)
```python
@dataclass(frozen=True)
class Form:
    segs: tuple[Segment, ...]
    gaps: tuple[frozenset[Mark], ...]      # len = len(segs)+1; gaps[0], gaps[-1] include PHRASE
    brackets: tuple[Bracket, ...]          # (label, open_gap, close_gap), properly nested
    syllables: SyllableTier | None
    tiers: tuple[AutoTier, ...]            # one per Tier feature
    def replace(self, i, j, new: Sequence[Segment]) -> "Form"
```

`replace` performs these steps:

1. Splice the segments.
2. Union the marks of the gaps inside `[i, j]` into the single new gap if nothing is inserted.
   Otherwise put them at the edges `i` and `i+len(new)`: interior marks go to the left edge,
   and the spec says "kept where positions survive" (spec §8.2.4).
3. Shift the bracket gap indices.
4. Call `syllables.after_replace(...)` (spec §5.4 upkeep).
5. Call `tier.after_replace(...)` for each tier, which removes the links of deleted segments
   and handles strays.

Simultaneous application does a single right-to-left pass of `replace` over the sorted foci, so
that indices stay valid.

## 5. Patterns and NFA (`pattern.py`, `nfa.py`)

### 5.1 AST
```
Pattern = Spec(SegmentSpec) | Ortho(tuple[SegmentSpec]) | Macro(name)   # macros expanded in compile
        | Nothing | Boundary(mark) | BracketAssert(open|close, label|*)
        | Seq(items) | Alt(id, items) | Opt(p) | Star(p) | Plus(p) | Anything
        | Locus | BackRef(n) | Capture(n, p)       # Capture inserted by compile for LHS items
        | AutoFloat(tier, X, name) | Linked(spec, tier, X, exact, name)
```
Every node has a `loc` and a `canonical()` method.

### 5.2 NFA
```python
class NFA:
    start: int; accept: int
    edges: list[list[Edge]]        # adjacency by state
Edge = (kind, payload, target)
kinds: EPS | SEG(spec) | ASSERT(pred) | CAP_OPEN(n) | CAP_CLOSE(n)
       | ALT(id, index) | BACKREF(n) | LINK(...) | FLOAT(...)
```
- The NFA is built by a standard Thompson construction with **ordered** ε-edges. The order of
  the out-edges encodes preference: for `Star`, the loop comes before the exit, which gives
  greedy matching, and alternatives are kept in textual order. The matcher's deterministic
  ordering (spec §6.3) comes from this.
- `ASSERT` predicates are small objects with `holds(form, gap) -> bool`. Examples are
  `MarkAtLeast(MORPHEME)` and `BracketOpen("N")`.
- **Reversal** for backward context matching reverses every edge and swaps `start` and
  `accept`. Captures swap open and close, and `ASSERT` predicates on gaps need no change,
  because gaps are symmetric. Reversal is done once at compile time, so each context stores
  `(fwd_nfa for D, rev_nfa for C)`.
- **ε-closure is precomputed per state.** It is a list of `(target, ops)`, where `ops` is the
  ordered list of non-consuming edge actions (`ASSERT`, `CAP_*` and `ALT`) along the path.
  ε-cycles, which arise from `(P*)*`, are cut by never re-entering a state on the current path
  and expanding each state once per distinct `ops` list (decision 36; "once per closure" would
  drop matches such as `(#) V`). Because
  assertions depend on the gap, the matcher evaluates `ops` at run time; only the graph walk is
  precomputed.

## 6. Matcher (`matcher.py`)

```python
def match_anchored(nfa, form, start_gap, env, *, direction=+1, visible=None, stop_at=None)
        -> list[tuple[int, Env]]          # (end_gap, env) in preference order
```

The algorithm is a Pike-VM-style breadth-first simulation over positions, with environments
carried along in each thread.

1. `threads = closure(start, gap=start_gap, env)`. Each thread is a pair `(state, env)`.
   Duplicates are removed by that pair, keeping the first occurrence, which preserves the
   preference order.
2. At each gap `g`: record the accepting threads as results `(g, env)`. Then, for each thread,
   follow the consuming edges on the next *visible* segment in `direction`:
   - `SEG` calls `spec.match(view, env)`, which may yield several envs;
   - `BACKREF(n)` compares a whole captured span and jumps ahead by its length. This is handled
     by a pending-jump queue keyed by the target gap.
   - Advance `g`, skipping invisible segments (their gaps merge; see spec §8.5).
3. Stop at the form edge, or when no threads are left.
4. Order the results by end gap, longest first. Within the same end gap, keep the thread
   preference order.

**Unanchored search** (focus search) calls `match_anchored` from each start gap in rule
direction order. This is O(n²·|NFA|) in the worst case, but words are short (n ≲ 15), so it is
simpler and fast enough. It also gives exact "leftmost first, then longest" ordering, which a
single-pass `.*`-prefixed NFA (the `ret.py` "anywhere" trick) cannot guarantee.

**Pre-filter.** At compile time each LHS computes a `first_specs` set: the specs that can
consume its first segment. Start gaps whose next segment matches none of them are skipped. If
the LHS may be empty, the pre-filter is disabled.

**Caching.** `spec.match` results for variable-free specs are cached in a dict keyed by
`(spec_id, segment_id)`. Segments are interned, so this cache is effective across the whole
lexicon.

## 7. Lexer, parser, compiler

### 7.1 Lexer (`lexer.py`)
The input is first split into **logical lines**:

1. strip `%%` comments and `#`-comment lines (spec §3.2);
2. join lines at `/\`;
3. keep `[[` and `]]` on their own tokens.

Then tokens are produced per line with a **mode** that the parser selects, because the same
characters mean different things in different places:

| Mode | Used for | Notes |
|---|---|---|
| `top` | identifiers, `$var`, `:=`, `===`, `!command`, section keywords | |
| `phon` | feature declaration lines | value tokens per spec §4.1 |
| `ortho` | `[...]` grapheme lists (whitespace-split, no escapes), `{...}`, `==>`, settings | |
| `pattern` | the token set of spec §3.4 | longest match: `<<` before `<`, `##` before `#`, `___`+ as LOCUS, `-->` before `-` |
| `spec` | inside `{}`: value prefix + feature name, `(a)`, `(?a)`, op-prefixed variables, nested `{v v}F`, comparisons | |
| `string` | `"..."` with `\n \t \" \\` | |

### 7.2 Parser (`parser.py`)
A hand-written recursive descent parser; the grammar sketch is below. Each production records a
`SourceLoc`. For error recovery, on an error the parser skips to the next logical line; at
`[[`/`]]` it keeps a depth counter so that block structure survives.

```
script     := stmt*
stmt       := assign | macro | section | command | rule_item
section    := ['$' ID ':='] KIND '[[' mods? NL body ']]' mods?
rule_item  := basic_rule | group | '$' ID | command | assign
basic_rule := rhs_pat '-->' rhs mods
group      := ('&&' | '||')? '[[' mods? NL rule_item* ']]' mods?
mods       := ( '/' ctx | '/!' ctx | '/:' KEY arg? | '/*' | '/%' NUM (':each')? | '/???' (':each')?
               | '/"' ID | '/::' | '/~' )*
pattern    := alt_seq ;  alt := '<<' pattern ('|' pattern)* '>>' ; postfix := atom ('*'|'+'|'?')?
```

- **`-` ambiguity.** In pattern mode, a `-` token is a morpheme boundary. `-->` is recognised
  first by longest match, and `{-Voice}` is lexed in spec mode, so no conflict remains.
- **`.` ambiguity.** `.` is the syllable assertion and `...` is Anything; longest match
  resolves them.
- **`0`** is the Nothing element only when it stands alone. Inside `{}`, `0Stress` is a value.

### 7.3 Compiler (`compile.py`)
- Resolves feature names and aliases, and type-checks values.
- Expands macros, detecting cycles.
- Numbers the top-level LHS items and wraps them in `Capture`.
- Assigns disjunction ids, and checks class-correspondence arity (spec §8.2.4).
- Builds and reverses the NFAs; computes `first_specs`.
- Validates modifiers per rule and per group (spec §8.8), and applies inheritance.
- Checks that dates are monotonic.
- Produces `rules.Rule` objects.
- Collects all errors before raising `YascLoadError(errors)`.

## 8. Review of `orig-notes/ret.py`: bugs and how the design fixes them

The prototype's architecture is kept: Thompson NFA, epsilon edges with meta flags marking the
focus, asymmetric unification, rule classes for disjunction and conjunction. Its defects were
found by reading the code; each one becomes a regression test in P2, P3 or P5.

| # | Location | Defect | Fix in this design |
|---|---|---|---|
| R1 | whole file | Python 2 (`print` statement, `has_key`, `xrange`, `unicode`) | Python 3 rewrite |
| R2 | `epsilon_closure` | `a.extend(e.meta_flags)` mutates a list shared across sibling ε-edges, so meta flags leak between branches. `new_na not in x` compares list values, so the closure can revisit states and grow without bound on flag-accumulating cycles | ordered closure with per-path immutable op lists, and a visited set per state (§5.2) |
| R3 | `Node.follow_match` | returns only the **first** matching out-edge ("at most one exit edge matching"). This is false for NFAs (alternatives, overlapping specs), so matches are lost | follow all edges; every env the spec yields becomes its own thread (§6) |
| R4 | `Matcher.next_match` | results are `pop()`ed, which is LIFO and gives no defined leftmost or longest order. Stops after the first position that yields any match | ordered results (§6), start-gap enumeration |
| R5 | `BasicRule` "anywhere" | the prefix `{}*` loses the real match start, and the `[`/`]` meta flags are overwritten on multiple ε paths | explicit start-gap loop; `Capture` edges carry positions in the Env |
| R6 | `Segment.unify` case (f) | binds a variable to a variable, which can create a cycle, and `Val.instantiate` then loops forever | the word side never contains variables; bindings are always to values |
| R7 | `Segment.unify` with ops | a pattern `-(a)High` against `+` binds `a := +` and ignores the op | bind to the op's preimages (`FeatureType.preimages`) |
| R8 | `Segment.unify` | `OPT_VARIABLE` (`(?a)`) is parsed but never handled | `WeakVar` constraint |
| R9 | `Segment.__hash__` | `hash(str(dict))` depends on insertion order, so equal segments can hash differently | tuple storage by feature index, interned |
| R10 | `Segment.__init__(v={})` | mutable default argument | immutable construction |
| R11 | `Segment.__delitem__` | does not delete anything | no mutation API |
| R12 | `Segment.merge` | `newval[f] = ...` depends on `__setitem__` of a *copy*, which works by accident; `weak_merge` has confusing semantics | `spec.apply` / `weak_apply` defined by spec §6.2 |
| R13 | `MatchTree.compile_graph` | calls the nonexistent `g.disjunct`; starting from an empty `Graph()` adds a spurious empty alternative; `'+'` is not a supported kind but is used in the tests | AST with a `Plus` node; `Alt` builds a single fork state |
| R14 | `ParallelMatcher` | broken: `self.graph` is undefined, `(None)*n`, `n` is undefined | not needed: contexts are checked by anchored matching (spec §8.2, design §10) rather than a product automaton |
| R15 | `IterativeRule` | restarts at `start` every time, so it re-matches the same spot; returns `None` if the rule never applied, which makes `word` indistinguishable from failure; prints on runaway instead of raising | modes of spec §8.3 with progress guarantees; a `Result(form, applied)` return value |
| R16 | `ConjunctionRule` | passes `require_change=False` to the members, so "applies" really means "matched" | `And` group with explicit `applied` flags and undo (immutable forms make undo trivial) |
| R17 | `BasicRule.apply` | aligns the RHS to the LHS positionally, but excess RHS segments stay as bare bundles with no defaults, and boundaries are not represented at all | spec §8.2.4, and implications fill in defaults |
| R18 | `Orthography.segment_to_string` | tests `metric != 0` (the last loop value) instead of `best_val`; only one diacritic can be applied; ties depend on dict order | search in spec §5.6, memoised |
| R19 | `parse_segment_string` | the `ws` regex `'(?:\s'+igs+')+'` is wrong: with ignorables present it becomes `(?:\sx\|y)`. Garbage is silently skipped | DP tokenizer; `OnUnparsable` |
| R20 | `minimize_phonology` | `features_used = [].extend(...)` is `None`, so it crashes immediately; also stops wrongly when all features are needed (as its own TODO notes) | rewritten in P10 |
| R21 | `FEATURE_PATTERN` | features must be alphabetic, so a digit value like `2Stress` works but `_` and `(...)`-values are handled implicitly and fragilely | dedicated spec-mode lexer |
| R22 | `Lexer` | recursion per skipped character (a stack overflow on long garbage); `/` and `/!` sorted with reverse string sort, which only works by accident | a line lexer with modes |

## 9. Orthography algorithms (`orthography.py`)

**Parsing** runs one dynamic program over the character positions of the whole text.
Separators, syllable marks, bracket delimiters and whitespace are transitions in the same
program, ranked after grapheme units, so X-SAMPA `p_>` beats the `>` delimiter (decision 54).

- `best[k]` holds the best tokenisation of `text[:k]`, scored by the tuple
  `(unparsed_chars, tokens, -longest_grapheme_bonus)`, with lower being better.
- The transitions from position `k` are:
  - a grapheme `g` starting at `k`, found through a trie of all graphemes;
  - a prefix diacritic;
  - a postfix diacritic attached to the previous token;
  - a circumfix pair around exactly one grapheme with its own stacked diacritics.
- With `OnUnparsable=keep|skip`, a one-character "unknown" transition exists at a penalty.
  With `error`, the first position that no transition reaches is reported.
- Tokens become segments. The steps are: take the base bundle, merge the diacritic bundles in
  order from innermost to outermost, then apply `FeatureSystem.close()`.

**Rendering** is memoised per `Segment` id. The steps are:

1. Look for an exact match through a dict from bundle (with ignored features projected out) to
   the first declared grapheme.
2. Otherwise compute a distance to every base: the number of mismatched non-ignored features.
   The inventory has at most a few hundred bases, so a linear scan is fine.
3. For the k nearest bases, repeatedly add the diacritic that most reduces the distance, until
   no diacritic improves it.
4. Choose by `(distance, number of diacritics, declaration order)`.
5. Circumfix and prefix/postfix placement follows the declared templates. Diacritics are put
   in declaration order when that gives the same segment; otherwise the order they were added
   in is kept, so the output always reparses to the same segment (decision 59). A round-trip
   test enforces this.

## 10. Rule engine and performance (`rules.py`)

```python
class Rule:                                     # abstract
    name: str|None; loc: SourceLoc; date: int|None; restrictions: Restrictions
    def apply(self, form: Form, ctx: ApplyContext) -> Outcome   # Outcome(form, applied: bool)

class BasicRule(Rule):
    lhs: CompiledPattern; rhs: tuple[RhsItem, ...]
    pos_ctx: tuple[Context, ...]; neg_ctx: tuple[Context, ...]
    in_filters, out_filters; mode; direction; visibility; raw; weak; tier
class Group(Rule): kind: SEQ|AND|OR; members: tuple[Rule, ...]; persistent: tuple[Rule, ...]
class CyclicCategorical(Rule): body: Group
class Optional(Rule) / Stochastic(Rule): wrappers (P9)
```

**`BasicRule.candidates(form, ctx)`** yields `(i, j, env)` in preference order. The steps are:

1. Loop over the start gaps in the rule's direction, skipping those that fail the pre-filter.
2. Call `match_anchored(lhs)` from each start gap.
3. For each `(j, env)`, check every positive context in turn with backtracking:
   - `D`: `match_anchored(fwd, j, env)`;
   - then `C`: `match_anchored(rev, i, env')`, with the direction reversed.
4. Then check the negative contexts, which must have no match, and the input filters.

**Contexts under visibility** use the same visibility mask as the LHS. Under `/:C±` domains the
mask also hides everything outside the domain.

**Modes:**

| Mode | How it is done |
|---|---|
| once | take the first candidate whose output passes the output filters |
| simultaneous | collect the candidates, keeping each one that does not overlap a kept focus and whose single-focus output passes the output filters; then build a single new `Form` by applying all the replacements right to left |
| iterative | take the first candidate, apply it, set `cursor = i + len(replacement)` (L→R), and look for the next candidate with `i ≥ cursor` in the new form. A zero-width focus that is replaced by zero width advances the cursor by 1. Count the applications against `MaxIterations` |
| repeat | loop over `apply`, keeping a `seen` set of `(segs, gaps)` for cycle detection |

**Groups:**

- SEQ folds its members over the form.
- AND folds, and returns the original form with `applied=False` at the first failure.
- OR returns the first member that applies.
- Persistent rules are kept in a list for the current scope. After any member returns
  `applied=True`, `run_persistent(form)` loops over that list until nothing changes.

**`ApplyContext`** holds:

- `settings`, `rng` and `trace` (a list of `TraceStep`, or `None`);
- `record` (lexical bundle, date, dialect, fields);
- `date_window`;
- `only` and `skip` (rule filters by name);
- `depth`, used to indent the trace.

A `TraceStep` is `(rule_id, name, loc, before: Form, after: Form, foci)`. Rendering is deferred
until output time.

**Performance budget.** The workload is 10³ words × 300 rules × about 10 start gaps, which is
about 3·10⁶ anchored matches. That is too slow if every match walks the full NFA, so the
engine avoids that work:

- the `first_specs` pre-filter removes most start gaps, and many rules can be skipped for a
  whole word;
- a per-rule **quick reject** keeps a set of the "required segment specs" (specs every match
  must consume). If one of them matches no segment of the form, the rule is skipped for that
  word. The check is O(n) per rule per word and uses the spec-match cache;
- variable-free spec matches are cached per interned segment;
- threads are deduplicated by `(state, env)`;
- the NFA for `...` is a single self-loop state.

The target is 1,000 × 100 rules in 10 s or less on CPython 3.11 (plan P6). A `--profile` CLI
flag prints the time per rule (from `time.perf_counter`), so slow rules can be tuned.

## 11. Syllables and tiers (`syllable.py`, `tiers.py`)

- **`SyllableTier`** holds `syls: tuple[Syllable]`. A `Syllable` records `(start, end,
  nucleus_start, nucleus_end, feats: Segment)`, where `feats` holds only the syllable-scope
  features. There is also `seg_to_syl: tuple[int|None]`.
  - `role(i)` is derived from the nucleus span.
  - `after_replace(i, j, k)` implements the upkeep of spec §5.4.
  - Syllable templates compile to NFAs with the same matcher; MaxOnset uses anchored backward
    matching of `Onset` from each nucleus.
- **`AutoTier`** holds `autos: tuple[Auto(id, value, anchor_gap)]` and `links:
  frozenset[(seg_index, auto_id)]`.
  - **No-Crossing** means that for any two links `(s1, a1)` and `(s2, a2)`,
    `s1 < s2 ⇒ order(a1) ≤ order(a2)`. It is checked in O(L log L) after every change.
  - The segment view is computed on demand and cached per `Form`.
  - A matcher edge `LINK(tier, X, exact, name)` checks the links of the current segment and can
    bind `autos[name]`. A `FLOAT` edge is a zero-width assertion that looks for floating
    autosegments anchored at the current gap.
  - RHS link operations are applied after the segmental replacement, using index maps from
    `Form.replace`.
- **Tier-only rules** (`/:T`) run the ordinary `BasicRule` machinery on a *projected form*. That
  form's "segments" are the autosegments, each a one-feature `Segment` with the tier value.
  After the rule runs, the changes are mapped back: deletions drop links, and insertions float.

*The tier design above is the plan; the implemented tier design (P8) is recorded in §13,
entries 128–149, and category domains and variants (P9) in entries 150–162.*

## 12. Interface changes
*(Implementers record any change to a frozen interface here: date, phase, what changed
and why.)*

- *2026-09-12, P1.* `Segment.make(system, values)` takes the feature system (§4.1 wrote
  `Segment.make(values)`). Each `FeatureSystem` owns its intern table, so the modules keep no
  global state (§1), and segments of different systems never compare equal.
- *2026-09-12, P1.* `SegmentSpec.match(view, env)` returns a **tuple** of `Env` (empty = no
  match) instead of an iterator (§4.2). It is still iterable. A tuple can be cached as it is by
  the P2 memo, and it allows `if spec.match(...)`. `SegmentSpec.matches(view, env) -> bool` is
  added.
- *2026-09-12, P1.* `FeatureSystem` additions:
  - `features`, `implications` and `by_name` are read-only snapshots (tuples and a dict copy),
    not live lists;
  - `close_new(seg)` closes a fresh segment, for the orthography;
  - `apply_implications` is an alias of `close`;
  - `segment(mapping, **kw)` is a validated builder;
  - `empty` is the all-unspecified segment;
  - `add_bidirectional(...)` expands `<-->` and its variants.
- *2026-09-12, P1.* Operation names are stored without their parentheses (`'-'`, `'++'`,
  `'<Max>'`). A composition `op1#op2(a)` is the tuple `('op1', 'op2')` in written order.

- *2026-09-12, P2.* Interface additions (details in §13, entries 26–46):
  - `yasc.pattern`:
    - `walk`, `nullable`, and the `OPEN`, `CLOSE` and `ANY_LABEL` constants;
    - `Macro.refine` and `Ortho.text`;
    - `number_lhs(pattern, whole=True)`.
  - `yasc.nfa`:
    - `NFA.first_specs`, `NFA.pattern`, `NFA.match_cache`, `NFA.clear_cache()`,
      `NFA.n_states` and `NFA.dump()`;
    - `NFA.closure[u]` holds `(target, ops)` entries, with `ops` a tuple of `(kind, payload)`
      pairs;
    - an `ALT` payload is `(id, index)`.
  - `yasc.nfa`: predicates have the signature `holds(form, lo, hi=None)`, where `lo..hi` is a
    virtual gap. `holds(form, g)` still works for a single gap.
  - `yasc.matcher`:
    - `Visibility` (a precomputed mask); `visible=` accepts `None`, a sequence of bools or a
      `Visibility`;
    - `match_anchored` accepts either orientation of the NFA and chooses the one the
      direction needs.
  - The matcher calls only `n`, `seg`, `view`, `gap_marks` and `brackets` of `FormLike`.
- *2026-09-12, P3.* Form and Orthography construction API (details in §13, entries 47–62):
  - `Form(segs, gaps, brackets=(), syllables=None, tiers=(), pending_syllable_marks=())`,
    `Form.from_segments(segs, marks=None, brackets=(), pending_syllable_marks=())`.
    `marks` can be a mapping `gap -> marks` or a sequence of n+1 collections.
  - `Form.replace(i, j, new, *, attach="left", align=None) -> Form` and
    `Form.replace_with_map(...) -> (Form, index_map)`.
  - `Form.with_marks(g, marks)`, `with_brackets(brackets)`, `erase_brackets(pred)` and
    `segments_equal(other)`.
  - `yasc.form.validate_brackets(brackets, n)` and `bracket_events(brackets, n)`.
  - `Orthography(fs, name=None, *, k=3)`. Declarations: `add_phones`, `add_diacritic`,
    `ignore`, `set_separator`, `set_delimiter`, `set_setting`, `add_syllable_mark` and
    `seal`. Other methods: `parse(text, *, on_unparsable, loc, source_line, close=True)`,
    `tokenize`, `render`, `render_ex`, `render_segment`, `phone_bundle` (declared features
    without implications, for RHS `[x]`) and `phone_segment`.
- *2026-09-12, P4.* Parser and compiler API (details in §13, entries 63–86):
  - New modules `yasc/syntax.py` (the statement and section AST) and `yasc/ir.py` (the rule
    IR). Design §7.3 said the compiler "produces `rules.Rule` objects"; P4 produces the
    data-only IR (`IRBasicRule`, `IRGroup`, `IRInvoke`, `IRCommand`, `IRAssign`,
    `IRPlaceholder` and their modifier records) instead, because `rules.py` is P5.
  - `yasc.lexer`: `split_lines(text, file) -> [LogicalLine]`, `Lexer(line).next(mode)` /
    `peek(mode)` / `match(regex)`, `tokenize(text, mode)`, and `Token(kind, text, value,
    start, end, loc)`. Constraints inside `{}` lex into `ConstraintSyntax` records.
  - `yasc.parser`: `parse(text, file) -> syntax.Script`, raising `YascLoadError`.
  - `yasc.compile`: `compile_source(text, filename, *, allow_unimplemented=False) ->
    ir.CompiledScript`, plus `compile_file(path, ...)` and `compile_script(script, ...)`.
  - `yasc.errors.YascWarning` (label `warning`, optional `phase`) is new. It is collected on
    `CompiledScript.warnings` and never raised.
  - Syntax patterns reuse the P2 composite nodes. Unresolved leaves are the new
    `syntax.RawSpec`, `RawOrtho`, `Combine` and `LinkOp`. Every syntax `Alt` has `id=None`
    until compilation.

- *2026-09-12, P5.* New module `yasc/rules.py` (details in §13, entries 87–100). No P1–P4
  interface changed.
  - `ApplyContext(settings=None, *, trace=None, record=None, date_window=None, only=(), skip=(),
    skip_pending=False, rng=None, on_command=None, builder=None)` and
    `RecordData(lexical, date, dialect, fields)`.
  - `Outcome(form, applied)`. `TraceStep(rule_id, name, loc, before, after, foci, note=None,
    depth=0)`: the six fields of §10 plus two optional ones.
  - `Builder`, `build(ir)`, `build_rules(compiled) -> {id: Rule}`, `apply_rule(rule_or_ir, form,
    ctx=None)`, `run_section(compiled, name_or_ir, form, ctx=None)`, `run_sequence(members, form,
    ctx, kind)` and `run_persistent(form, ctx)`.
  - The rule classes are `BasicRule`, `Group`, `Invoke`, `PendingRule` and `CommandItem`. The
    §10 `Rule` fields other than `name`, `loc` and `date` are reached through `rule.ir`. §7.3
    and §10 said the compiler produces `rules.Rule` objects; P5 builds them from the IR
    instead (entry 87).

- *2026-09-12, P6.* New modules `yasc/runtime.py`, `yasc/lexicon.py`; `yasc/cli.py` runs
  scripts; `yasc/__init__.py` exports the public API (details in §13, entries 101–111).
  - `yasc.rules` additions (the only change to a P1–P5 module): `ApplyContext.date_allows(date)`
    and `ApplyContext.group_date`; `BasicRule.apply`, `Group.apply` and `PendingRule.apply`
    now apply the date filter (entry 101); `PendingRule.date` is set. The `date_window`
    argument keeps its P5 signature.
  - `yasc.runtime`: `Runtime(compiled, *, date_window, only, skip, allow_pending, trace,
    settings)` with `run(record) -> Result`, `enable_profile()` and `on_command(ir, form, ctx)`;
    `Result` (`record`, `input`, `outputs`, `trace`, `printed`, `text`, `warnings`, `error`,
    `to_dict()`); `Variant` (`form`, `label`, `dialect`, `vars`, `text`, `approximate`);
    `Script`, `load(path, *, allow_pending=False, **options)`, `loads(text, filename, ...)`;
    `format_trace(step, orth)`.
  - `yasc.lexicon`: `Record`, `LexiconError`, `read_records(lines, fmt, source)`,
    `read_file(path, fmt)`, `word_records(words)`, `parse_features(text)`, `parse_format(fmt)`.
  - `yasc.cli`: new options `--profile`, `--allow-pending` and `--wide` (P9: refused);
    `main(argv) -> int` is unchanged in shape (entry 9). Exit status 1 is new: load errors.

- *2026-09-12, P7.* New module `yasc/syllable.py`: `Syllable`, `SyllableTier`, `SylView`,
  `Syllabifier`, `declare_role_features`, `view_kinds`, `ROLE_FEATURES` and the role constants
  `ONSET`, `NUCLEUS`, `CODA` (details in §13, entries 113–127). Changes to earlier modules:
  - `yasc.features`: a third scope `ROLE = "role"` in `SCOPES`, for the role
    pseudo-features; `FeatureSystem.canonical()` omits them.
  - `yasc.form`:
    - `Form.view(i)` returns a `SylView` when the form has a syllable tier;
    - `gap_marks` adds `Mark.SYLLABLE` at every syllable edge;
    - `Form.with_syllables(tier, pending_syllable_marks=())` is new;
    - the syllable hook is called as `after_replace(i, j, k, index_map, segs=..., gaps=...)`
      when the tier object has `takes_frame = True`. Other hook objects, P8 tiers included,
      keep the four-argument call of entry 52.
  - `yasc.ir.SyllabificationDef` gains `letters`: each canon letter mapped to its specs.
  - `yasc.compile`:
    - `Scope(Syllable)`, `SyllableMark`, `Syllabification`, `/:$` and `!syllabify` no longer
      go through the phase gate;
    - every Phonology gets the role pseudo-features before sealing;
    - `_canon_letters` is new.
  - `yasc.rules`:
    - `ApplyContext` gains `syllabifier`, `warnings` and `warn(message)`;
    - `_Piece` gains `sylw`;
    - `BasicRule.rewrite(form, piece, ctx=None)` takes an optional context, and so do the
      mode helpers;
    - `resyllabify(form, ctx, rule=None)` is new;
    - `BasicRule._syl_split(spec)` is new.
  - `yasc.runtime`:
    - `Runtime.syllabifier(definition)`, `Runtime.initial_syllabification` and
      `_cmd_syllabify` are new;
    - `!use $S` selects the active syllabification;
    - the warnings of `ApplyContext` reach `Result.warnings`.
  - `yasc.orthography.render_ex` writes each syllable's `SyllableMark` before its first
    segment.

- *2026-09-12, P8.* Autosegmental tiers (details in §13, entries 128–149):
  - **New module `yasc/tiers.py`:**
    - the tier: `Auto`, `AutoTier`, `TierCrossing`;
    - views and pattern payloads: `TierView`, `TierX`, `LinkSpec`, `FloatPred`;
    - RHS operations: `TierOp`, `OP_KINDS`;
    - level helpers: `levels`, `decompose`, `ncc_ok`;
    - rule-engine hooks: `rewrite`, `after_rule`, `apply_tier_rule`, `project`,
      `unproject`;
    - commands: `associate`, `command`;
    - compiler helpers: `resolve_tier`, `check_x`, `check_element`, `compile_rhs`,
      `tier_rule_syntax`, `tier_mods`, `segments_with_tiers`;
    - orthography helpers: `tier_features`, `build_tiers`, `floating_by_gap`,
      `strip_tier_values`.
  - **`AutoTier` methods:**
    - queries: `value`, `linked`, `segs_of`, `floating`, `is_floating`, `auto`, `order`;
    - operations: `write`, `link_new`, `link`, `delink`, `insert_floating`, `move_floating`,
      `delete`, `relabel`;
    - upkeep: `after_replace` (the P3 four-argument hook), `remap`, `ocp`.
  - `yasc.form`:
    - `Form.view(i)` returns a `TierView` when the form has autosegmental tiers;
    - `Form.tier(name=None)`, `Form.tier_by_index(idx)` and `Form.with_tiers(tiers)` are new;
    - construction checks that each tier's `n` matches the form.
  - `yasc.nfa`: a new edge kind `FLOAT = 7`, whose payload is a `FloatPred`; `S^X` compiles
    to a SEG edge whose payload is a `LinkSpec`.
  - `yasc.matcher`: FLOAT closure ops branch through `_ops_envs`; `_push` is factored out of
    `_add`.
  - `yasc.orthography`:
    - `parse` returns forms with tiers when the phonology declares tier features, and reads
      `FloatingPrefix` text;
    - `render`/`render_ex` write tones through the segment view, and write floating
      autosegments.
  - `yasc.compile`:
    - the P8 gate is gone: `_MOD_PHASE` and `_CMD_PHASE` are empty, `Tier(...)` compiles,
      and the P8 `IRPlaceholder` path in `_basic` was removed;
    - `_leaves` compiles `Linked`/`AutoFloat`, and `_rhs` compiles the tier forms;
    - `/:T` rules are rewritten by `tiers.tier_rule_syntax`/`tier_mods`;
    - `_ortho_segments` keeps tier values.
  - `yasc.ir`: an `IRRhsItem` of kind `tier` now has `pattern` (a `TierOp`) and
    `alternatives=((segment item,),)` (empty for a floating element).
  - `yasc.rules`:
    - `_Piece` gains `env`, passed by `piece()`;
    - `_expand` keeps `tier` items and drops floating ones, and `_modify`/`_insert` delegate
      a tier item to its segment part;
    - `rewrite` hands forms with tiers to `tiers.rewrite` (except for `/:T` rules);
    - `apply` sends `/:T` rules to `tiers.apply_tier_rule`, and `run` calls
      `tiers.after_rule` after a change.
  - `yasc.runtime`: `Runtime._cmd_associate` and `Runtime._cmd_ocp` are new.
  - `yasc.parser`: `_check_rhs_node` accepts a bare `(name)` on the RHS (for `/:T` rules).
  - `yasc.variants` (P9, after it finished):
    - `_shift` also shifts `env`;
    - `_run` calls `tiers.after_rule`;
    - `_apply_in` and `_body` send `/:T` rules to `tiers.apply_tier_rule(rule, form, ctx,
      window)`.
  - `yasc.paradigm` (P9, after it finished): `_Builder` records `parts`, and cell forms get
    `tiers.concat_tiers(parts, n)`.
  - `yasc.tiers` additions for P9:
    - `shift_env`, `concat_tiers` and `_window_base`;
    - `project(form, tier, autos=None)` and `unproject(tier, proj, selected=None,
      bounds=None)`;
    - `apply_tier_rule(rule, form, ctx, window=None)`.
- *2026-09-12, P9.* Advanced rule features (details in §13, entries 150–162):
  - `yasc/variants.py` (new): `DomainView`, `Explorer`, `choose`, `explore`, `join_label`,
    `rule_token`, `names_ok`, `draw`, `innermost`, `constraint_violations`,
    `constraints_ok`, `apply_basic`, `apply_group` and `run_cyclic`.
  - `yasc/paradigm.py` (new): `cell_ok`, `applicable_cells`, `build_cell` and `concatenate`.
  - `yasc.rules`:
    - `ApplyContext` gains `domain`, `explorer`, `constraints`, `p9` and `p9_bypass`;
    - `BasicRule.p9` and `Group.p9` are new;
    - `BasicRule.apply` and `Group.apply` hand P9 rules to `yasc.variants` after the date,
      pending, name and lexical checks.
  - `yasc.compile`:
    - `_MOD_PHASE` and `_CMD_PHASE` no longer list P9 constructs;
    - `Constraint *` and `Paradigm` compile without the gate;
    - the `/:C*` one-rule group has `pending=()`.
  - `yasc.lexicon`: `Record.dialects` is new.
  - `yasc.runtime`:
    - `Runtime(..., dialect=None, paradigm=None)`, and `Runtime.dialect_names`;
    - `Variant.rng_state`, `Result.wide(columns)`, `merge_variants` and
      `declared_dialects`;
    - `Script(..., dialect=, paradigm=)`, `Script.constraints`, `Script.violations` and
      `Script.well_formed`;
    - the commands `_cmd_dialects` and `_cmd_paradigm`; `!set EnforceConstraints` takes
      effect.
  - `yasc.cli`:
    - `--dialect` and `--wide` work, and `--paradigm NAME` is new;
    - exit 2 for `--wide` without `!dialects`, and for an undeclared `--dialect`.
- *2026-09-12, P10.* Tools, standard library and documentation (details in §13, entries
  163–173):
  - New files: `yasc/lib/ipa.yasc`; `yasc/tools/__init__.py`, `yasc/tools/minimize.py`
    (`minimize`, `Minimization`, `candidates`, `spec_item`, `load`, `phones_of`,
    `select_orthography`, `main`) and `yasc/tools/make_ortho.py` (`make_ortho`,
    `phonology_block`, `main`).
  - `yasc.compile`: `LIB_PREFIX` and `LIB_DIR`; `_include` resolves `lib:NAME` (entry 163).
  - `yasc.features`: `FeatureSystem.segment` also accepts a bundle string, through the new
    `_parse_bundle` (entry 170).
  - `yasc.cli`: only the `--allow-pending` help text changed.

- *2026-09-13 (after P10).* RHS `[x]` now compiles to kind `replace` (it was `merge`); the new
  syntax node `syntax.Merge` (RHS `+X`, canonical `+[x]`) compiles to kind `merge`;
  `Compiler._rhs(..., merge=False)`.

## 13. Decisions log
Design decisions made while writing the spec; implementers append to this list.

1. Boundaries live in gaps (spec A2). Considered: boundary items in the segment string with
   ε-skipping (the `ret.py` "meta" idea). Rejected, because it needs special cases for
   adjacent boundaries and for "ignored unless addressed".
2. Contexts are verified by anchored forward and backward matching instead of a product
   automaton (notes' option 3). It is simpler and exact with variable bindings; performance is
   adequate for short words.
3. Default mode is simultaneous (spec A3); `DefaultMode` keeps the notes' behaviour available.
4. Stress is a syllable-scope feature and tone is a tier feature. In both cases segments see a
   derived value, so ordinary feature rules keep working.
5. The notes' "lazy auto-features" are implemented as disjunction index correspondence
   (spec §8.2.4), not as generated hidden features. The behaviour is the same with nothing
   generated.
6. *(P0)* **Source positions and carets.** `SourceLoc.line` and `.col` are 1-based, and
   `col` counts code points, so a tab is one column. `end_col` is 1-based and exclusive. A
   caret may sit one column past the last character, for "unexpected end of line". In the
   excerpt, tabs are expanded to 8-column tab stops, combining marks have zero width and East
   Asian wide characters have width 2, so the carets line up under IPA diacritics. At least one
   caret is always drawn, and a span that runs past the line end is clipped. *Reason:* spec §12
   does not fix these conventions. Using code points keeps columns independent of the terminal.
7. *(P0)* **`YascLoadError` keeps every error.** `.errors` stores all errors in the order
   found, and nested load errors are flattened. `format()` shows the first 20, then
   `note: N more errors not shown`. An empty list is rejected. `str(err)` equals
   `err.format()` for every YASC error. *Reason:* spec §12 says "up to 20" are reported. Keeping
   the full list costs nothing, and the API can still reach all of them.
8. *(P0)* **`NotImplementedYet`** subclasses both `YascError` and the built-in
   `NotImplementedError`, so the compiler can collect it into a `YascLoadError` and generic
   handlers still catch it. It has an optional `phase` field (such as `"P8"`), which is
   printed as a note.
9. *(P0)* **CLI shape.** `yasc.cli.main(argv)` returns an exit status and never raises
   `SystemExit`. `--help` returns 0 and usage errors return 2. `--from` and `--to` are parsed
   as integers, and negative dates are accepted. `--only`, `--skip` and `--word` accept several
   values and may be repeated; the values are appended. These options are greedy, so they
   go after SCRIPT and the LEXICON files (`s.yasc a.tsv --only R1 R2`), or SCRIPT and the
   files go after `--` (`--only R1 -- s.yasc a.tsv`). A `--` placed between SCRIPT and the
   LEXICON files does not work with argparse. Until P6, any script argument prints a message
   to stderr and returns 2.
10. *(P1)* **Value representation.** Every feature value is a string:
    - `+` and `-` for Binary features and `!` for Unary ones;
    - decimal strings for Scalar values (`'2'`); the Python API also accepts `int`;
    - bare names for bracketed values (`[H]` is stored as `'H'`).

    Canonical printing puts brackets back on alphabetic values. Inside a segment, `None` means
    unspecified; as an operation result it means undefined (`_`). Value tokens (spec §4.1 (a))
    may not contain `_`, because `_` is the unspecified/undefined marker. An ad-hoc value list
    such as `High + -` has kind `enum`, so it has no built-in `(-)`; it can still share
    variables with Binary features, because its value set is the same. *Reason:* strings print
    and compare cheaply, and the design's `values: tuple[str, ...]` already implies them.
11. *(P1)* **Operations.**
    - Names are stored without their parentheses. `with_op("(-)", ...)` is also accepted.
    - A declared operation with the same name replaces the built-in.
    - Each result is a value of the type or `_`.
    - Composition tables (forward, and inverse in declaration order) are computed once for
      each operation tuple and cached on the type. An `OpVar` keeps its own reference to them,
      so matching never builds them.
    - Applying an operation to a value outside the type gives undefined (`None`).
12. *(P1)* **Nodes in specs.**
    - A Node accepts only `!N` (present), `_N`, `(p)N` and `(?p)N`. `{v..}N`, comparisons and
      operations on a Node are load-time errors.
    - `!N` is illegal as an output. A node becomes present only through its daughters, so
      there is no value to write.
    - `(p)N` requires the node to be present, like `(a)F`, which requires a specified F.
    - `(?p)N` binds `UNSPEC` when the node is absent; as an output, `UNSPEC` delinks the
      subtree.
    - A `BundleValue` covers every descendant leaf, with `None` for the unspecified ones, so
      applying it replaces the subtree exactly.
    - Writing a bundle to a node with different descendants raises `YascRuntimeError`.
13. *(P1)* **Variables at run time.**
    - An unbound variable in an output raises `YascRuntimeError`; P4 should reject it at load
      time.
    - A bound value that is not valid for the feature (spec §6.2 sharing rule) makes a match
      fail, but raises `YascRuntimeError` in an output. Silently writing an invalid value would
      corrupt the segment, and P4 can detect the problem statically.
    - `UNSPEC` used in `(a)F` or `op(a)F` as an output unsets F. In a match it never equals a
      value.
    - Operations on a weak variable (`op(?a)F`) are not supported. `<--(op)-->` over a spec
      containing `(?a)` is therefore a load-time error.
14. *(P1)* **Weak application** (`weak_apply`, `~~>`, `~{...}`):
    - a leaf is written only if it is unspecified;
    - a Node output (a bundle or `UNSPEC`) is written only if the node is absent;
    - `_F` does nothing, because a weak output never removes anything;
    - `strict` is ignored.
15. *(P1)* **Strict specs** (`'S`).
    - A match requires every segment-scope, non-tier leaf that the spec does not mention to be
      unspecified. Leaves under a Node that the spec mentions count as mentioned. Syllable-scope
      and tier features are ignored, because they are not the segment's own (spec §5.4 and
      §5.5), so `'[a]` still matches a stressed [a].
    - As an output, a strict spec starts from the empty bundle.
16. *(P1)* **Implication triggering and closure.**
    - A **strong** implication re-runs only when one of the features its trigger reads changes
      (for a Node: the node and its descendants). The features it writes do not re-run it.
      Otherwise `{+High} --> {-Low}` would undo a rule that lowers a high vowel. With
      `{+Low} --> {-High}` also declared, the rule's change wins and yields `-High +Low`.
    - A **weak** implication also re-runs when a target feature changes. This is always safe,
      because it can only fill gaps, and it makes `{S _F} --> {F-value}` and `S ~~> {F-value}`
      exactly equivalent under every changed-set, as spec §4.5 requires. A test checks this.
    - An empty trigger `{}` is always eligible.
    - When the trigger yields several environments (unbound op-variables), the first one is
      used.
    - Every variable in the target must be bound by the trigger. This is checked when the
      implication is added.
    - **Passes.** In each pass the implications run in declaration order. An implication is
      eligible if its trigger set intersects the features changed before the pass or earlier
      in the same pass. The loop ends after a pass that changes nothing. Pass
      `4 × |implications| + 1` raises `YascRuntimeError("implication cycle …")`, with the
      implications still firing given as the hint.
    - `close()` works on bare `Segment`s. Syllable-scope features in a trigger are read from
      the segment tuple, so P7 must decide whether implications may mention them.
17. *(P1)* **Bidirectional expansion.** `add_bidirectional(S, T, op=, forward_weak=,
    backward_weak=)` produces `S → T'` first, then `T → S'`. The right half of the arrow is
    `S → T` (`forward_weak` means `~~>`); the left half is `T → S` (`backward_weak` means
    `<~~`). `op` is prepended as the outermost operation of every variable constraint in
    each target, so `(a)` becomes `op(a)` and `o(a)` becomes `op#o(a)`. Constant constraints
    are copied unchanged. Both halves keep the arrow text in `origin`.
18. *(P1)* **`SegmentSpec.match` returns a tuple** of environments (see §12).
    - Variable-free constraints are tested before variable constraints.
    - Variable constraints then run in feature-index order.
    - Several unbound op-variables give the cartesian product of their preimages. It is
      ordered by feature index, then by value declaration order.
19. *(P1)* **Segments and views.**
    - Segments can be built only from a sealed system. Each system has its own
      `WeakValueDictionary` intern table.
    - `Segment` itself satisfies the `SegView` protocol (`segment`, `view[i]`, `get`,
      `present`). The raw `view[i]` is the only thing specs read.
    - A Form-backed view (P7/P8) must provide `.segment` and resolve syllable-scope and tier
      features in `__getitem__`. Specs that mention only plain features read
      `view.segment.values` directly (the fast path); `type(view) is Segment` is the fastest
      path.
    - Node slots in a segment's tuple are always `None`.
20. *(P1)* **Specs before sealing.** `SegmentSpec` and the constraints need only resolved
    `Feature` objects, so implications can be declared among the features, as in the syntax.
    Everything that depends on the geometry is computed on first use: node descendants,
    `is_plain`, strict coverage, and implication trigger sets (at `seal()`).
21. *(P1)* **Seal errors.** `seal()` collects all geometry errors: an unknown daughter (with a
    difflib hint), a feature listing itself as a daughter, a duplicate daughter, two parents,
    and cycles. A single error is raised as `YascDefinitionError`; several are raised together
    as `YascLoadError`. A system that fails to seal stays open. A Node with no leaf descendants
    is allowed, and is never present.
22. *(P1)* **Values for syllable-scope and tier features on a Segment.** A `Segment` may hold
    values for any feature, because design §11 uses segments both for syllable bundles and for
    projected tier forms. In P1, `apply` simply writes into the tuple. From P7/P8 the rule
    engine must route outputs to syllable-scope and tier features through the Form, not
    through `SegmentSpec.apply`.
23. *(P1)* **Miscellaneous spec semantics.**
    - Several constraints on one feature are allowed. They keep their written order (the sort
      by feature index is stable), and in an output the last one wins.
    - The bound in a comparison may lie outside the Scalar range; `>7Len` simply never
      matches.
    - `In` sorts its values into declaration order for canonical printing.
    - A bare Unary feature `F` is `Eq(F)`, whose default value is `!`.
24. *(P1)* **Feature names.** Names and aliases share one namespace. A clash is a
    `YascDefinitionError`. A lookup hint tries a case-insensitive exact match first, then
    `difflib.get_close_matches` with a cutoff of 0.6, and names the feature when the
    suggestion is an alias ("did you mean 'Hi' (alias of 'High')?").
25. *(P1)* **Scope and tier flags.**
    - `Scope(Syllable)` is `scope='syllable'`. `Tier(...)` is a frozen `TierDecl(tbu, stray,
      ocp)` placeholder, which validates `stray` and `ocp`; `tbu` stays uninterpreted until
      P8.
    - A Node may not be a tier feature. Mixed scopes inside one Node subtree are not rejected
      in P1.
26. *(P2)* **AST nodes are frozen dataclasses, not slotted.** `loc` is a field with `compare=False`.
   *Reason:* `dataclass(slots=True)` needs Python 3.10, and the AST is not on a hot path.
   The NFA and matcher are, and they use tuples and `__slots__`.
27. *(P2)* **`Spec` and `Ortho` compare by canonical text** (and feature-system identity).
   *Reason:* `SegmentSpec` has no value equality. Spec §11.3 requires a parsed `canonical()`
   to round-trip to an *equal* object, and P4 will test that.
28. *(P2)* **`Ortho(specs, text=None)`.** `canonical()` prints `[text]` (or `'[text]` when all specs
   are strict). Without `text` it prints the specs as a sequence, which means the same thing.
   *Reason:* rendering graphemes needs the orthography, and the AST must not depend on it.
29. *(P2)* **`Macro(name, refine=None)`** carries the `Name:{...}` refinement of spec §7, and
   `canonical()` prints `Name:<refine>`. *Reason:* the compiler (P4) needs the refinement
   before expansion.
30. *(P2)* **`Alt(id, items)`: `id` is an `int` or `None`.** `None` records nothing and compiles
   to plain ε-edges. *Reason:* Env keys of one kind must be mutually sortable (P1
   decision 18), and unnumbered disjunctions (for example in syllable templates) need no
   index.
31. *(P2)* **Nested `Seq` prints flat; `Capture` prints its inner pattern.** Star and Plus
   parenthesise anything except Spec, single Ortho, unrefined Macro, Alt and BackRef.
   *Reason:* `(P)` is Optional in the surface syntax, so a nested sequence cannot be
   bracketed. Captures have no surface syntax (spec §6.4).
32. *(P2)* **`Linked` / `AutoFloat` canonical form:** `S^[Tier.]X['][=name]`. *Reason:* spec §6.5
   does not fix where `'` and `=name` go when both appear. P8 may revise this.
33. *(P2)* **LHS numbering (`number_lhs`):**
   - Every top-level item except `Boundary`, `BracketAssert` and `Nothing` is numbered.
   - A macro that expands to a sequence is one item.
   - The whole LHS is also wrapped in `Capture(0, …)` (`whole=True`), so `$0` works in
     contexts.
   - It is an error for `$n` inside the LHS to refer to itself, to a later item, or to `$0`.

   *Reason:* spec §6.4 lists only consuming constructs as items. Zero-width items match no
   segments, so `$n` of them is meaningless. Under forward matching a forward or self
   reference can never match, so it should be a load-time error rather than a rule that
   silently never fires.
34. *(P2)* **`split_at_locus`** requires the locus to be a direct top-level item, exactly once. It
   returns `Nothing` for an empty side, the bare item for a one-item side, and otherwise a
   `Seq`. `C` is returned in forward order; reversal happens in the NFA.
35. *(P2)* **`first_specs`** sees through zero-width assertions and captures, and takes unions
    through nullable prefixes (`(C) V` → `{C, V}`) and alternatives. It returns `None` if the
    pattern is nullable or may start with `...`, `$n`, a macro or a P8 element. It returns a
    tuple in textual order, deduplicated by identity. `compile_pattern` stores it as
    `nfa.first_specs`. *Reason:* assertions add conditions but consume nothing, so the filter
    stays sound. A tuple gives deterministic iteration.
36. *(P2)* **ε-closure cycle cutting.** A state is never re-entered while it is on the current DFS
    path, and it is expanded at most once per distinct `ops` tuple. Results are deduplicated
    by `(target, ops)`. **This deviates from the literal "each state at most once per
    closure".** That rule is wrong when two paths reach the same state with different ops:
    for `(#) V`, the path that skips the assertion would be dropped. Without ops the two
    rules coincide. Re-entering a zero-width cycle only repeats zero-width actions, so
    nothing is lost.
37. *(P2)* **Reversal keeps preference order.** The builder keeps each state's in-edges in
    "reversed preference" order: loop back-edges are inserted first, and alternatives stay
    textual. The reversed NFA's out-edges are those in-edges. So the backward matcher is
    greedy and keeps alternatives in textual order. A test checks that its results match a
    forward NFA of the mirrored pattern on the mirrored form, in the same order.
    `nfa.reversed()` is cached, and `rev.reversed() is nfa`.
38. *(P2)* **Bracket assertions are not swapped by reversal.** `<:N` still means "an N bracket
    opens at this gap" when matched backward, because the form is not mirrored and gaps are
    symmetric (design §5.2). Only mirroring the *form* would turn an opening into a closing.
39. *(P2)* **Captures in the Env.** `CAP_OPEN` stores a pending `(g, None)`, and `CAP_CLOSE`
    completes it to `(min, max)`. In both directions the capture seen first in matching order
    is `CAP_OPEN`, because reversal swaps them. Spans are therefore always `i <= j` in form
    coordinates. *Reason:* no special case for direction, and dedup by `(state, env)` stays
    exact.
40. *(P2)* **Back-references.**
    - Equality is `Segment` equality (interned identity first), not view equality.
    - Under visibility, `$n` compares the **visible** segments of the captured span with the
      next visible segments.
    - A capture that is unset or still pending makes `$n` fail.
    - A zero-length capture makes `$n` an ε-move, followed at once during closure expansion.
    - A multi-segment `$n` is matched one segment per step, as a thread
      `((target, n, k), env)`.

    *Reason:* matching one segment per step keeps the Pike-VM preference order exact, where a
    jump queue would not. Spec §8.5 says invisible segments are skipped like gaps, so a copy
    is judged on what the rule can see.
41. *(P2)* **Positions under visibility.** Every position is a real gap. After a segment is
    consumed, the position is the gap adjacent to it, so end gaps are tight. An assertion at
    real gap `g` tests the virtual gap `prv[g]+1 .. nxt[g]`, taking the union of the marks
    and bracket edges of those gaps. `find_all` tries one start gap per virtual gap: the real
    gap just before the next visible segment, or `n`. A zero-width match at a virtual gap is
    therefore reported at its right end. *Reason:* each match is found once, and foci never
    begin or end with invisible material. P5 may pick another insertion point for epenthesis
    if needed.
42. *(P2)* **`find_all` never uses the reversed NFA.** With `direction=-1` it only enumerates start
    gaps `n..0` (as instructed). The pre-filter is off by default: pass
    `first_specs=nfa.first_specs`. *Reason:* keeps the given signature, and `None` already
    means "cannot pre-filter".
43. *(P2)* **The spec-match cache lives on the NFA** (`nfa.match_cache`, shared with the reversed
    NFA). It maps a spec to a dict from `Segment` to `bool`, and holds only specs that are
    variable-free **and plain** (`spec.is_plain()`). Keys are `Segment` objects, not
    `id()`s. `nfa.clear_cache()` empties it. *Reason:*
    - non-plain specs read syllable and tier features through `form.view(k)`, which depend on
      the form and not only on the segment;
    - a stale `id()` of a collected interned segment could be reused;
    - a per-NFA cache needs no global state (design §1).
44. *(P2)* **Result dedup.** `match_anchored` returns every `(end, env)` pair. `match_context`
    yields each distinct Env once: it deduplicates D's Envs (D's open right end repeats them),
    then the final Envs, in D-major order. It is lazy, so a negative context can stop at the
    first result.
45. *(P2)* **Specs that read syllable or tier features** get `form.view(k)`. All other specs get
    `form.seg(k)` (the fastest path of `SegmentSpec.match`).
46. *(P2)* **`Anything` is one state** with a `SEG(None)` self-loop, and `None` means "any segment".
    No spec is called for it.
47. *(P3)* **`Form` is a frozen dataclass.** Its fields are `segs`, `gaps` (the *stored* marks),
   `brackets`, `syllables` (`None`), `tiers` (`()`) and `pending_syllable_marks`, plus a
   private cache of effective gap marks that takes no part in comparisons. Equality and
   hashing are by value. `gap_marks(g)` returns the stored marks plus `PHRASE` at gaps 0
   and n. An explicit `PHRASE` or `WORD` stored at an edge is kept rather than normalised
   away, so `#pa` renders back as `#pa`. *Reason:* immutability (design §1), with O(1)
   `gap_marks` for the matcher.
48. *(P3)* **Bracket order and nesting.** `brackets` is in document order of the opening
   delimiters: construction does a stable sort on `open_gap` and then checks nesting with a
   stack. When one bracket *could* nest inside the open one (same span, or a zero-width
   bracket at the open one's end), it nests. So `<A:x><B:>` and `<A:x<B:>>` give the same
   tuple, which renders as the second form. Crossing spans raise `ValueError`.
   *Reason:* a tuple of `(label, open, close)` cannot tell those two texts apart. The rule
   is applied the same way in validation, `bracket_events` (rendering) and parsing, so
   `parse(render(f)) == f` holds.
49. *(P3)* **Brackets are never dropped by `replace`.** A bracket whose content is deleted stays as
   a zero-width span `(i, i)`. Only `erase_brackets` removes brackets. *Reason:* spec
   §8.2.4 says only that brackets are "adjusted"; keeping them preserves the morphological
   structure that `/:C*` (P9) needs.
50. *(P3)* **The gap map of `replace(i, j, new)`.** Let k = len(new). Gaps before i are unchanged,
   and gaps after j shift by k-(j-i). Interior gaps and gap i go to i, and gap j goes to
   i+k. When k = 0 everything collapses into gap i, so the marks are unioned. Marks,
   bracket ends and pending syllable marks all use this one map. For a pure insertion
   (i == j), the keyword `attach` chooses the side. `"left"`, the default, means the new
   material joins the left neighbour: whatever is stored at gap i moves to i+k, so
   `C ___ #` epenthesis gives `C e #`. `"right"` keeps it at i, which is what `# ___ C`
   prothesis needs. *Reason:* one monotone map keeps nesting valid. Interior bracket ends
   must go to the same point for both open and close, or disjoint brackets would cross. P5
   has to pick `attach` from the rule, for example `right` when the context has a boundary
   immediately left of the locus.
51. *(P3)* **Index map.** `replace_with_map` returns `(form, index_map)`. Replaced segments are
   aligned by position (`m_q -> r_q` for q < k, and deleted otherwise), unless
   `align=[offset or None, ...]` is given. P5 should pass `align` for `$n` spans and
   metathesis. *Reason:* this matches the positional RHS alignment of spec §8.2.4 while
   letting P5 express anything else.
52. *(P3)* **Hooks.** `replace` calls `syllables.after_replace(i, j, k, index_map)` and
   `tier.after_replace(i, j, k, index_map)` whenever those objects exist, and stores what
   they return. *Reason:* P7 and P8 plug in without changing `form.py`.
53. *(P3)* **The unit grammar and merge order.** A unit is
   `prefix* (circumfix-open)? grapheme (postfix | circumfix-close)*`, with at most one
   circumfix, and a circumfix must be closed. Merging runs innermost first: the postfixes
   written inside the circumfix, then the circumfix, then the postfixes after it, then the
   prefixes from the nearest outwards. So postfixes bind tighter than prefixes (`*t_0` with
   `*`=+Voice and `_0`=−Voice is voiced). Diacritics are applied as output specs
   (`SegmentSpec.apply`), so `_F` in a diacritic unsets F. After merging, `fs.close_new`
   applies the implications. *Reason:* spec §5.6 fixes "diacritic wins" but not the
   relative order of prefixes and postfixes.
54. *(P3)* **One DP over the whole text, not a pre-split.** Separators, syllable marks, bracket
   delimiters, whitespace runs and unknown characters are transitions of the same
   dynamic program as grapheme units. Every token counts as one: a grapheme with all its
   diacritics is a single token. The score is `(unparsed chars, tokens, per-token keys)`,
   compared lexicographically. A unit's key is
   `(-len(grapheme), declaration order, number of diacritics, merge order)`, and every
   non-unit key sorts after every unit key. Graphemes and diacritics therefore beat
   delimiters on the same text (`p_>`, `b_<`, `_<`), a multi-character separator beats
   repeated short ones (`##`), and among tokenisations with equally few tokens the one with
   the longest first grapheme wins (`ts|h` beats `t|sh`). Leading and trailing whitespace
   is ignored. **Deviation from design §9**, which splits separators off first. The task
   requires graphemes to win over delimiters when both match, and a pre-split cannot do
   that.
55. *(P3)* **`OnUnparsable = keep` uses `OpaqueSegment`**, a `Segment` subclass defined in
   `orthography.py`. All its real features are unspecified. It keeps the character text,
   equals only opaque segments with the same text, answers `get("Unknown") == "!"` and
   renders verbatim. `is_unknown(seg)` tests for it. There is no `Unknown` feature in the
   system: specs mentioning only real features treat it as a fully unspecified segment,
   and a rule that writes to it produces an ordinary segment. *Reason:* this is the
   smallest workable form of the spec's pseudo-feature, and it needs no change to
   `features.py`.
56. *(P3)* **Unparsable input raises `UnparsableError`**, a subclass of `YascRuntimeError`
    defined in `orthography.py`. The whole run of consecutive unparsable characters is
    underlined, and `.text`, `.offset` and `.end_offset` are available. With `loc` (the
    location of `text[0]` in a script) and `source_line`, the error is located in the
    script. Otherwise the input text itself is the excerpt, with file `<input>`. Unmatched
    and unclosed brackets raise the same error. *Reason:* lexicon input is data, so this is
    a runtime error; P4 can re-raise it as `YascSyntaxError` for `[...]` strings in a
    script, keeping the same `loc`.
57. *(P3)* **Separators.** The defaults are syllable `.`, morpheme `+`, clitic `=`, word `#`,
    phrase `##`, and no phone separator. Whitespace always separates words, whatever the
    word separator is. `set_separator(kind, None)` disables a separator. When rendering, a
    word mark is written as a space inside the form and as the word separator at the edges,
    because edge whitespace is stripped when parsing. Several marks in one gap are written
    strongest first. Marks go after the bracket closes and before the opens at the same
    gap, and syllable marks come last. A syllable mark replaces the `.` of its gap: parsing
    one also stores `SYLLABLE`.
58. *(P3)* **The phone separator in output** is inserted only between adjacent segments whose
    texts would otherwise tokenise differently (for example `t|s` when `ts` is a grapheme).
    Without a phone separator, such forms cannot round-trip. Separators that are also
    written as diacritics (X-SAMPA `=`) also break round trips. That is a property of the
    orthography, not a bug. *Reason:* round trips when possible, with no clutter.
59. *(P3)* **Rendering.** The steps are:
    - an exact lookup of the closed bundle with the ignored features projected out; if
      several graphemes match, the first declared wins;
    - otherwise the k (default 3) nearest bases by `(distance, declaration order)`;
    - for each base, a greedy loop adds whichever diacritic lowers the distance most
      (ties go to declaration order), allowing at most one circumfix. Each new diacritic
      is tried as the outermost and then as the innermost one, so `(t)_h` (a circumfix
      that unsets Asp, then `_h` outside it) can be found;
    - the candidates are ranked by `(distance, number of diacritics, base order,
      diacritics)`.

    Each candidate's segment is computed with exactly the parse merge order for its written
    arrangement. The diacritics are then put in declaration order if that gives the same
    segment; otherwise the order in which they were added is kept. The first candidate
    whose text re-tokenises to that same segment is chosen. Ignoring a Node ignores its
    descendants. The residual is `((feature, segment value, rendered value), ...)`.
    `render_ex(form)` returns `(text, [(index, residual), ...])`. *Reason:* each rendered
    text re-parses to the value that was evaluated, so several postfixes always come out in
    an order that re-parses correctly, even when they conflict.
60. *(P3)* **Not implemented in P3:** `Escape == [\]`. `set_setting("Escape", ...)` raises
    `NotImplementedYet`. Floating autosegment rendering is also left out: `floating_prefix`
    is stored only (P8).
61. *(P3)* **Bracket labels** in input are identifiers (`[A-Za-z_][A-Za-z0-9_]*`) between the open
    delimiter and the label end. Input has no labelled close. *Reason:* this matches the
    `<:N` and `>:N` pattern assertions.
62. *(P3)* **Syllable-mark features** are stored as a `Segment` built with `fs.segment` (with no
    implications). `pending_syllable_marks` holds sorted `(gap, Segment)` pairs, which P7
    consumes.
63. *(P4)* **Logical lines.**
    - Comment lines (`#` + whitespace, or `#` alone) are dropped before `/\` joining, so a
      continuation skips over them.
    - `[...]` and `"..."` protect `%%`, `/\`, `[[` and `]]` only within one physical line.
    - `"` right after `/` is the `/"` modifier, not a string.
    - A `]]` after other text starts a new logical line, so `A --> B ]] /:1` is a rule
      followed by a closing line. A line containing `[[` "opens" a block; the parser uses
      that for depth counting during recovery.
    - A joined line gets one space in place of the discarded `/\...` text.

    *Reason:* spec §3.2–§3.3. This keeps block structure visible without full tokenizing.
64. *(P4)* **The locus is one or more underscores outside `{}`.** Spec §6.1 says "two or
    more"; the notes and the example also use `#_` and `C __ D`. In patterns an identifier
    may contain single underscores only between alphanumerics, so `V___` lexes as `V ___`
    and `C_x` is one name. Inside `{}`, `_F` stays "unspecified". *Reason:* unambiguous, and it
    accepts every form in the notes.
65. *(P4)* **Postfix operators and groups.**
    - `(P)` and `(P)?` are Opt; `(P)*` and `(P)+` are Star and Plus of `P` (not of `Opt(P)`).
    - `P*`, `P+` and `P?` apply to the atom just before them, and only one postfix is
      allowed per atom.
    - `'` must be written immediately before `{`, `[` or `$n`.
    - `'$n` means `$n` (spec §6.4).
    - `S1:S2` chains to the left. `Name:S` is `Macro(name, refine=S)`; anything else is
      `Combine`.
66. *(P4)* **The syntax AST reuses the P2 composite nodes.** Leaves that need a feature
    system or an orthography are separate: `RawSpec`, `RawOrtho`, `Combine`, and `LinkOp`
    for the RHS `^+`/`^-` forms. Compilation replaces them. Because P2's `_atomic` does not
    know about them, a star over a raw spec prints `({+Syll})*`. That still round-trips;
    compiled patterns print `{+Syll}*`. *Reason:* no changes to frozen P2 modules.
67. *(P4)* **Modifiers are normalised when the node is built.** `BasicRule`, `Group`,
    `RulesSection` and `Cell` sort their modifiers by `syntax.MOD_ORDER`. Modifiers with the
    same key keep their written order, which matters for contexts because bindings thread
    through them in order. `/~` is stored as `/:~`. The name after `/"` is
    `[A-Za-z_][A-Za-z0-9_.]*`. `/%n` accepts decimals between 0 and 100. *Reason:* spec §8.7 says
    order is free, and §11.3 needs equality after a canonical round trip.
68. *(P4)* **Bidirectional arrows** are read as `<L(op)?R?>` with L, R in {`--`, `~~`}. When R
    is missing it equals L, so `<-->`, `<~~>` and the notes' `<---->` are all accepted.
    Canonical printing writes `<-->`, `<--(op)-->`, `<~~-->` and so on.
69. *(P4)* **Feature lines.**
    - `Labial !` is Unary.
    - `== aliases` may also end the feature line itself (the notes write `Labial Unary ==
      labial lab`).
    - Alias and `(op)` lines attach to the most recently declared feature, even when an
      implication line comes between.
    - `Scope(Segment)` is accepted.
    - `Tier(...)` takes `TBU=`, `Stray=` and `OCP=` in any order.
    - `...` in a value, operation or daughter list is a syntax error with the A9 hint.
    - All features are declared before implications and TBU specs are compiled, so those
      may refer to features declared later in the section.
70. *(P4)* **Macros expand syntactically** (on the AST, before specs are resolved).
    - `[...]` inside a macro body is parsed with the orthography that is working where the
      macro is *used*.
    - All top-level macros are collected before compilation, so a global macro is visible
      everywhere, including above its definition (spec §7 "global"). A macro inside a Rules
      block or group is visible from its definition to the end of the block.
    - Names in a macro body resolve in the scope chain of its definition.
    - Two definitions in the same scope are an error. Shadowing an outer definition is a
      warning.
    - A refinement `Name:S` expands to `Combine(body, S)`. The body must be a single segment
      spec or a disjunction of them; a disjunction becomes a numbered `Alt`, so class
      correspondence works for `C:[p t k]`.
71. *(P4)* **The working orthography** is the orthography most recently selected by, in file
    order:
    - an `Orthography` section;
    - `!use $O`;
    - `!orthography input $A` (the input orthography wins, because spec §10.1 parses
      `[...]` "with the input orthography").

    A new `Phonology` drops a working orthography that belongs to another feature system.
    `[...]` without a usable orthography is an error. `!use` and `!orthography` also take
    effect at load time, and they are emitted as `IRCommand`s for the runtime.
72. *(P4)* **`[...]` in patterns and on the RHS.**
    - In a pattern, the boundary marks inside the parsed form are ignored; each segment
      becomes `SegmentSpec.from_segment` (strict for `'[x]`).
    - In `S1:[p t k]` a whitespace-separated list is a disjunction, and each item must
      parse to exactly one segment.
    - On the RHS, `[x]` is kind `merge` with `parse(close=False)` (declared features only),
      and `'[x]` is kind `replace` with the closed segment.
    - A multi-segment string gives one RHS item per segment.
    - An `UnparsableError` becomes a `YascSyntaxError` located in the script.
73. *(P4)* **Class-correspondence alignment.** RHS item *p* (0-based, as written) is aligned
    with top-level LHS item *p*+1 (the `$n` numbering), not with segment *p*. The LHS item
    must itself be a disjunction (directly, through a macro, or through `S:[p t k]`), with
    the same number of alternatives. The IR records the LHS disjunction's id (`alt`) and
    one tuple of RHS items per alternative.
74. *(P4)* **Static checks.**
    - An RHS variable must be bound by the LHS, a positive context or a positive input
      filter. Negative contexts discard their bindings, and output filters run after the
      RHS, so neither binds.
    - The variable-sharing check (`FeatureType.shares_values_with`) covers every spec of the
      rule: LHS, all contexts and filters, and the RHS.
    - `$n` on the RHS and in contexts and filters must satisfy n ≤ the number of LHS items.
      `number_lhs` enforces the in-LHS rule.
    - RHS specs must pass `for_output`.
75. *(P4)* **Modifier categories and inheritance.** Every key has a category
    (`compile.MOD_CATEGORY`).
    - Contexts and filters always accumulate.
    - `/:L±`, `/:D±` and `/:C±` may appear several times on one rule, but a member's own
      modifier of a category overrides the inherited ones.
    - Every other category holds one modifier. A second one is an error ("given twice", or
      "conflict" for `/:1` with `/*`, `/:>` with `/:<`, and `/:F+` with `/:F-`).
    - Inheritance passes through nested groups; a nested group's own leading modifiers
      override. Invocations do not inherit.
    - `/"` after `[[` is an error, because a group is named after `]]`.
    - After `]]`, the spec §8.8 list plus `/::` is allowed. Group filters may not use `___`
      or `$n`.
76. *(P4)* **Dates.**
    - `!date n` sets the date for the following rules in file order, across sections.
    - `!date`, `/:@n` on rules and `/:@n` after `]]` are checked together for
      non-decreasing order, as they are met in the file. A group's trailing `/:@` is
      therefore checked after its members.
    - `!date` is load-time only and is not emitted. Undated rules have `date=None`.
77. *(P4)* **Constructs pending a later phase.**

    | Phase | Constructs |
    |---|---|
    | P7 | `Scope(Syllable)`, `SyllableMark`, `Syllabification`, `/:$`, `!syllabify` |
    | P8 | `Tier(...)`, `S^X`, `^X`, RHS link forms, `/:T`, `!associate`, `!ocp` |
    | P9 | `/:C±`, `/:C*`, `/%`, `/???`, `/:D±`, `Paradigm`, `!paradigm`, `!dialects`, `Constraint *` |

    With `allow_unimplemented=False` each one records `NotImplementedYet(phase=...)`. With
    `True` it records a `YascWarning(phase=...)`, and:
    - rules whose patterns contain tier elements, and `/:T` rules, become
      `IRPlaceholder`s (keeping the rule number, name and date);
    - rules and groups with P7/P9 modifiers compile fully, with the modifier records
      filled in, and list the phase in `pending`;
    - pending commands become `IRCommand(pending=...)`;
    - Syllabification and Paradigm sections become `SyllabificationDef` / `ParadigmDef`.

    Features refused under P7/P8 are still declared, so other lines do not cascade into
    "unknown feature" errors. The orthography's own `Escape` `NotImplementedYet` (phase
    unset) is handled the same way. *Reason:* the plan says P4 must accept everything.
    Keeping full records lets P7–P9 flip a gate without recompiling differently.
78. *(P4)* **`/:C*` on a single rule** compiles to `IRGroup("seq", (rule,), cyclic=True)`
    (spec §8.6 "wraps the rule in a one-rule group"). The rule itself also has
    `cyclic=True`.
79. *(P4)* **`!include`.**
    - The path is relative to the directory of the including file (the current directory
      for `<string>`).
    - Each real path is included at most once, and the main file counts as included.
    - At top level the included statements are compiled in place, and its macros become
      global when the include is reached.
    - Inside Rules, the included file may contain only rule items, which join the
      enclosing block.
    - `!include` is load-time only.
80. *(P4)* **`$Name` invocation** resolves at compile time to a Rules section, group or rule
    named before that point (by `$Name :=` or `/" Name`). An unknown name is an error; a
    name that is some other definition gets a hint.
81. *(P4)* **RHS items.**
    - `0` must be the whole RHS.
    - `~` applies only to a spec, or to a macro that expands to one.
    - Boundaries, `...`, repetition and bracket assertions are not allowed on the RHS.
    - A macro that expands to a sequence contributes several items, and one that expands to
      `0` contributes none.
82. *(P4)* **Filters and contexts.**
    - A context has exactly one top-level `___`; a filter has at most one.
    - A filter with `___` is anchored like a context (`IRFilter.left`/`right`).
    - A filter without `___` is searched anywhere (`IRFilter.anywhere`).
    - `/:F±` takes a pattern that matches one segment: a spec, a single-segment `[x]`, or a
      disjunction of them. The IR stores it as a tuple of specs.
83. *(P4)* **Commands.**
    - `!set Name = value` keeps the rest of the line raw, so `regex:` patterns survive. The
      setting name and simple values are checked at load time.
    - `!orthography` takes `input $A` and/or `output $B`.
    - `!dialects` accepts `(A B)` or `A B`.
    - `!associate` accepts `dir=>|<`, `mode=one-to-one` and `spread=last|none`. `!ocp`
      accepts `merge` or `delete`.
    - `!print` directives are validated: known letters, an argument index below the
      argument count, and a known orthography for `%O[$X]`.
84. *(P4)* **Syllabification sections.**
    - Unknown keys and invalid words are syntax errors, and a key may appear only once.
    - `Onset`, `Nucleus` and `Coda` templates may use only specs, `[x]`, sequences,
      optionals, disjunctions and `0`. `*` and `+` are rejected too, following the spec's
      list.
    - `Canons` is a `>`-separated list of letter strings.
    - Settings keep only what is written; P7 supplies the defaults.
85. *(P4)* **Error recovery and reporting.**
    - After an error the parser skips to the next logical line. If the bad line opened a
      block, it skips to the matching `]]`.
    - An error in the trailing modifiers of a `]]` line does not lose the block.
    - Syntax errors stop the load before compilation.
    - Compile errors are all collected, deduplicated by (class, location, message), and get
      their source lines filled in from the file they came from (included files too).
86. *(P4)* **Rule numbering.** `IRBasicRule.id` counts basic rules from 1 in file order,
    including rules reached through `!include`. Placeholders use up a number too. Invoking
    a rule block does not renumber it.
87. *(P5)* **Executable rules are built from the IR.** `rules.Builder` turns each IR record into
    a `Rule` object once, memoised by record identity; `ApplyContext.builder` holds the memo.
    Building precomputes the quick-reject specs, the insertion `attach` and the pending phase,
    and the IR stays on `rule.ir`. *Reason:* the IR is data-only (§12, P4), and interpreting it
    directly would repeat that work for every record.
88. *(P5)* **A focus is rewritten position by position.**
    - RHS item q acts on the q-th *visible* LHS segment. Excess RHS items join the output of
      the last visible LHS segment. Invisible segments inside the focus stay where they are
      (spec §8.5).
    - The engine calls `Form.replace(p, p+1, out, align=(a,))` for each changed position,
      right to left, rather than one `replace(i, j, new)`. `a` is the offset of the segment
      itself in `out`, or `None` for a deletion or a `$n` copy of another position.
    - As a result, a boundary between two surviving positions keeps its place
      (`V C V --> $3 $2 $1` turns `pe+ta` into `pa+te`). A deletion unions the gaps it merges,
      and brackets follow the same monotone map.
    - **This deviates from the P3 carry-over** (entry 51), which suggested one `replace` with
      `align` for `$n` spans. A single `replace` moves interior marks to the left edge
      (entry 50), which breaks spec §8.2.4 "kept at their positions".
    - *Cost:* the index maps passed to the P7/P8 hooks are per position. A metathesis
      therefore reaches a hook as two replacements by copies, not as a swap. P8 may add a
      whole-span map if links must follow moved segments.
89. *(P5)* **`attach` for pure insertions** (`insertion_attach`). The engine uses `"right"` when
    some positive context's left side ends in a boundary assertion, possibly followed by items
    that can match nothing (`#___`, `#C*___`), and no positive context's right side starts
    with one. Otherwise it uses `"left"`. So `# ___ C` keeps the `#` before the prothetic vowel,
    and `C ___ #` keeps it after an epenthetic one. *Reason:* entry 50 leaves the choice to P5.
    A static rule is predictable, and the rare `# ___ #` case falls back to the default.
90. *(P5)* **A candidate that changes nothing is not applicable** in once and iterative modes,
    unless the rule is `/:~`: the next candidate is tried instead. In simultaneous mode such a
    candidate still occupies its span, so overlapping foci after it are dropped. *Reason:* spec
    §8.2 step 6 counts a focus as applied only when the form changes. This also makes `/:1` and
    `||` groups skip vacuous matches.
91. *(P5)* **Iterative (`/*`) progress.**
    - L→R: the next focus needs `i >= cursor`, where `cursor = i + len(rewritten span)`. It is
      `i + 1` for a zero-width focus rewritten to nothing, which only `/:~` can apply.
    - R→L: the next focus needs `j <= cursor`, where `cursor = i` (`i − 1` in the zero-width
      case).
    - Every application counts, and the `(MaxIterations+1)`-th raises `YascRuntimeError`.
    - Input filters and contexts are tested on the form each search sees, which is the
      updated form.
92. *(P5)* **Repeat (`/:*`).**
    - Each pass runs in `/:1` mode, or `/*` if that is also given.
    - The loop stops at the first pass that does not apply or does not change the form.
    - A form seen before raises a "cycles" error. More than `MaxIterations` passes raise a
      "did not converge" error.
    - Groups with `]] /:*` work the same way.
93. *(P5)* **Filters and visibility.**
    - Rule filters, like contexts, use the rule's `/:F±` mask. Output filters recompute the
      mask on the output form.
    - Positive filter bindings are threaded like contexts, with backtracking. Negative
      filters and negative contexts stop at their first match, and their bindings are
      discarded.
    - Group filters (`]] /:i± /:o±`) test the whole form without a mask. A group whose output
      filter fails is undone, and its trace entries are removed.
94. *(P5)* **Group results.**
    - SEQ is applied iff some member applied.
    - AND returns the original form with `applied=False` at the first member that does not
      apply, and drops the trace entries of the undone members. An empty AND succeeds.
    - OR stops at the first member that applies.
    - A group with `]] /:~` counts as applied whenever it runs, meaning its restrictions and
      input filters pass. A group has no single "match", and this makes an optional step
      usable inside `&&`.
    - `IRCommand` and `IRAssign` members neither succeed nor fail. They are handed to
      `ApplyContext.on_command` (the runtime, P6) and ignored without it. Pending ones follow
      `skip_pending`.
95. *(P5)* **Persistent rules** (spec §8.9).
    - A `/::` rule or group joins `ctx.persistent` after its first application and stays
      until the end of the enclosing group (`run_sequence`). Nested groups share the list.
    - After each member that changes the form, including the declaring application itself,
      `run_persistent` runs every active persistent rule in declaration order until a pass
      changes nothing. More than `MaxIterations` passes raise.
    - A re-entry guard stops persistent rules from triggering the loop themselves (item 4).
    - `apply_rule` also runs the loop after a changing application, so P6 can give top-level
      statements a scope.
96. *(P5)* **Lexical restrictions** (`/:L±`) are checked against `RecordData.lexical`, a mapping
    `{name: value}` in which Unary features are `"!"`. The constraints are untyped:
    - `vF` means equality;
    - bare `F` means `!F`;
    - `_F` means absent;
    - `{v..}F` means membership;
    - comparisons are numeric;
    - `(a)F` means "specified";
    - `(?a)F` means anything.

    `/:L+` needs every constraint to hold, and `/:L-` needs at least one to fail. *Reason:*
    lexical features are declared by the lexicon (P6), so their types are not known when the
    script is loaded.
97. *(P5)* **Pending constructs.** These raise `NotImplementedYet(phase=…)` when they are
    executed:
    - a rule or group with a non-empty `pending`;
    - an `IRPlaceholder`;
    - a pending `IRCommand`;
    - an RHS `tier` item.

    With `skip_pending` they are skipped instead: the result is `Outcome(form, False)`, a
    `TraceStep` gets a `note`, and an `(id, name, phase)` entry goes to `ctx.skipped`. Rules
    that read syllable-scope features (`Stress`) are *not* pending. Until P7 those values
    live in the segment tuple (entry 22).
98. *(P5)* **Trace.**
    - A basic rule adds one `TraceStep` per application that changes the form, or matches
      under `/:~`.
    - `before` and `after` cover the whole application. `foci` are the rewritten spans, each
      in the coordinates of the form it was found in, so under `/*` each application uses its
      own form.
    - Groups add no steps of their own. `depth` is the group nesting.
99. *(P5)* **Quick reject and caches** (§10).
    - `required_specs(lhs)` collects the specs every match consumes, through Seq, Capture,
      Plus, Spec and Ortho. Alternatives, optionals, stars, `...` and `$n` contribute nothing.
    - Only plain specs are used. The results for variable-free specs are cached in the LHS
      NFA's `match_cache`, which the matcher shares.
    - The pre-filter `first_specs` is passed to `find_all`.
    - *Measured* on CPython 3.10: 1,000 forms × 100 simple rules take about 1.5 s (engine
      only), so no further optimisation was needed.
100. *(P5)* **Names, dates and copies.**
    - `!skip` names skip a rule or a whole group.
    - With `!only`, a basic rule runs only if it, or an enclosing group, is named.
    - Rule dates are carried on `rule.date` / `rule.ir.date`. Filtering by date is P6.
    - An RHS `$n` copies the *visible* segments of the capture, consistent with entry 40. The
      copies get no implications, because they are complete segments.
101. *(P6)* **The date filter lives in `ApplyContext`** (`date_allows`), not in the builder.
    - `BasicRule`, `PendingRule` and dated `Group`s check it before running, and so do
      persistent rules when they re-run.
    - An undated member of a group with `]] /:@n` inherits the group's date through
      `ctx.group_date`. An undated group (a `Rules` section, for instance) is not filtered;
      its members are.
    - Commands and assignments are never filtered, so `$spell := $_` after `!date 1500` still
      runs for a record dated 1600.
    - The rules follow spec §8.10. A dated record admits only rules dated strictly later, and
      undated rules count as −∞. `--from A` admits dates ≥ A and excludes undated rules;
      `--to B` admits dates ≤ B and keeps undated rules.

    *Reason:* the record date changes from record to record. A builder-time filter would need
    one rule tree per distinct date, while the check costs one comparison per rule.
102. *(P6)* **Per-record state.**
    - `$_` is a list of `Variant`s, each with its own variable table. Every top-level statement
      runs on every variant, and so does `!print`. P9 forks variants by copying their table.
    - Each record gets a fresh `ApplyContext`: the load-time settings, and an RNG reseeded
      from `Seed`. So `!set` lasts until the end of the record, and results do not depend on
      record order (spec §12 determinism). One `Builder` is shared by all records.
103. *(P6)* **Orthographies at run time.**
    - A record starts with input = output = the most recently *defined* Orthography.
    - Leading top-level configuration commands (`!set`, `!use`, `!orthography`, `!only`,
      `!skip`) run *before* the record's form is parsed, so a top-level
      `!orthography input $Lat output $IPA` governs parsing. Later `!use` and `!orthography`
      re-select in statement order.
    - The compile-time working orthography (entry 71) is unchanged.
    - Edge case: an Orthography section defined *after* a top-level `!orthography` wins at
      load time but not at run time.

    *Reason:* the compiler applies `!use` at load time, so starting from the end-of-load
    selection made a `!use $X` inside Rules change how records are parsed.
104. *(P6)* **Input settings.** Records are read before any statement runs, so `InputFormat` and
    `OnUnparsable` given by a top-level `!set` anywhere govern reading (the last one wins).
    Inside Rules, `!set OnUnparsable` affects later parses, such as `$_ := $field[2]`, and
    `!set InputFormat` has no effect. `!set Trace = on` starts collecting a trace for the
    record. `EnforceConstraints` is stored only (P9).
105. *(P6)* **Values and variables.** A value is a `Form`, a text or an int.
    - `$in` is the parsed input, and `$raw` the unparsed form text.
    - `$field[n]` past the last field is the empty string, as in awk; `n < 1` is an error.
    - `$NF` and `$NR` count fields and records. `$NR` is numbered over all inputs of a run.
    - A text becomes a form, when one is needed, by parsing it with the current input
      orthography.
    - Reading an unset variable is a run-time error. `in`, `raw`, `NF`, `NR` and `field` are
      read-only.
106. *(P6)* **`!print` directives.**
    - `%O` renders forms in the output orthography and `%O[$X]` in `$X`; texts and numbers
      are written verbatim.
    - `%S` writes the canonical segment bundles, separated by spaces.
    - `%I` writes the raw input text for `$in`, other forms in the *input* orthography, and
      texts verbatim.
    - `%F` writes texts verbatim and forms like `%O`.
    - `%L` writes the variant's label, empty before P9.
    - The lexer already decodes `\n` and `\t`. There is no implicit newline, and several
      `!print`s concatenate. The default `input<TAB>output` lines appear only when no
      `!print` ran for the record.
107. *(P6)* **`!assert "text"`** compares the current variant's form, rendered in the output
    orthography, with the text. A mismatch raises `YascRuntimeError` located at the `!assert`
    line and naming the record. The record then produces no output, and the CLI continues.
108. *(P6)* **Pending constructs at run time.**
    - `allow_pending` (CLI `--allow-pending`) compiles with `allow_unimplemented=True` and
      runs with `skip_pending=True`. Each skipped construct is warned about once per run.
    - `--list-rules` always compiles with `allow_unimplemented=True`, because it only lists.
      `--check` is strict unless `--allow-pending` is given, so it answers "would this run?".
    - Under P7 placeholders, stress written by rules stays on segments (entry 97), so the
      Latin example runs without SyllableMarks.
109. *(P6)* **CLI formats.**
    - Text output is `!print` text or `input<TAB>output`.
    - With `--trace`, each record is preceded by `== input` and by its trace lines, indented
      two spaces: `id  name  line  before → after  (focus i..j, …)`. The forms are rendered
      in the output orthography.
    - `--json` writes JSON Lines, with the schema documented on `Result.to_dict`.
      `trace` is filled only with `--trace`, and `error` is present only on failure.
    - Warnings go to stderr as `source:line: warning: …` in both modes; `--json` also
      includes them.
    - `--list-rules` writes tab-separated `id name date line pending rule` after a `#!`
      header, or one JSON object per rule with `--json`.
    - `--check` prints `SCRIPT: OK: N rules, M warnings`, and `--profile` writes a stderr
      table of calls, applications and seconds per rule, slowest first.
    - Exit status 1 means a load error or unreadable script. 2 means usage errors (including
      a missing lexicon, `--dialect` and `--wide`) or a failed record.
110. *(P6)* **Lexicon details.**
    - Header names are split on whitespace or commas, and a header may repeat.
    - `%%` is recognised after leading blanks, and a UTF-8 BOM is dropped. The form field is
      stripped, and its column is kept for carets.
    - `regex:` uses `re.match`, so it is anchored at the start. Every group is a field; with
      no `form` group, group 1 is the form.
    - `csv` uses the `csv` module.
    - A malformed record carries `Record.error`, which becomes a run-time error for that
      record only: a bad date or feature item, or a regex mismatch.
    - Feature items are `vName` separated by whitespace or commas, and a bare `Name` is `!`.
111. *(P6)* **Warnings and API.**
    - Warnings cover approximate renderings of the final outputs (not of `!print`
      intermediates), dated records alongside undated rules (once per run), and skipped
      pending constructs.
    - `Script.apply` raises the record's error, while `Runtime.run` stores it on `Result`.
    - **Deviation from spec §11.2** (resolved in P10, entry 170): `sc.phonology.segment`
      took only a mapping, not spec text such as `"{+Syll +High}"`; it now accepts both.
112. *(P6)* **Performance.** *Measured* on CPython 3.10 (`tests/test_runtime.py`
    `PerformanceTests`): 1,000 records × 100 simple rules take about 1.9 s through the whole
    CLI (reading the lexicon, parsing, rules, rendering and output to a file), against
    1.5 s for the engine alone (entry 99). The acceptance limit is 10 s. The runtime adds
    per record a fresh `ApplyContext` (settings copy and RNG seeding), one parse and one
    render; nothing else needed optimising. `--profile` wraps each built `BasicRule.apply`
    per instance, so it costs nothing when it is off.
113. *(P7)* **The syllable tier.** `SyllableTier(fs, n, syls, syllabifier, kinds)` holds
    sorted, non-overlapping, contiguous `Syllable(start, end, nuc_start, nuc_end, feats)`
    objects with a contiguous nucleus; roles are derived from the nucleus span (§11).
    - `feats` is a full-width `Segment` in which only the syllable-scope slots are used.
    - Equality is by `(n, syls)`, so `Form` equality (entry 47) covers the syllable
      structure and the syllable features. A change of either therefore counts as a change
      for `run_persistent`, `/:*` and the trace.
    - `edges` is precomputed, so `Form.gap_marks` stays O(1).
114. *(P7)* **Role pseudo-features (fixes compiler bug C2, notes.md §4).**
    - `declare_role_features` adds `SylOnset`, `SylNucleus`, `SylCoda` and `Syllabified` to
      every compiled Phonology before sealing. They are Unary features of scope `role`, and a
      name the user has declared is skipped.
    - Their segment slots are always `None`. A `SylView` computes them, and without a tier
      they read as absent.
    - Writing one on the RHS (`{F}`, `~{F}`, `_F`) is a load-time `YascDefinitionError`
      ("read-only role pseudo-feature").
    - They are omitted from `FeatureSystem.canonical()` and ignored by strict specs
      (entry 15).

    *Reason:* the notes' suggestion. Real features keep name lookup, hints and the matcher
    unchanged: a spec that mentions one is not plain, so it gets `form.view(k)` (entry 45),
    and it is never put in the spec-match cache (entry 43).
115. *(P7)* **Reading and writing syllable-scope features.**
    - *Reading* goes through `form.view(k)` (a `SylView`). An unsyllabified segment reads
      `None`.
    - *Writing:* `BasicRule._syl_split` splits each RHS spec into a segment part and a
      syllable part. The segment part is applied as before, implications included. The
      syllable part is recorded in `_Piece.sylw`. `SyllableTier.write` applies it after the
      piece's segmental rewrite, at the new position of the output segment, so an inserted
      segment is written after the upkeep has placed it in a syllable.
    - Strictness never clears syllable features.
    - A write to an unsyllabified segment, or to a form with no tier, is a no-op. In trace
      mode `ApplyContext.warn` records it, and the runtime adds the warning to
      `Result.warnings` (spec §5.4, §12).
116. *(P7)* **Upkeep under the per-position rewrite (entry 88).** Inside `[i, j)`,
    `after_replace` pairs the old segments that the index map deletes with the new segments
    it does not reach, in order.
    - A `$n` copy, a strict `'[x]` replacement and a metathesis therefore keep their
      position's syllable and role. A metathesis (`V C --> $2 $1`) reaches the hook as two
      replacements by copies. Spec §5.4 now says so [Δ].
    - The structure stays *positional*. After `pat` becomes `pta`, t holds the nucleus role
      until the tier is recomputed (`/:$`, `Persistent`, `!syllabify`).
    - A pure feature change (an identity map) returns the tier itself.

    *Note for P8:* this pairing suits syllables, but autosegment links that should follow a
    moved segment need a whole-span map (entry 88).
117. *(P7)* **Insertions.** A run of inserted positions takes the syllable and role of its
    left neighbour, if that neighbour is syllabified and in the same word. Otherwise it takes
    those of its right neighbour, on the same terms; otherwise it stays unsyllabified.
    - "Same word" means that the new form's stored gap between them has no `#` or `##`. The
      gaps reach the hook through `Form.replace` (§12). The engine's `attach` (entry 89)
      decides where those marks go, so `# ___ C` prothesis has no left neighbour.
    - This follows spec §5.4 literally, so a consonant inserted after a nucleus becomes part
      of the nucleus. `/:$` or `Persistent` gives a proper analysis.
118. *(P7)* **Implications may not mention syllable-scope or role features** (this answers
    entry 16). Doing so is a load-time error.
    *Reason:*
    - `close()` works on a bare segment, without a form.
    - A syllable value is shared by every segment of the syllable, so a segment-level
      implication that wrote one would have non-local effects.
    - One that read a syllable value would give the same segment different results in
      different forms.

    Rules can express these dependencies.
119. *(P7)* **Canon.**
    - Canon `r` of `m` scores `m − r`.
    - The dynamic program runs from the right and minimises `(unsyllabified segments,
      −score)`. Ties go to the higher-ranked canon at the leftmost position, then to a
      syllable over a skipped segment.
    - Without `AllowUnsyllabified`, a chunk that cannot be fully parsed stays unsyllabified,
      because an unparsable segment costs −∞.
    - The letters are global macros that must expand to single-segment specs. They are
      resolved at compile time into `SyllabificationDef.letters`.
    - A canon's nucleus runs from its first V to its last V, and a canon with no V is an
      error.
    - The notes' sums for *abamordi* (7 and 10) mis-score the final CV *di* in both parses.
      With the ranking CV > CVC > VC > V of `orig-notes/scer.txt`, a.bam.or.di scores 10 and
      a.ba.mor.di scores 12. The DP chooses a.ba.mor.di, as MaxOnset does.
120. *(P7)* **MaxOnset in detail.**
    - *Chunks.* The *stored* `.` and `##` marks, and `#` marks in `Domain word`, are hard
      boundaries. Derived edges are not. Each chunk is syllabified on its own.
    - *Nuclei* are the greedy longest `Nucleus` matches, from left to right.
    - *NucleusPreference* (S3) applies to adjacent nuclei. `first` demotes the second nucleus
      if a `Coda` match from the end of the first covers it. `last` demotes the first if an
      `Onset` match ending at the second covers it.
    - *Onset.* The earliest start of an `Onset` match that ends at the nucleus, no earlier
      than the end of the previous nucleus. No match means an empty onset.
    - *OnsetRequired.* A nucleus with an empty onset is dropped unless it is at a word start
      (gap 0, or a stored `#`/`##`).
    - *Coda.* The longest prefix of the remainder that `Coda` matches. The rest is
      unsyllabified; this reading is now in spec §5.7 [Δ].
    - *Templates.* A missing template matches only the empty string. A missing `Nucleus`
      gives a load-time warning, which the notes' empty section now triggers.
121. *(P7)* **The active syllabification.**
    - As for orthographies (entry 103), a record starts with the most recently defined
      Syllabification, and `!use $S` re-selects it (`ApplyContext.syllabifier`).
    - `!syllabify` with no argument uses it, and so does `/:$`, which falls back to the
      syllabifier of the form's tier. With neither, it is a run-time error.
    - Syllabifiers are compiled once per `Runtime`.
    - `Persistent` recomputation uses the syllabifier that built the tier. `BasicRule.run`
      does it once per rule that changes the form, not once per focus, and before the trace
      step.
    - `/:$` runs in `BasicRule.apply`, before `/:*` and the mode. If the rule then does not
      apply, the re-syllabified form is still returned.
122. *(P7)* **S1: stress survives re-syllabification.** A new syllable inherits every
    syllable-scope value from the old syllable that held the first segment of its nucleus;
    otherwise the values are unspecified. This applies to `!syllabify`, `/:$` and
    `Persistent`: spec §5.4 [Δ], Appendix A16.
    *Reason:* the nucleus is a syllable's most stable anchor. Without inheritance, every
    persistent recomputation would erase stress.
123. *(P7)* **S2: orphans are re-attached with the templates** (spec §5.4 [Δ], A17).
    - Orphans first join the next syllable as onset: the longest suffix that, together with
      that syllable's onset, matches `Onset`.
    - The rest then join the previous syllable as coda: the longest prefix that extends its
      coda to a `Coda` match.
    - Anything left over is unsyllabified. The neighbours must be in the same word, and on a
      tier built by hand, with no syllabifier, orphans stay unsyllabified.

    *Reason:* the simple alternatives each break a Latin word:
    - joining the left syllable as coda gives \*seɲˈoɾ for SENIŌREM instead of seˈɲoɾ;
    - joining the right syllable as onset gives \*θjˈudad for CĪVITĀTEM;
    - leaving orphans unsyllabified also gives \*seɲˈoɾ.

    Re-attaching with the syllabification's own templates gets both words right, and does
    locally what a re-syllabification would do.
124. *(P7)* **S3: hiatus is the default under `Nucleus V`** (spec §5.7 [Δ], A18).
    `NucleusPreference` applies only where the templates allow a vowel to be a margin, as in
    the Phonix *bui* test with `Onset (C)(V)` and `Coda (V)(C)`. No `Hiatus` setting is
    added.
    *Reason:* FĪ.LI.UM, A.QU.AM and A.RĀ.NE.AM need a hiatus, the Phonix example needs the
    choice, and this rule gives both without new syntax.
125. *(P7)* **S4: no `Default=`.** A new syllable starts with its syllable features
    unspecified. Value tests and comparisons fail on unspecified values, as `Cmp.test`
    already does, so `{0Stress}` and `{<2Stress}` do not match a syllable that no rule has
    written, while `{_Stress}` does. Scripts assign defaults by rule, as the rule Unstressed
    does in the Latin example. Spec §5.4 [Δ], A16.
    *Reason:* the smallest change. A `Default=` modifier would be new syntax, touching P1 and
    P4.
126. *(P7)* **SyllableMark.**
    - A SyllableMark may set only `Scope(Syllable)` features; anything else is a load-time
      error.
    - `syllabify` applies each pending mark to the first syllable that starts at or after
      its gap within the same chunk, so `'stra` stresses *ra*, and then clears the pending
      marks.
    - `render_ex` writes the mark of every syllable whose features match one (P3's
      `_sylmark_text`) before the syllable's first segment, in place of a stored `.` at that
      gap.
    - Derived edges are never written as `.`, so `esˈkwela` needs no dots. Explicit stored
      marks are kept (entry 47).
    - *Amended after P7:* a SyllableMark at the form edge, or right after a word or phrase
      mark, stores no `SYLLABLE` mark, because the edge already implies a syllable boundary
      (spec §5.2). Other marks still store one (entry 57). Previously `'stra` rendered back
      as `.st'ra`; it now renders as `st'ra`.
    - Round trip: parse → syllabify → render → parse → syllabify gives the same tier.
127. *(P7)* **Performance.** *Measured* on CPython 3.10:
    - syllabifying 1,000 short words takes 0.10 s with MaxOnset and 0.07 s with Canon;
    - the Latin lexicon (48 records, 40 rules) loads in 0.08 s and runs in 0.11 s, and the
      whole CLI run takes 0.25 s;
    - the P5/P6 performance tests are unchanged: 1.47 s and 1.88 s.

    Forms without a syllable tier take no new code path, apart from one `getattr` in
    `Form.__post_init__` and the `takes_frame` check in `replace`.
128. *(P8)* **Module layout.** Everything P8 lives in the new `yasc/tiers.py`: the tier value
     (`Auto`, `AutoTier`, `TierCrossing`), the segment view (`TierView`), the pattern payloads
     (`LinkSpec` for `S^X`, `FloatPred` for `^X`, `TierX`), the RHS operations (`TierOp`), the
     rule-engine hooks (`rewrite`, `after_rule`, `apply_tier_rule`, `project`, `unproject`),
     the commands (`associate`, `command`), and the compiler and orthography helpers. Shared
     files only call into it through one-line hooks (see "Interface changes").

129. *(P8)* **Representation and order** (design §11). An autosegment is `Auto(id, value, anchor)`.
     Ids are stable within a tier and never reused, so an `Env` can hold them (`=name`).
     Lines are `(segment, id)` pairs. The tier order is the tuple order, kept consistent with
     a key in half-gap units: `2g` for a floating autosegment at gap `g`, and `2s+1` for a
     linked one whose first segment is `s`. A linked autosegment's anchor is always the gap
     before its first segment. **Tier equality ignores ids**: it compares the values, the
     floating anchors and the lines by tier position. So two derivations that differ only in
     the ids they allocated give equal forms, which keeps `/:*`, persistent rules and change
     detection exact.

130. *(P8)* **Levels and contours** (spec §5.5). The *levels* of a tier feature are its declared
     values that are not a concatenation of two or more other declared values
     (`[H] [L] [M] [HL] [LH]` gives `H L M`). Writing a value creates one autosegment per
     level (`HL` gives H and L, linked to the segment in that order), using the split into
     the fewest levels. A value that is not a concatenation of levels is a single
     autosegment.

131. *(P8)* **No-Crossing Constraint.** Checked in O(L log L) whenever a tier is built (`ncc_ok`).
     Linked autosegments never change their relative order through an operation. The only
     exception is the whole-focus remap (decision 6), where segments themselves moved.
     A violation raises `TierCrossing`, a `YascRuntimeError`, which the callers handle as
     follows:
     - in a rule (link operation, tier-feature write, remap), the focus is left unchanged
       and counts as not applicable, so `/:1` and `/*` try the next candidate (as for
       design §13 entry 90);
     - in `!associate`, that one link is skipped;
     - directly through the Python API, it propagates.

132. *(P8)* **Stray** (spec §5.5). Only an autosegment that *loses its last line* through an
     operation is a stray; autosegments that were already floating stay as they are.
     - With `Stray=delete` a stray is removed.
     - With `Stray=float` it floats. After a write or delink it floats at the gap before its
       old first segment, or just after that segment if an autosegment earlier in tier order
       is still linked there. So delinking the L of an HL contour leaves `ta'^``.
     - After a segmental change it floats at the image of that gap under the replacement's
       gap map, so deleting a toned final vowel leaves `tam^'`.

133. *(P8)* **Links follow moved and copied segments: a whole-focus source map** (the task's
     metathesis requirement; design §13 entries 88 and 116).
     - `Form.replace` cannot provide the map. The engine rewrites a focus one position at a
       time, so a metathesis reaches `after_replace` as copies.
     - Instead, `BasicRule.rewrite` hands forms that have tiers to `tiers.rewrite`, which does
       three things:
       1. it runs the ordinary segmental rewrite on a copy of the form without tiers, so the
          marks, brackets and syllables are exactly as before P8;
       2. it derives from the piece a *source map*: for every new position, the old segment
          it carries (the segment itself, or the source positions of each `$n` copy);
       3. it rebuilds every tier from the pre-focus tier with that map (`AutoTier.remap`).
     - As a result, lines follow a moved segment (metathesis), and a copy shares its
       source's autosegments (gemination `V --> $1 $1` gives one H linked to both vowels).
     - Only `remap` may reorder linked autosegments, to follow the segments. If a crossing
       remains, it cannot be resolved (for example, a multiply linked autosegment split by
       a moved segment), so the focus is left unchanged (decision 4).
     - Floating anchors follow a gap map that sends each interior gap to the start of its
       position's output. A pure insertion follows the rule's `attach`.
     - `Form.replace`'s own `tier.after_replace` (the P3 four-argument hook) still keeps
       tiers correct for direct callers. Per decision 6, the engine then discards its result.
     - rules.py gets two small changes for this: `_Piece.env` (the focus environment) and
       one line at the top of `rewrite`.

134. *(P8)* **Writing through the segment view in rules** (spec §5.5). RHS specs are applied to the
     segments as before, so `{[H]Tone}` first lands in the output segment's tier slot. Then
     `tiers.rewrite` moves every such value to the tier (`AutoTier.write`, which relinks the
     segment, with the old autosegments following `Stray`) and clears the slot.
     - Which item wrote what comes from the RHS items: a `spec`/`weak` item that mentions
       the tier feature (so `{_Tone}` delinks), an `[x]` merge that carries a tier value, or
       a `'[x]` replacement (always).
     - A weak write (`~{[L]Tone}`) applies only when the segment view was unspecified.
     - The segments of a form with tiers therefore never hold tier values. Forms without
       tiers (built by `Form.from_segments`, and projected tier forms) keep the pre-P8
       behaviour, where the value lives in the segment tuple (design §13 entry 22).

135. *(P8)* **When the OCP applies** (spec §5.5). The tier's `OCP` setting is enforced:
     - after parsing;
     - after every rule that changes the form (`BasicRule.run` hook; tier-only rules
       included).

     `!ocp Tier [merge|delete]` applies the mode given, or else the tier's setting, or
     `merge` when the setting is `off`.
     - Adjacent means adjacent in tier order, with no stored word or phrase mark between the
       two autosegments.
     - `merge` keeps the first autosegment and gives it the lines of both.
     - `delete` removes the second one together with its lines.

136. *(P8)* **`!associate`** (spec §5.5). The command works word by word; a word is a span without a
     stored word or phrase mark.
     - The floating autosegments of the word are linked one-to-one, in tier order, to the
       TBUs that have no line, in the given direction. A link that would cross is skipped.
     - The floating autosegments still to come are re-anchored past each TBU just linked,
       so leftover tones keep their order and stay floating: `^'^`^'ta` gives `ta'^`^'`.
     - With `spread=last`, each TBU still without a tone is then linked to the last
       autosegment (in the direction) of the nearest TBU before it that has one.
     - `mode=one-to-one` is the only mode.

137. *(P8)* **Tone input.** A segment whose diacritics set a tier value gets new autosegments
     for its levels, linked to it alone, and the value leaves the segment. `FloatingPrefix`
     followed by exactly one diacritic on an empty base (`^'`) is a floating autosegment at
     the current gap. It is a new token kind of the tokenizer's dynamic program; its text
     must set a tier value, otherwise it is an `UnparsableError`. Several floating tones are
     written one after another (`^'^``).
     - **Rendering** writes tier values through the segment view, that is, as diacritics on
       the TBU. Each floating autosegment is written as the prefix plus the diacritic that
       sets exactly that value, choosing the one with the fewest features.
     - Floating autosegments come before the separators of their gap.
     - `parse(render(f)) == f` holds; the round trips are tested.

138. *(P8)* **A floating autosegment at a word-boundary gap belongs to the word before it.** This
     governs rendering, the OCP, `!associate` and the projection of tier-only rules, so
     `tam^' ba` and `tam ^'ba` both parse with the float in *tam*. Since a gap is the only
     anchor, a word-initial float of a later word is written after the word's first letter
     (`b^'ana`) or at the end of the word (`ka^'`).

139. *(P8)* **Pattern elements** (spec §6.5; design §11 `LINK`/`FLOAT`).
     - `S^X` compiles to a SEG edge whose payload is a `LinkSpec`. It behaves like a
       non-plain spec, so it is matched on `form.view(k)` and is never cached.
     - `S^X` over a disjunction or a one-segment `[x]` is distributed over its alternatives.
     - `S^X` needs at least one line matching X, and `S^X'` exactly one line, which matches X.
     - `S^0` needs no line. `(a)` binds the autosegment's value.
     - `=name` binds the autosegment id; if the name is already bound, the ids must agree.
     - `^X` compiles to a new zero-width closure op `FLOAT` with a `FloatPred`. It looks at
       the floating autosegments anchored in the current (virtual) gap and may bind in
       several ways, so the matcher branches, keeping the preference order.
     - An element that binds nothing gives one environment.
     - Limitation: two `^X` elements at the same gap may match the same autosegment.
     - The canonical form stays `S^[Tier.]X['][=name]` (design §13 entry 32).

140. *(P8)* **RHS operations** (spec §6.5 table).
     - An RHS `S^X`, `S^+X`, `S^-=h` or `^[H]` compiles to one `IRRhsItem(kind="tier")`:
       `pattern` is a `TierOp`, and `alternatives[0][0]` is the compiled segment part `S`,
       which is aligned and rewritten like any item.
     - The link operation runs after the segmental rewrite, at the output position of `S`.
     - `S^*` keeps the lines. `S^(a)` links a new autosegment with the value of `a`.
     - A floating RHS element is zero-width and does not take part in positional alignment.
     - **Floating elements pair up in order:** the k-th top-level `^X` of the LHS with the
       k-th floating element of the RHS.
       - A pair relabels the matched autosegment (`^[H] --> ^[L]`).
       - An unpaired LHS element is deleted with its lines (`^=h --> 0`).
       - An unpaired RHS element inserts a new floating autosegment at the gap after the
         outputs of the RHS items before it.
     - An RHS `^=h` (make `h` float here) is not defined by the table and is a load-time
       error.
     - An unbound `=h` on the RHS is a run-time error.

141. *(P8)* **Tier-only rules** (`/:T Tier`; spec §6.5; design §11).
     - The compiler rewrites the rule's syntax before macro expansion:
       - `[X]` becomes `{[X]Tier}`, and `[X Y]` a sequence of such specs;
       - a bare `(a)` becomes `{(a)Tier}`.
     - In the syntax, `(a)` is an optional macro. The parser now accepts a bare `(name)` on
       the RHS; outside `/:T` rules the compiler reports it (as an unknown macro).
     - At run time the tier is projected: one single-feature segment per autosegment, with a
       word mark where decision 11 puts a word boundary. The ordinary rule machinery runs on
       the projection: mode, direction, `/:*`, contexts and filters. The projection's only
       tier is an id tracker updated by `Form.replace`.
     - Mapping the result back:
       - kept autosegments take their new value;
       - deleted ones go with their lines;
       - a position whose value was unset is deleted;
       - inserted ones float between their neighbours.
     - The trace shows one step, with the real forms.
     - Values written by a tier rule are not decomposed into levels.

142. *(P8)* **Tier references.** `^X` without `Tier.` means the single tier feature. If several are
     declared, the tier must be named; this is checked at load time, together with the
     existence of the tier feature and every `[X]` value.

143. *(P8)* **`[á]` in patterns and on the RHS keeps its tone.** The compiler's `[x]` segments
     come from the parsed form with each tier's segment view written back into them
     (`tiers.segments_with_tiers`). So `'[á]` and `C:[á]` require H, and an RHS `[á]` writes
     H through the view (decision 7).

144. *(P8)* **Not covered.** No load-time check was added for these:
     - Implications never see tier values on forms with tiers, because the values are not
       in the segment. They see them only on forms without tiers. A later phase may forbid
       tier features in implications, as design §13 entry 118 does for syllable features.
     - `$n` back-references compare `Segment`s only, so tone is ignored when matching `$n`.
       On the RHS, copies do carry their lines (decision 6).
     - Strict specs ignore tier features, as design §13 entry 15 already says.

145. *(P8)* **Performance** (plan P8 item 8).
     - Forms without tiers take no new slow path. The only additions are one truthiness test
       in `Form.view`, one in `BasicRule.rewrite` and one in `BasicRule.run`, plus an `elif`
       in the matcher's closure loop that is reached only for `ALT` ops.
     - *Measured* on CPython 3.10 with `YASC_SKIP_PERF` unset:
       - in the final run, 10k anchored matches take 0.41 s; the rule engine does 1,000
         forms × 100 rules in 1.50 s, and the full runtime in 1.93 s (P7: 1.47 s and 1.88 s;
         the limit is 10 s);
       - the tone example (8 records, 3 rules, 2 commands) runs through the CLI in about
         0.2 s.

146. *(P8)* **Tests changed in other phases' files** (the gating is gone, so these expectations
     changed):
     - `tests/test_nfa.py` `test_errors`: `^X` and `S^X` now compile, to a FLOAT edge and a
       LinkSpec SEG edge.
     - `tests/test_compile.py`:
       - `test_tier_rule_placeholder` became `test_tier_rules_compile`;
       - `test_revised_example` now requires a strict load with no warnings.
     - `tests/test_cli.py` `test_pending_constructs` (edited at the coordinator's request,
       after P9 finished): nothing is pending any more, so it now checks that `--check`
       succeeds strictly on `examples/revised-example.yasc` and that the script runs without
       `--allow-pending` (`tabi > dab`, `papi > pap`, no skips).

147. *(P8)* **Interplay with P9: routing.** `BasicRule.apply` sends rules with P9 features, and
     every rule inside a domain, to `variants.apply_basic`, before the P8 `/:T` hook.
     `variants._body` and `_apply_in` therefore send `/:T` rules to `tiers.apply_tier_rule`
     themselves. Stochastic and optional `:each` decisions (`decide`) do not apply inside a
     tier-only rule; a whole-rule `/%n` or `/???` still does. `variants._run` enforces the
     OCP after a change, as `BasicRule.run` does.

148. *(P8)* **Tier rules inside `/:C±` and `/:C*` domains** (spec §8.6).
     - `S^X` matches through `DomainView.view(i)`, which gives the full form's `TierView`.
     - `FloatPred` reads a window's `form` and `a`, so `^X` finds the floating autosegments
       of the window's gaps in form coordinates.
     - `variants._shift` now also shifts the piece's environment (`tiers.shift_env`), so the
       whole-focus remap sees `$n` spans and floating-element gaps in form coordinates.
       Without this, metathesis inside a domain moved the wrong lines.
     - A `/:T` rule in a domain projects only the autosegments inside the domain: floating
       ones anchored in its gaps, and linked ones whose lines all fall in its segments.
       Autosegments linked across the domain edge stay invisible and untouched.
     - Regression tests: `tests/test_tiers.py` `DomainAndParadigmTests`.

149. *(P8)* **Paradigm cells keep their tones.** `paradigm._Builder` records the parts it
     concatenates, and a cell form gets `tiers.concat_tiers(...)`. That is one tier per tier
     feature, with the parts' autosegments in order, fresh ids, and shifted lines and
     anchors. A stem's tones and those of the template's `[x]` strings survive
     (`^'ta'ma` gives `^'ta'ma+i``). The OCP is not applied when a cell is built; the next
     rule applies it.
150. *(P9)* **A domain is a window over the form.** `yasc.variants.DomainView` presents the gaps
     `a..b` of the form as a form of its own (`FormLike` plus `segs`). Focus search,
     contexts, filters and `$n` all run on the window, so material outside it is invisible.
     Rewrites are then applied to the full form, and the domain end moves by the change in
     length. Syllable views, bracket upkeep and the syllable tier therefore stay exact.
     The window's edge gaps carry the phrase mark, as form edges do, so `#` matches at a
     domain edge (spec §8.5: the marks of invisible material are unioned into the visible
     gap). *Reason:* spec §8.6 says "material outside it is invisible". A sub-form cut out
     of the form and spliced back would lose the syllable and tier structure.
     **[Δ] §8.6**, after "…material outside it is invisible": *"The domain behaves as a
     form of its own: its edges match `#` and `##`, and brackets outside it are not
     visible to `<:N`/`>:N`."*

151. *(P9)* **A focus belongs to its innermost enclosing bracket.** A focus `(i, j)` is enclosed
     by a bracket `(o, c)` when `o ≤ i` and `j ≤ c`, which includes zero-width foci at
     either edge. The whole form is the outermost domain and has no label. `/:C+ N` admits
     a focus whose innermost bracket is labelled N. `/:C- N` admits one whose innermost
     bracket is not N, and that includes foci enclosed by no bracket. Outside cyclic
     application, a `/:C±` rule applies once to every admitted domain: brackets first, in
     reverse document order (innermost and rightmost first), then the whole form. In each
     domain it skips foci that belong to a nested bracket. Contexts can still see the
     nested material. So `A --> B / C ___ D /:C+ V` on `<V:<N:CAD>CAD>` gives
     `<V:<N:CAD>CBD>`, and the non-cyclic rules of the §8.6 example also give `CEDCBD`.
     Simultaneous mode is simultaneous within a domain, and domains are processed one
     after another. With no brackets, `/:C+` never applies and `/:C-` applies to the whole
     form. **[Δ] §8.6**, replacing the `/:C+ N` item: *"`/:C+ N`, `/:C- N`,
     `/:C+ (N V)` — the rule applies only to foci whose domain is (or is not) a bracket of
     those categories. A focus's domain is the innermost bracket that encloses it; a focus
     in no bracket has the whole form as its domain, which has no category. Focus and
     contexts are confined to the domain, and material outside it is invisible. Each
     domain is processed in turn, innermost first."*

152. *(P9)* **Cyclic application (`/:C*`).**
     - Each cycle's domains are the innermost brackets, taken left to right. The group
       runs once on each with `ctx.domain = (index, label)`. Every basic rule inside,
       restricted or not, is confined to the domain, and its `/:C±` is checked against the
       domain's label. A `/:C±` written after the `]]` of the cyclic group itself is also
       checked per domain.
     - The brackets processed in a cycle are then erased. The final cycle runs once over
       the whole form, with no label.
     - The output has no brackets left, so `CEDCBD` renders without them.
     - A `/:C*` group nested inside an active cycle runs as an ordinary group in the
       current domain; it does not start cycles of its own.
     - `/:C*` on a single rule is the one-rule group of design entry 78; its `pending` is
       now empty.

153. *(P9)* **Group modifiers after `]]`** (spec §8.8).
     - `/%n` on a group draws once per invocation; `:each` has no per-focus meaning for a
       group, so it is treated the same way. `/???` forks once per invocation, and so does
       `/???:each`.
     - `/:D±` and `/:C±` restrict the group as a whole. With `/:C±` the group runs once per
       admitted domain, as in entry 2.
     - Whole-form group filters (`/:i±`, `/:o±`) keep their P5 meaning. Even inside a
       domain they test the whole form.
     - The route is `Group.apply` → `variants.apply_group`, which runs the P5 body through
       a one-shot `ctx.p9_bypass` flag, so `rules.py` needed no restructuring.

154. *(P9)* **Variants by replay.** A rule cannot return several forms, so every fork is a
     *decision point*: `variants.choose(ctx, tokens)`. On the current path it takes
     alternative 0 (the rule applied, or the first cell or dialect). `variants.explore`
     then re-runs the computation once for every other alternative, replaying the earlier
     choices, and enumerates the paths depth-first in label order.
     - The runtime explores **one top-level statement at a time**, for each variant.
     - Each path restarts from the same saved state: form, variables, settings, RNG state,
       `!only`/`!skip`, orthographies, syllabifier, dialect and constraints.
     - Output that a path prints or traces before it leaves its parent path is dropped as a
       duplicate. `!print` therefore writes the shared prefix once and then each variant's
       suffix, in label order.
     - A statement with no decision points (no `/???`, `!paradigm` or `!dialects`, found
       statically and memoised) runs as in P6.
     - A fork happens only when the rule actually changed the form. An optional rule with
       no applicable focus leaves one variant with no label token.
     - *Reason:* this adds no multi-outcome machinery to the engine, and it composes with
       `&&`, `||`, `/:*`, persistent rules and `$Name` for free. The cost is re-running
       the statement once per extra path.

155. *(P9)* **Labels and merging** (spec §8.11).
     - A label is a space-separated sequence of tokens. An optional rule gives
       `Name:yes`/`Name:no`, where the name is the rule's `/"` name, else `R<id>`; a group
       without a name gives `G<line>`. `/???:each` gives one token per decided focus, in
       focus order: `R1:yes R1:no R1:yes`. A paradigm cell gives its cell name. Dialect
       forks give no token, because the dialect has its own field.
     - After each top-level statement, variants merge when their form, dialect and
       variables are all identical. Their labels are joined with `|`, and the first
       occurrence keeps its place.
     - A token added to a merged label is distributed over its alternatives: `a|b` + `t`
       gives `a t|b t`.
     - A record with one unforked variant has label `None`. **[Δ] §8.11**, after "joined
       with `|`": *"Labels are space-separated decision tokens (`R12:yes`, `Voicing:no`, a
       paradigm cell name); a later decision is appended to every `|`-alternative."*

156. *(P9)* **Randomness** (spec §8.7).
     - `/%n` draws `rng.random() * 100 < n` once per invocation, after the date, name,
       lexical and dialect checks pass and before the rule searches.
     - `/%n:each` draws once for each focus that would change the form. A focus it drops
       still occupies its span in simultaneous mode. In `/:1` and `/*` modes the search
       continues past it.
     - `/???:each` decides per focus in the same way, and `/%n:each` with `/???:each`
       decides per focus with both.
     - Each record starts from `Seed` (entry 102). Once there are several variants, each
       carries its own RNG state, and the variants of one fork start from the same state.
       So `--dialect D` gives exactly column D of the full run, and results do not depend
       on the order of records or variants.

157. *(P9)* **Paradigms** (spec §9).
     - A cell's template builds a new form: stem segments and marks, orthographic strings,
       boundary marks in the gap where they are written, and brackets
       (`yasc.paradigm.build_cell`). If the stem was syllabified, the result is
       re-syllabified. P8 tiers of the stem are not carried over.
     - `!paradigm $X` forks the variant into the cells of `$X` whose `/:L±` and `/:D±`
       restrictions admit the record and the variant's dialect.
     - A record tagged with *another* paradigm, in its `paradigm` column or through
       `--paradigm`, is left unchanged by `!paradigm $X`. This lets `!paradigm $Noun` and
       `!paradigm $Verb` share one script.
     - When the script has **no** `!paradigm` command, a tagged record is expanded before
       its first statement runs. That is the spec's "`--paradigm`-style use".
     - The CLI option `--paradigm NAME` (and `Script(..., paradigm=)`) tags every record
       that has no `paradigm` column value.
     - An unknown tag is a run-time error for the record, and so is a paradigm with no
       admitted cell.
     - **[Δ] §9**, replacing the `!paradigm` item: *"`!paradigm $Noun` inside a `Rules`
       block expands the current variant into one variant per cell, unless the record is
       tagged with another paradigm. A record whose `paradigm` column names a paradigm is
       expanded by it before the script's statements run, when the script contains no
       `!paradigm` command. `--paradigm Noun` tags every record that has no `paradigm`
       column value. Each variant's label is its cell name."*

158. *(P9)* **Constraints** (spec §4.6).
     - With `EnforceConstraints = on`, every basic rule goes through the P9 path. Each
       single-focus output must contain no match of a `Constraint *` pattern (of the
       rule's phonology) that **overlaps the rewritten span**. For a deletion, the match
       must instead cross the gap where the segments were removed. A blocked focus is
       treated like an `/:o-` failure: the next candidate is tried, or the focus is
       dropped in simultaneous mode.
     - Violations already present elsewhere in the form do not block unrelated rules.
       *Reason:* taken literally, `/:o-` without `___` would freeze every word of the
       lexicon that already violates a constraint.
     - API: `Script.constraints`, `Script.violations(text) -> [(source, start, end)]` and
       `Script.well_formed(text)`; `yasc.variants.constraint_violations(constraints, form)`
       and `constraints_ok`.
     - **[Δ] §4.6**, replacing the third sentence: *"When enabled, they act as `/:o-` output
       filters on every rule, anchored at the rewritten span: an output is blocked when it
       has a match of a constraint that overlaps the rewritten material (or spans the gap
       of a deletion). Violations elsewhere in the form do not block a rule."*

159. *(P9)* **Output** (spec §10.5, §10.4, §11.1).
     - The default output is `input<TAB>output`, then `<TAB>dialect` when the variant has a
       dialect, then `<TAB>label` when it has a label.
     - `--wide` writes a header `#! form<TAB>D1<TAB>D2…` with the declared dialects, in
       declaration order (or only `--dialect D`). It then writes one row per record: the
       input, then for each dialect the distinct forms of that dialect's variants, joined
       with `, `. A cell is empty when the record has no variant in that dialect.
     - `--wide` ignores `!print` text and needs `!dialects` in the script (otherwise exit
       2).
     - `--json` fills in `label` and `dialect`, and its schema is unchanged.

160. *(P9)* **Dialects** (spec §10.4).
     - `!dialects (A B)` is a decision point where it runs, so a split can come after the
       common rules. At the top of the script it gives "each record is processed once per
       dialect".
     - The choices are the declared names, restricted to the record's `dialect` column
       (names separated by whitespace or commas: `Record.dialects`) and to `--dialect`.
       A variant with no dialect left is dropped.
     - A record naming an undeclared dialect gets a warning.
     - Scripts without `!dialects`: a record is processed once per name in its `dialect`
       column. `--dialect D` keeps only D, and gives D to records without the column.
     - `/:D+ G` fails when there is no dialect; `/:D- G` passes then.
     - An unknown `--dialect` is exit 2 when the script declares dialects.

161. *(P9)* **MaxVariants** counts the variants of a record after each top-level statement:
     exploration stops once the paths of a statement, across all variants, would exceed
     the cap, and more variants than the cap after merging is an error. The setting is
     re-read after the statement, so `!set MaxVariants` inside it counts. The error
     names the deciding rule.

162. *(P9)* **No new slow path.**
     - A rule or group is routed to `yasc.variants` only when it carries `/:D±`, `/:C±`,
       `/:C*`, `/%` or `/???` (`rule.p9`, computed when the rule is built). Otherwise it
       is routed only when a domain or enforced constraints are active (`ctx.p9`). The P5
       path pays one attribute test.
     - The runtime pays a memoised dictionary lookup per statement. When there is a single
       variant it does no RNG bookkeeping.

163. *(P10)* **`!include "lib:NAME"`** (spec §10.5 [Δ], A24). A path starting with `lib:`
     names a file in `yasc/lib/` (`compile.LIB_PREFIX`, `compile.LIB_DIR`), whatever the
     including file's directory; other paths are unchanged. Deduplication is still by real
     path, so a library included twice, or by `lib:` and by path, is read once. A missing
     library file is a load error whose hint names the library directory. The tools use the
     same resolution (`yasc.tools.minimize.load`). *Considered:* a search path (`YASC_PATH`)
     falling back to `yasc/lib/`. Rejected: an environment variable makes a script's meaning
     depend on the machine, against spec §12 determinism.

164. *(P10)* **The standard library's feature system** (`yasc/lib/ipa.yasc`).
     - Hayes-style features with geometry: `Laryngeal Node(Voice SpreadGl ConstrGl)`,
       `Place Node(Labial Coronal Dorsal Pharyngeal)`, `Labial Node(Round Labiodental)`,
       `Coronal Node(Anterior Distributed)`, `Dorsal Node(High Low Front Back)`,
       `Pharyngeal Node(Epiglottal)`; plain `Syll Cons Approx Son Cont DelRel Nasal Lateral
       Tap Trill Strident`, `Click` (Unary), `ATR` (alias `Tense`), `Reduced` (Unary, schwa),
       `Long`, `Stress Scalar(0,2) Scope(Syllable)`.
     - Every phone specifies every major-class, manner and laryngeal feature; Place features
       only where the phone has that articulator. All vowels carry `Round` (so a `Labial`
       node) and the Dorsal features. Palatals are coronal (−Anterior +Distributed) *and*
       dorsal, so `t_j` and `k_j` stay distinct from `c`.
     - Seven vowel heights with three binary features: near-open `{ 6` are `+Low +ATR`
       (ATR raises), open `a & A Q` are `+Low −ATR`; schwa `@` is `!Reduced`, which alone
       separates it from `3`.
     - Implosives are `+Voice +ConstrGl` and ejectives `−Voice +ConstrGl`, both declared as
       phones in X-SAMPA; IPA spells ejectives base + ʼ. Clicks are stops with `!Click` and
       no Dorsal.
     - Inventory: all of `orig-notes/xsampa` except as below, plus `U` and the affricates
       `d_z t_S d_Z` (the notes list `t_s`). The notes' `\|\|` is read as X-SAMPA `|\|\`.
       `P` and `v\` are one phone (the only shared bundle; `v\` renders as `P`).
     - Left out: phonetic-detail diacritics with no feature (`_O _c _+ _- _" _x _r _o _a _m
       _N _n _l _} _e`, `:\`, `_X`), breathy `_t` (entry 166), tone letters and `! ^ <R> <F>`
       (a general library cannot impose a `Tier`; entry 165), intonation `| || -\`.
     - The IPA orthography is generated from the X-SAMPA class lines (same bundles); `g` also
       parses as `ɡ`; combining diacritics follow the base, so input must be decomposed.
     - No macros (they would clash with scripts' own) and no constraints. Because `Constraint`
       lines belong to a Phonology section, a script using the library cannot add any.

165. *(P10)* **X-SAMPA separator settings.** `$XSAMPA` sets `CliticSeparator == [/]`
     (syllabic `=` is a diacritic; entry 58), `BracketOpen == [(]` and `BracketClose == [)]`
     (`_< _> <\ >\` use angle brackets; the label end stays `:`, which is safe because a label
     is always read right after the open bracket), `PhoneSeparator == [-]` (the X-SAMPA
     convention; written only where needed, entry 58), and the syllable marks `"`
     (`2Stress`) and `%` (`1Stress`). `$IPA` keeps the defaults, with `ˈ` and `ˌ`. The
     library defines `$XSAMPA` last, so it is active after the include. *Tone:* X-SAMPA tone
     letters would fit a `Tone Tier(...)` with levels T H M L B, but a tier in the library
     would put a tone tier on every form of every script that includes it.

166. *(P10)* **Diacritic bundles are chosen for the renderer** (spec §5.6 step 3 takes the
     k = 3 nearest bases, then adds diacritics greedily). `_j` is `{+Front}` and `_G`
     `{+Back}` (implications complete them): with the full `{+High −Low +Front −Back}`,
     `t_j` was nearer to `c` than to `t` and rendered as `c_d`. Breathy `_t`
     (`{+Voice +SpreadGl}`) tied with `_h` and turned `b_h` into `p_t`, so it is not
     declared. Equal bundles render by declaration order: `_k` before `_<` (creaky vowels
     print `_k`; implosive stops are phones), `=` before `_=`, `~` before `_~`.

167. *(P10)* **`yasc.tools.minimize`** (scer.txt "minimize"; fixes R20).
     - Candidates are segment-scope leaf features without a tier; Node values are derived.
       "Unspecified" is a value like any other.
     - Greedy: forced features first, then repeatedly the feature whose addition gives the
       highest partition entropy, ties to the feature declared first, until the number of
       classes equals the number of distinct full bundles. Then, in reverse order, greedy
       picks that are no longer needed are dropped.
     - Phones with identical bundles are reported as `merged`.
     - `predictable`: for each excluded feature, the smallest subset (at most 2) of chosen
       features that determines it over the inventory, with the value table. The text
       output hides rows whose value is unspecified; `--json` keeps them.
     - R20: the forced list is copied (`[].extend` was `None`); the loop ends on the class
       target or when candidates run out, so "every feature needed" works and no `None` is
       ever appended.

168. *(P10)* **`yasc.tools.make_ortho`** (the plan's "`yasc tools make-ortho`"). Input:
     X-SAMPA phones, parsed with `$XSAMPA` (diacritics allowed). Output: lines grouped by
     feature value, then merged when they list the same phones. The default keeps every
     feature a phone specifies, for use after the include. `--minimal` keeps the features
     `minimize` chooses and emits a `Phonology` with the library's geometry pruned to them,
     so the output compiles on its own. `--ipa` writes IPA graphemes. Phones that remain
     identical are named in a comment.

169. *(P10)* **Tested documentation** (`tests/test_docs.py`). Every fenced block with
     info string `yasc` in `docs/manual.md`, `docs/llm-guide.md` and `README.md` is a
     complete script and must compile with no warnings. An HTML comment `<!-- run: IN ->
     OUT; ... -->` on the line before it adds input/output checks; `IN [features] @date`
     passes lexical features and a record date, and several variants are joined with
     ` | `. Expected strings are compared exactly, so combining characters must be written
     decomposed (as the renderer produces them). Fragments use `text`.

170. *(P10)* **`FeatureSystem.segment` accepts a bundle string** (`"{+Syll +High}"`), as
     spec §11.2 shows (`sc.phonology.segment("{+Syll +High}")`). Until now only a mapping
     worked, and the documented call raised `AttributeError`. Values are those of spec §6.2
     output constraints: `vF`, `[X]F`, `_F`, or a bare Unary name; variables are an error.
     The mapping and keyword forms are unchanged (`tests/test_p10.py`).

171. *(P10)* **Tool entry points** are modules: `python3 -m yasc.tools.minimize` and
     `python3 -m yasc.tools.make_ortho`, each with `main(argv) -> int` (exit 0, 1 when the
     script or library does not load, 2 on usage errors, as in the CLI). `python -m yasc
     SCRIPT ...` is untouched. *Rejected:* a `tools` subcommand of the main CLI, because
     `python -m yasc tools ...` would read `tools` as a script name, and changing the
     argument parser risks the frozen CLI shape (entry 9).

172. *(P10)* **Examples.** `examples/conlang/` is new. `examples/simple/expected.out`
     (identical to `tests/data/simple-grimm.out`) and `examples/latin-spanish/expected.out`
     are added, so every directory has an expected file. `tests/test_examples.py` runs them
     all and fails if a directory is missing from its table. The Latin snapshot records the
     current output, including the known FĒMINAM miss (`hemna` for `hembra`, notes.md).
     `examples/README.md` indexes them.

173. *(P10)* **Drift fixed.**
     - `README.md` said only P0–P6 worked; it is rewritten.
     - `--allow-pending`'s help text still said P7–P9 were pending (`tests/test_p10.py`).
     - Spec §11.2's `segment("{...}")` did not work (entry 170).
     - The spec's Status line said "draft 1".
     - `yasc/__init__.py` said P0–P9.
174. *(after P10)* **RHS `[x]` replaces; `+[x]` merges** (user request, 2026-09-13;
     spec §8.2.4 [Δ], Appendix A25).
     - Plain `[x]`, and `'[x]` (now a synonym), compile to kind `replace`: x's segment,
       closed by the implications.
     - `+[x]`, `+Macro` and `+<< ... >>` compile to kind `merge`, which takes x's declared
       features only. `+` distributes over the `[x]` alternatives of a class-correspondence
       disjunction.
     - `+` must come directly before the item. It cannot be combined with `~` or a strict
       `'`; `+{...}` is an error (a spec already modifies), and so is `+` on `$n`.
     - *Reason:* users read `[k] --> [t_S]` as "k becomes tʃ". Under the old merge default it
       produced a velar-postalveolar hybrid (the P10 pitfall).
     - *Effects:* the Latin → Spanish Lenition rule now reads `VoicelessStop --> +VoicedStop`
       to keep ts > dz (DECEM > diez), and the manual and LLM guide drop the `'[x]` advice.
       No other example output changed.
