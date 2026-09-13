# YASC — Specification

*Yet Another Sound Changer*: a sound-change applier built on generative phonology
(distinctive features, feature geometry, autosegmental tiers), written in pure Python 3.

Status: this document describes the implemented version 1 (plan phases P0–P10 complete). It
was derived from `orig-notes/scer.txt`, `orig-notes/Yasc-Manual.tex`,
`orig-notes/Yasc-Example.yasc`, `orig-notes/ortho`, `orig-notes/xsampa`, `orig-notes/wishlist`
and the prototype matcher in `orig-notes/ret.py`. Where this document departs from the notes, the
change is flagged with **[Δ]** and justified; appendix A collects all of them.

Keywords MUST / SHOULD / MAY have their RFC 2119 meaning.

---

## 1. Goals and non-goals

Goals

1. Express sound changes the way phonologists write them: `A --> B / C ___ D` over feature
   bundles, with variables (alpha notation), feature geometry, and ordered and grouped rules.
2. Support three communities:
   - **historical linguistics**: reconstruction, dated rules, lexica with a few thousand
     entries, auditable derivations;
   - **conlangers**: quick SCA-style rules over orthographic classes, paradigms;
   - **LLM-driven research**: deterministic behaviour, machine-readable traces and output,
     precise errors, and a Python API.
3. Clarity of implementation first, with reasonable performance: a lexicon of 10³ short words ×
   a few hundred rules should finish in seconds.
4. No dependencies outside the Python ≥ 3.9 standard library.

Non-goals (for version 1)

- A GUI or rule editor. The wishlist item about editing and reordering rules is met by named
  rules, `include`, and `--trace`/`--list-rules` tooling (§11); interactive editing is out of scope.
- Full Optimality Theory evaluation. Constraints exist only as filters (§4.6).
- Random generators (phonology/morphology/dialect makers) listed in the notes.

## 2. Overview of a YASC script

A script is a sequence of **definitions** and **commands**, executed in two stages:

1. **Load time.** Definitions (`Phonology`, `Orthography`, `Syllabification`, `Paradigm`,
   macros) are parsed and compiled once.
2. **Run time.** The `Rules` sections and commands run once for each input record (lexicon
   entry). The current form is `$_`, as in awk/perl.

```
$P := Phonology [[ ... ]]          %% feature system
$O := Orthography [[ ... ]]        %% graphemes <-> feature bundles
Syllabification [[ ... ]]          %% optional
V === {+Syll}                      %% pattern macro
$R := Rules [[
   V --> 0 / ___#  /! #C*___
   ...
]]
```

The most recently defined `Phonology`, `Orthography` and `Syllabification` become the **active**
ones, unless you select one explicitly with `!use` (§10.3).

## 3. Lexical conventions

### 3.1 Characters and encoding
Source files are UTF-8. Identifiers are ASCII letters, digits and `_`, and start with a letter.
Orthographic strings (inside `[...]`) can hold any non-whitespace Unicode characters except `]`.

### 3.2 Comments **[Δ]**
- `%%` starts a comment that runs to the end of the line, so `%%%` works too.
- A line whose first non-blank character is `#` *followed by whitespace or end of line* is a
  comment line.
- A `#` anywhere else is **not** a comment. It is the word-boundary symbol in patterns, the base
  placeholder in diacritic templates, or a literal grapheme inside `[...]`.

*Rationale:* the notes use `#` both as a comment and as the word boundary (`# V --> 0 / ___#`).
Mid-line `# comment` in the notes must be rewritten as `%% comment`. If a rule starts with a word
boundary, write it without a space: `#C --> ...`.

### 3.3 Blocks and lines
- `[[` … `]]` delimit blocks (sections and rule groups). They nest.
- One statement per line. A trailing `/\` continues the statement on the next line; the rest of
  the physical line after `/\` is discarded (that is how the notes specify it).
- Inside `[`…`]` and `"`…`"` no other syntax is recognised.

### 3.4 Reserved punctuation in patterns
`{ } [ ] ( ) << | >> ... ___ 0 # ## - = . ' : ~ $ ^ * + ? < > !` — see §6. In orthographic strings
none of these are special.

### 3.5 Operators summary

| Token | Meaning | Where |
|---|---|---|
| `:=` | assign a value to a variable / name a section | top level, Rules |
| `===` | define a pattern macro | top level, Rules |
| `==` | feature-name aliases; orthography settings | Phonology, Orthography |
| `-->` | rule arrow; strong implication | Rules; Phonology |
| `~~>` | weak (default) implication | Phonology |
| `<--(op)-->` etc. | bidirectional implication | Phonology |
| `==>` | diacritic declaration | Orthography |
| `/`, `/!`, `/:X`, `/*`, `/%n`, `/"`, `/::`, `/\` | rule modifiers | Rules |

## 4. Phonology (feature system)

```
[$Name :=] Phonology [[          %% "Phonetics" is accepted as a synonym
   <declaration lines>
]]
```

### 4.1 Features
A **feature** has a name, optional aliases, a **type** (its value set) and optional
**operations**.

Declaration forms:

| Form | Meaning |
|---|---|
| `High + -` | ad-hoc: lists the values explicitly |
| `High Binary` | values `+ -`, operation `(-)` swaps them |
| `Labial Unary` or `Labial !` | a single value `!` (present); absent otherwise |
| `Stress Scalar(1,3)` | integer values 1..3, with ops `(++)` and `(--)` that saturate at the ends |
| `Stress Scalar` | integers 0..9 (default bounds) |
| `Tone [H] [L] [M] [HL] [LH]` | many-valued; bracketed alphabetic value names |
| `Place Node(Labial Coronal Dorsal)` | geometry node (§4.3) |
| `Tone Tier(...)` modifier | autosegmental feature (§5.5) |

Value tokens are either (a) non-empty strings with no letters, whitespace or `[]{}()#`
(for example `+ - ! 1 2 3`), or (b) `[Name]`, where `Name` is alphabetic.

Once a feature is declared it is the **current feature**. The indented lines after it can be:

- `== alias1 alias2 ...` — alternative names that are accepted everywhere;
- `(op) r1 r2 ... rN` — defines operation `op` with one result per declared value, in
  declaration order. `op` is any string without letters, or `<Name>`, for example
  `(-) (++) (<Max>) (<M->)`. A result of `_` means the operation is **undefined** on that value;
  applying it fails to match (in patterns) or leaves the feature unspecified (in outputs).

The notes use `...` inside value and operation lists (`[H] [L] ... [LH]`) as editorial elision.
**[Δ]** That is not syntax: every value and every result must be listed.

### 4.2 Unspecified values
Every feature can also be **unspecified** on a segment, written `_F` in bundles. A Unary feature
has exactly two states: `!F` (present) and `_F`. This underspecification is what makes weak
implications and `~S` weak application meaningful.

### 4.3 Feature geometry (Node features)
`N Node(d1 d2 ...)` declares a class node with daughters `d1…`. The daughters can be features or
nodes declared before or after this line. Rules:

1. The tree MUST be acyclic, and each feature has at most one parent.
2. A node has no stored value. `!N` (the node is present) holds iff some daughter (transitively)
   is specified. `_N` holds iff no daughter is specified.
3. `_N` on the right-hand side removes every descendant (delinking).
4. A variable on a node, `(p)Place`, binds the **sub-bundle** of every descendant feature value.
   On the right-hand side it replaces the whole sub-bundle. This is how you write place
   assimilation: `{+Nasal} --> {(p)Place} / ___ {-Syll (p)Place}`.
5. The notes' constraint "`{(a)Labial} --> {!Place}` is created automatically" follows from rule 2
   and needs no explicit implication.

The Phonix geometry in `scer.txt` (`ROOT Node(Place Glottal Manner)` …) MUST be expressible and
ships as an example.

### 4.4 Operations on values
In bundles, an operation is applied to a variable with prefix notation: `-(a)High`,
`++(s)Stress`, `<M->(t)Tone`. Operations compose: `op1#op2(a)` means `op1(op2(a))`, so the
operation nearest the variable is applied first. **[Δ]** `ret.py` applied them in list order,
left to right. Function-composition order was chosen because it reads naturally.

### 4.5 Implications (redundancy rules)
Implications are feature-level rules that the system maintains automatically.

| Syntax | Meaning |
|---|---|
| `S --> T` | strong: whenever a segment matches `S`, force `T` onto it |
| `S ~~> T` | weak/default: when a segment matches `S`, fill `T`'s features only where they are unspecified |
| `S <--> T` | `S --> T` and `T --> S` |
| `S <--(op)--> T` | `S --> T'` and `T --> S'`, where every variable value in the target has `op` applied |
| `S <~~--> T`, `S <--~~> T`, `S <~~~~> T`, `S <~~(op)--> T`, … | mixed strong/weak halves. The left half of the arrow describes `T → S`, the right half `S → T` |

- `{+Syll _Voice} --> {+Voice}` is exactly `{+Syll} ~~> {+Voice}`; the two forms are equivalent.
- **When implications apply.** Implications run after any rule changes a segment. Only changed
  segments are affected, and anything those changes trigger in turn. Implications run in
  declaration order and repeat until a fixed point is reached. If there is still no fixed point
  after `|implications| × 4` passes, the system raises an error (a cycle). Rules marked `/:Raw`
  suppress this step.
- Input segments produced by the orthography also get implications (weak ones only fill gaps).
- **Caution [Δ]:** the example `{(a)High} <--(-)--> {(a)Low}` literally makes `-High` imply
  `+Low`, which is phonologically wrong. The syntax is kept, but the shipped examples use
  `{+High} --> {-Low}` and `{+Low} --> {-High}`.

### 4.6 Constraints (well-formedness filters)
`Constraint * <pattern>` declares a sequence the phonology forbids, for example
`Constraint * {-Syll +Voice}{-Syll -Voice}`. Constraints are inert unless enabled by
`!set EnforceConstraints = on`. **[Δ]** When enabled, they act as `/:o-` output filters on
every rule (§8.4), anchored at the rewritten span. An output is blocked when a constraint
matches in a way that overlaps the rewritten material, or that spans the gap of a deletion.
Violations elsewhere in the form do not block a rule; read literally, a whole-form `/:o-` filter
would freeze every word that already violates a constraint. Constraints are also exposed
through the API (`Script.constraints`, `Script.violations(text)`, `Script.well_formed(text)`),
for example for word generators.

## 5. Representation of forms

### 5.1 Form
A **form** (the value of `$_`) contains:

1. a sequence of **segments** `s[0..n-1]`. Each segment is a feature bundle, a finite map from
   features to values;
2. **gaps** `g[0..n]`, one between each pair of adjacent segments plus one at each edge. Each gap
   holds a set of **boundary marks**;
3. **category brackets**: properly nested labelled spans over gap indices, for example
   `<N: ... >` (§5.3);
4. zero or more **tiers**: the syllable tier (§5.4) and autosegmental tiers such as Tone (§5.5);
5. **record data** that is not phonological: lexical features, dialect, date, fields (§10.2).

**[Δ] Boundaries live in gaps, not in the segment string.** A segment pattern therefore skips
boundaries automatically, which answers the wishlist request that syllable breaks and similar
marks be "ignored unless specifically addressed". A boundary element in a pattern is a
zero-width assertion on the current gap. (This generalises the epsilon-with-meta edges in
`ret.py`.)

### 5.2 Boundary marks and their strength

| Mark | Pattern symbol | Default input separator | Implied by |
|---|---|---|---|
| syllable | `.` | `.` | `#`, `##`, word edges |
| morpheme | `-` | `+` | `=`, `#`, `##` |
| clitic | `=` | `=` | `#`, `##` |
| word | `#` | space or `#` | `##`, form edges |
| phrase | `##` | `##` | form edges |

A pattern boundary symbol matches a gap that contains that mark **or any mark that implies it**.
Gap 0 and gap n always carry the phrase mark, so `#` matches at both ends of the form.

### 5.3 Category brackets
Input like `<V:<N:CAD>CAD>` creates labelled spans. Brackets are used by `/:C±` and `/:C*`
(§8.6) and by the pattern assertions `<:N` (at the opening of an N bracket) and `>:N` (at its
closing). `<:*` and `>:*` match any category. The input delimiters are configurable (§5.6)
because X-SAMPA uses `<` and `>` in diacritics.

### 5.4 Syllable tier
Syllabification (§5.7) builds a syllable tier. Each **syllable** is an object that holds a
contiguous run of segments. Each segment in a syllable has one role: onset, nucleus or coda.
Segments that belong to no syllable are **unsyllabified**. The rest of this section describes
how the tier behaves.

- **Syllable features.** Features declared with the modifier `Scope(Syllable)`, for example
  `Stress Scalar(0,2) Scope(Syllable)`, are stored on the syllable, not on its segments. A
  segment *sees* its syllable's value as though it were its own feature, so `{2Stress}` matches
  any segment of a primary-stressed syllable. Writing a syllable feature onto a segment writes it
  to that segment's syllable. On an unsyllabified segment it is a no-op, and in trace mode it
  produces a warning.
- **Role pseudo-features** (read-only, Unary): `SylOnset`, `SylNucleus`, `SylCoda`,
  `Syllabified`. You can write, for example, `{-Son SylCoda} --> {-Voice}`.
- **The `.` assertion** matches any gap that starts or ends a syllable. Explicit syllable marks
  in the input are kept as fixed boundaries for syllabification.
- **Upkeep after rules.** If the syllabification is not `Persistent`:
  - a deleted segment leaves its syllable;
  - a syllable that loses its nucleus is dissolved. **[Δ]** Its remaining segments are
    re-attached with the templates of the syllabification that built the tier: the longest
    run at their right end that forms, together with the next syllable's onset, an `Onset`
    joins that syllable; of the rest, the longest run at their left end that extends the
    previous syllable's coda to a `Coda` joins it; the others become unsyllabified. Both
    neighbours must be in the same word;
  - an inserted segment joins the syllable of its left neighbour inside the same word, or else
    of its right neighbour, taking the role of that neighbour;
  - feature changes keep the structure. **[Δ]** A segment replaced by a copy (`$n`,
    metathesis) counts as a feature change: it keeps its place in the structure.

  `Persistent` syllabification is recomputed after every rule that changes the form. `/:$`
  recomputes it before one rule.
- **[Δ] Values across syllabifications.** A new syllable starts with every syllable feature
  unspecified; value tests and comparisons fail on unspecified values (§6.2), so `{0Stress}`
  does not match an unstressed syllable until a rule writes `0`. When the tier is recomputed
  (`!syllabify`, `/:$`, `Persistent`), a new syllable inherits the syllable features of the
  old syllable that held the first segment of its nucleus.

### 5.5 Autosegmental tiers (tone)
A feature declared with `Tier(TBU=<segment-spec> [, Stray=float|delete] [, OCP=off|merge|delete])`
is **autosegmental**. For example:

```
Tone [H] [L] [M] [HL] [LH] [HM] [MH] Tier(TBU={+Syll}, Stray=float)
```

The tier is structured as follows.

- The tier is an ordered sequence of **autosegments**. Each autosegment carries one *level*
  value; contour values are sequences of level values. Each autosegment also has an anchor gap
  that orders it relative to the segments. **[Δ]** The *level* values of a tier feature are its
  declared values that are not the concatenation of other declared values, so
  `[H] [L] [M] [HL] [LH]` has the levels H, L and M. Writing a contour creates one autosegment
  per level, in order. A linked autosegment is anchored at the gap before its first segment. A
  floating autosegment at a gap that carries a word boundary belongs to the word before it.
- **Association lines** link autosegments to TBU segments. A link may never cross another link
  (the No-Crossing Constraint), so any operation that would create a crossing fails.
  **[Δ]** Operations never reorder linked autosegments, with one exception: when a rule moves
  or copies segments (metathesis, `$n`), the lines follow the segments and the autosegments are
  reordered to match, and a copy shares its source's autosegments. A rule whose rewrite would
  cross lines does not apply at that focus.
- **Segment view.** The value of `Tone` on a segment is the concatenation, in tier order, of the
  values of its linked autosegments. For example, a segment linked to H and L has `[HL]Tone`, and
  an unlinked segment has `_Tone`. Ordinary feature rules therefore work unchanged; this is how
  `Yasc-Example.yasc` is written.
- **Writing through the segment view.** Writing `{[HL]Tone}` on a segment removes its current
  links and links it to new autosegments H and L, placed at its position. Autosegments left with
  no links become floating or are deleted, according to `Stray`. **[Δ]** An autosegment left
  without lines floats at the gap before its former first segment, or after that segment if an
  earlier autosegment is still linked to it. A weak write `~{[H]Tone}` affects only toneless
  segments.
- **OCP** **[Δ]**. A tier's `OCP=merge|delete` is enforced after parsing and after every rule
  that changes the form. It applies to two autosegments that are adjacent on the tier, have the
  same value and have no word boundary between them: with `merge` the first keeps the lines of
  both, and with `delete` the second is deleted along with its lines.
- **Link notation in patterns** lets you address the tier directly (§6.5): `V^[H]`, `V^0`,
  `^[L]` (a floating L), spreading with captured autosegments, and tier-only rules (`/:T Tone`).
- **Built-in association helpers** (Rules commands):
  - `!associate Tone [dir=> | <] [mode=one-to-one] [spread=last|none]` — the universal
    Association Convention. **[Δ]** It works word by word. The floating autosegments of each
    word are linked one-to-one, in order, to the word's toneless TBUs in the given direction,
    skipping links that would cross; leftover autosegments stay floating. With `spread=last`,
    each toneless TBU that remains is then linked to the last autosegment of the nearest toned
    TBU before it, in the given direction.
  - `!ocp Tone [merge|delete]`. Without a mode it uses the tier's `OCP` setting, or `merge` if
    that setting is `off`.
- **Orthography.** Tier features are rendered like other features, through diacritics on the TBU
  (`{[H]Tone} ==> [#']`). Floating tones render with `FloatingPrefix` (default `^`) plus the
  tone's diacritic on an empty base, for example `^'`. **[Δ]** In input, a tone diacritic on a
  segment creates autosegments linked to that segment. `FloatingPrefix` followed by one
  diacritic on an empty base creates a floating autosegment at that gap. Floating autosegments
  are written before the separators of their gap.

### 5.6 Orthography
```
[$Name :=] Orthography [[
   <orthography lines>
]]
```

| Line | Meaning |
|---|---|
| `[g1 g2 ...] {F}` | each grapheme `gi` has (at least) features `F`. **Declarations add up**: `[p] {!Labial -Syll}` and `[p t k] {-Voice}` together define p. Conflicting values are an error. A trailing `...` inside `{}` (as in the notes) is allowed and ignored. |
| `{F} ==> [pre#post]` | diacritic: a grapheme string with `pre` before and `post` after it adds the features `F`. At least one of `pre`/`post` must be non-empty. Circumfixes (both) are allowed. |
| `*{F1 F2}` | features ignored when rendering (broad transcription). Node features are always ignored. |
| `PhoneSeparator == [x]` | explicit separator between segments in input (no default) **[Δ]** |
| `SyllableSeparator == [.]` | **[Δ]** new; the notes used `PhoneSeparator == [.]`, which conflicts with X-SAMPA's `.` for syllable breaks |
| `MorphemeSeparator == [+]` · `CliticSeparator == [=]` · `WordSeparator == [#]` · `PhraseSeparator == [##]` | boundary marks in input and output |
| `BracketOpen == [<]` · `BracketClose == [>]` · `BracketLabelEnd == [:]` | category-bracket delimiters |
| `SyllableMark {2Stress} == [']` | prosodic mark: written before a syllable; sets the syllable feature on it (and implies a syllable boundary) |
| `FloatingPrefix == [^]` | how floating autosegments are written |
| `Escape == [\]` | optional escape character for input text only |

Graphemes are exactly what appears between whitespace inside `[...]`. **[Δ]** There is no escape
syntax inside brackets, because X-SAMPA depends on literal `\` (for example `J\`). The example's
`[#\']` therefore declares the diacritic `\'`; the shipped examples use `[#']`.

**Parsing text into a form** (`string_to_form`):
1. Separators and bracket delimiters are recognised first.
2. At each position, choose the tokenization that covers the most characters with the fewest
   tokens. This uses a dynamic-programming pass over `prefix* (circumfix-open)? grapheme
   (postfix | circumfix-close)*`. Ties go to the longest grapheme, then to declaration order.
3. A diacritic's features are merged over the base features (the diacritic wins). Then the
   weak and strong implications apply.
4. Characters that cannot be parsed are an error, reported with column and context. The notes'
   "skip garbage" behaviour is available as `!set OnUnparsable = skip|keep|error`
   (default `error`). With `keep`, the character is kept as an opaque segment with the pseudo
   feature `!Unknown` and is rendered verbatim.

**Rendering a form as text** (`form_to_string`):
1. Features in the ignore set are dropped from the comparison.
2. If some grapheme's bundle equals the segment, use it. When several match, the first one
   declared wins.
3. Otherwise, choose a base grapheme and a set of diacritics that minimises the distance, which
   is (number of mismatched features, number of diacritics). The search takes the nearest few
   bases (default k=3), then adds greedily whichever diacritic most reduces the distance. Results
   are memoised per distinct segment.
4. A segment that still does not match exactly is rendered with the result of step 3. In trace
   and JSON output it is also flagged `approximate` together with the residual feature
   difference, so no information is silently lost.

Several orthographies can coexist, for example X-SAMPA for input and a practical orthography
for output (§10.3).

### 5.7 Syllabification

```
[$Name :=] Syllabification [[
   Onset    << C | {-Son}{+Son -Syll} | [s]{-Son -Cont} >>  %% pattern; may be empty
   Nucleus  V
   Coda     (C)
   OnsetRequired   no          %% yes: a V-initial syllable is still allowed word-initially only
   Algorithm       MaxOnset    %% MaxOnset | Canon
   Canons          CV > CVC > V > VC   %% only for Algorithm Canon; the order gives the score
   NucleusPreference  first    %% first | last: in a VV sequence, which V is the nucleus
   Persistent      no
   Domain          word        %% word | phrase: do syllables cross word boundaries?
]]
```

The templates use the pattern language of §6, restricted to sequences, optionals, disjunctions
and segment specs; no boundaries, `...` or captures are allowed. There are two algorithms.

- **MaxOnset** (default):
  1. Mark nuclei: maximal runs that match `Nucleus`, resolving VV runs with
     `NucleusPreference`. **[Δ]** Nuclei are found from left to right, each the longest match
     of `Nucleus`, so with `Nucleus V` a VV sequence is a hiatus (two nuclei); `Nucleus V (V)`
     makes it one. `NucleusPreference` then resolves two adjacent nuclei only when the
     templates let one of them be a margin: `first` keeps the first nucleus if `Coda` can take
     the second, `last` keeps the second if `Onset` can take the first. Otherwise the hiatus
     stays.
  2. Between each pair of nuclei, give the longest suffix that matches `Onset` to the next
     syllable. The remainder must match `Coda` of the previous syllable; otherwise it is left
     unsyllabified. **[Δ]** Precisely: the longest prefix of the remainder that matches `Coda`
     is the coda, and what is left is unsyllabified. With `OnsetRequired yes`, a nucleus that
     gets no onset, other than word-initially, forms no syllable.
  3. Word-edge material is treated the same way. Existing `.` marks in the input act as hard
     boundaries.
- **Canon**: dynamic programming over the segment string. It chooses a parse into canons (the
  letters C and V are macros) that maximises the total score. Unparsable segments are allowed
  but cost −∞ unless `AllowUnsyllabified yes`. This is the notes' option (5). **[Δ]** Canon
  `r` of `m` scores `m − r`; the V letters of a canon are its nucleus. With
  `AllowUnsyllabified yes` the parse with the fewest unsyllabified segments wins, then the
  highest score. `OnsetRequired` and `NucleusPreference` apply to MaxOnset only.

**[Δ]** A syllable mark in the input (§5.6) sets its features on the first syllable that
starts at or after the mark, before the next hard boundary; syllabification consumes it.

## 6. Patterns

Patterns are regular expressions over forms. They are used in rule left-hand sides (LHS),
contexts, filters, macros, syllable templates and constraints.

### 6.1 Pattern elements

| Syntax | Name | Matches |
|---|---|---|
| `{...}` | segment spec | one segment (§6.2) |
| `[abc]` | orthographic string | the sequence of segments that `[abc]` parses to in the working orthography. Each resulting segment acts as a spec with that segment's features. |
| `'S` | strict | `S`, but every feature must be identical (unspecified features included), not merely compatible |
| `S1:S2` | combined | one segment that satisfies both specs, for example `V:{-Voice}` or `C:[p t k]`. An orthographic operand must parse to exactly one segment, or be a list `[p t k]`, which is read as a disjunction. |
| `Name` | macro | the body of a macro defined with `===`. Macro names are identifiers of any length; the wishlist's "full-fledged words" are simply identifiers such as `RoundedFrontVowel`. |
| `0` | nothing | the empty string |
| `.` `-` `=` `#` `##` | boundary assertions | a gap carrying that mark (§5.2) |
| `<:N` `>:N` | bracket assertions | the opening or closing of an N-bracket |
| `P P` | sequence | |
| `<< P \| P \| ... >>` | disjunction | the alternatives are **ordered**, and the index of the alternative that matched is recorded (§8.2.4) |
| `( P )` | optional | zero or one |
| `( P )*` or `P*` | star | zero or more |
| `( P )+` or `P+` | plus | one or more |
| `...` | anything | `{}*`, any run of segments; boundaries are skipped as usual |
| `___` (two or more `_`) | locus | the focus position; only in contexts |
| `$n` | back-reference | an exact copy of what the LHS item `n` matched (§6.4) |
| `^T` | autosegment | a floating autosegment at this point (§6.5) |
| `S^T` | linked segment | a segment `S` linked to the autosegment `T` (§6.5) |

`*`, `+` and `?` as postfix operators bind tighter than sequence. A bare `(P)` is optional, as in
the notes; write `(P)?` if you want to be explicit.

**Skipping.** When a pattern steps from one segment to the next, it crosses the gap between them
whatever that gap contains. Boundary assertions are checked only where they appear in the
pattern. Segments hidden by `/:F±` (§8.5) are likewise skipped.

### 6.2 Segment specs
A segment spec is `{ c1 c2 ... }`, where each constraint `ci` has one of these forms:

| Constraint | Match meaning | Output meaning (in a rule RHS) |
|---|---|---|
| `vF` (for example `+High`, `!Labial`, `[H]Tone`, `2Stress`) | F = v | set F := v |
| `_F` | F is unspecified | unset F (for a Node: delink the subtree) |
| `{v1 v2}F` | F ∈ {v1, v2} | error |
| `>n F`, `<n F`, `>=n F`, `<=n F` (Scalar only; the space is optional) | comparison | error |
| `(a)F` | F is specified; bind `a` or check it against the existing binding | set F := a |
| `(?a)F` | anything; bind `a` to F's value **or to "unspecified"** | copy F exactly, including unspecification |
| `op(a)F` | F = op(a); if `a` is unbound, bind it to each preimage in turn | set F := op(a) (if op is undefined: unset F) |
| `F` with no value | shorthand for `!F` on Unary features, and an error on others | |

- `{}` matches any segment.
- Feature names resolve through aliases. An unknown feature is a load-time error.
- Values must belong to the feature's type, unless they are variables.
- A variable is scoped to one application of one rule: its LHS, all its contexts and filters,
  and its RHS.
- A single variable may appear on different features only when their value sets are
  identical or both are Binary.

### 6.3 Matching semantics
Matching runs over positions `0..n` (gaps) and segments. A **match** of a pattern `P` starting at
gap `i` is a triple `(i, j, β)`. Here `j` is the end gap and `β` is the environment, made up of
variable bindings, back-reference captures, disjunction indices and autosegment captures.

- A spec matches segment `s` under `β` if every constraint holds; a successful match may extend
  `β`.
- Unbound variables are bound by the first segment that constrains them. Later occurrences must
  agree, including through operations.
- The matcher returns **all** matches, in a deterministic order: by start gap in the rule's
  direction, then by end gap (longest first), then by the order of pattern alternatives.
  **[Δ]** `ret.py` returned matches in stack-pop order and followed only the first matching edge
  from each node, so it could miss matches. See `docs/design.md` §8.

### 6.4 Back-references `$n`
- `$1`, `$2`, … number the **top-level items of the LHS** from left to right. An item is one
  spec, orthographic string, macro, group `( )`, disjunction, star or plus. `$0` is the whole LHS
  match.
- In an LHS or a context, `$n` matches an exact copy of the span that item `n` matched (strict
  equality). It must refer to a different item from the one it appears in.
- Because the LHS is matched first and the contexts afterwards (§8.2), contexts may refer to any
  LHS item on either side. **[Δ]** The notes marked `{} --> 0 / $1___` as illegal; under this
  evaluation order it is legal and well defined.
- On the RHS, `$n` inserts a copy of the span matched by `n`. This is how metathesis and
  gemination are written: `C V --> $2 $1`.
- `{}'$1` in the notes means "any segment followed by an exact copy of it". Under the new
  numbering the second item refers to the first, so the example is written `{} $1`. `'` in front
  of `$n` is redundant and accepted.

### 6.5 Autosegmental notation
A tier element `T` is `^[Tier.]X[=name]`. Here `Tier` defaults to the single tier (it is
required if more than one tier is declared), and `X` is one of:

| `X` | Meaning |
|---|---|
| `[H]` | an autosegment with value H |
| `(a)` | any autosegment; bind its value to `a` |
| `*` | any autosegment |
| `0` | used in `S^0`: the segment has no links on this tier |

Its uses in patterns:

- `S^X` — segment S with at least one link to an autosegment matching X. `S^X'` means exactly
  one link, and that link matches X.
- `^X` on its own — a *floating* autosegment whose anchor lies at this gap.
- `=name` captures the actual autosegment so that the RHS can refer to it.

Its uses on the RHS:

| RHS | Effect |
|---|---|
| `S^[L]` | replace S's links with one link to a **new** L |
| `S^+[L]` | add a link to a new L; the No-Crossing Constraint decides its order |
| `S^=h` | replace S's links with a link to the captured autosegment `h` (spreading) |
| `S^+=h` | add a link to `h` (creating a contour) |
| `S^0` | delink everything |
| `S^-=h` | delink `h` only |
| `^[H]` | insert a floating H at this gap |
| `^=h` then `0` | delete autosegment `h` (write the floating element in the LHS and `0` in the RHS) |
| `S^(a)` **[Δ]** | replace S's links with one link to a new autosegment with the value of `a` |
| `S^*` **[Δ]** | keep S's links |

**[Δ]** Floating elements pair up in order: the k-th floating element of the LHS with the k-th
floating element of the RHS.
- A pair relabels the matched autosegment (`^[H] --> ^[L]`).
- An unpaired LHS element is deleted along with its lines.
- An unpaired RHS element inserts a new floating autosegment after the output of the items
  before it.

`^=h` is not allowed on the RHS.

Examples:

```
V^0 --> V^=h / V^*=h ___          /*/:>   %% spread any tone rightward onto toneless vowels
^[H]=h --> 0 / V^[H] ___                  %% delete a floating H after an H-toned vowel
{+Syll}^[H]=a {+Syll}^[H]=b --> $1 {+Syll}^=a   %% OCP-style fusion (a simplified sketch)
```

**Tier-only rules.** With the modifier `/:T Tone`, the LHS, RHS and contexts are patterns over
the tier string of autosegments, for example `[H] --> [M] / [H] ___  /:T Tone` (downstep). In
this mode, specs are written `[X]`, `(a)`, `{}` or `...`. Boundaries are the word boundaries
projected onto the tier. Deleting an autosegment also deletes its links; an inserted autosegment
starts out floating. **[Δ]** `[X Y]` is the sequence `[X] [Y]`, and `(a)` may also appear on
the RHS. A value that a tier-only rule unsets deletes the autosegment.

## 7. Macros

`Name === pattern` defines a pattern macro. For example:

```
V === {+Syll}
C === {-Syll}
X === << # | C >>
K === <<[p]|[t]|[k]>>
```

- **Names.** A macro name is an identifier that does not collide with a keyword. Names are
  case-sensitive; by convention they start with a capital letter. A bare identifier in a
  pattern always means a macro, because feature names only ever appear inside `{}`.
- **Scope.** A top-level macro is global. A macro defined inside a `Rules` block or group is
  visible from its definition to the end of that block. An inner definition may shadow an outer
  one; the compiler warns when it does.
- **Expansion.** Macros are expanded when the script is compiled. A macro may use earlier
  macros; cycles are an error.
- **Refining a macro.** `Name:{spec}` combines the macro with extra constraints (§6.1). The
  macro must expand to a single segment spec or to a disjunction of them; for a disjunction,
  the constraints are added to each alternative.
- **Class correspondence.** A macro whose body is a disjunction keeps its alternative indices
  (§8.2.4).

## 8. Rules

```
[$Name :=] Rules [[ <modifiers>
   <rule or command lines>
]] <modifiers>
```

### 8.1 Basic rule
`LHS --> RHS  modifier*`

- `LHS` is a pattern (§6) without `___`. `0` means epenthesis: the focus is an empty span at a
  gap.
- `RHS` is a sequence of RHS items (§8.2.4), or `0` (deletion).
- The contexts are `/ C ___ D` (positive) and `/! C ___ D` (negative). Either side may be empty.
  The locus must appear exactly once.

### 8.2 Applying a basic rule to one focus

1. **Focus search.** For each start gap `i`, in the rule's direction (default `/:>`), enumerate
   the LHS matches `(i, j, β)` (§6.3).
2. **Positive contexts.** Every `/` context must hold. Contexts are *conjunctive*, as the notes
   intend; write a disjunction with `<< | >>` inside a single context. A context `C ___ D` holds
   if `D` matches **forward** from gap `j` (anchored there, with an open right end) and `C`
   matches **backward** from gap `i` (the pattern is reversed and anchored at `i`). Bindings
   thread through in the order D, then C, then the next context. A context may produce several
   binding sets; each is tried in order (backtracking).
3. **Negative contexts.** A `/!` context fails the focus if it has *any* match consistent with
   the current β. Bindings made inside a negative context are discarded.
4. **Input filters** (§8.4) are checked with β.
5. **RHS construction.** Build the replacement for the span `s[i:j]` (§8.2.4).
6. **Output filters and success.** Output filters (§8.4) run on the resulting form. If they
   block, try the next candidate (focus, β). A focus counts as *applied* when the form changed,
   or with `/:~` whenever it matched.

#### 8.2.4 RHS semantics
Let the LHS match cover the segments `m1..mk`, and let the RHS items be `r1..rl`.

- **Positional alignment.** For `p ≤ min(k, l)`, item `rp` acts on `mp`:
  - a spec `{...}` **modifies** `mp`: its constraints are applied as outputs (§6.2);
  - `~{...}` is a weak modification: it only fills unspecified features;
  - **[Δ]** `[x]` (an orthographic segment) **replaces** `mp` with x: x's declared features
    with the implications applied, keeping no features of `mp`. `'[x]` means the same;
  - **[Δ]** `+[x]` **merges** x's declared features onto `mp`, so x's features win and the
    other features of `mp` are kept. `+Macro` and `+<< ... >>` merge each `[x]` alternative
    (class correspondence). `+` is written directly before the item. It cannot be combined with
    `~` or a strict `'`, and `+{...}` is an error, because a spec already modifies;
  - `'{...}` **replaces** `mp` entirely with the spec, keeping no features of `mp`;
  - `$n` inserts the captured span (so the span can have a length other than 1);
  - an autosegment item `S^...` works as in §6.5.
- **Excess LHS** segments (`k > l`) are deleted.
- **Excess RHS** items (`l > k`) are inserted after `mk`, or at gap `i` if `k = 0`. A spec
  inserted this way creates a new segment from the spec alone; weak implications then fill in
  defaults. In trace mode a warning is issued if the result matches no grapheme exactly.
- **Order.** Variables are instantiated first, then operations are applied, then the outputs
  are applied in feature-declaration order.
- **Class correspondence** (SCA style; implements the notes' "lazy auto-features"). This applies
  when an LHS item is a disjunction `<<a1|…|an>>` (directly or through a macro), and the aligned
  RHS item is a disjunction with the **same number** of alternatives. The output is then the
  alternative with the same index. For example, `K === <<[p]|[t]|[k]>>` and
  `G === <<[b]|[d]|[g]>>` make `K --> G` voice p, t and k. A mismatch in the number of
  alternatives is a load-time error.
- **Boundaries** inside the matched span are kept at their positions whenever those positions
  still exist. If a deletion merges two gaps, their marks are unioned. Brackets are adjusted
  in the same way.
- **Implications** (§4.5) then run on the changed or inserted segments, unless the rule is
  `/:Raw`.

### 8.3 Application modes and direction
Direction is `/:>` (left to right, the default) or `/:<` (right to left). There are four modes.

| Modifier | Mode | Definition |
|---|---|---|
| (default) | **simultaneous** **[Δ]** | Find every applicable focus on the *input* form in the rule's direction, keeping each focus that does not overlap one already kept (zero-width foci at the same gap count as overlapping). Rewrite all kept foci at once. Contexts always see the input form. This is the usual semantics in historical linguistics. |
| `/:1` | once | Apply the first applicable focus in the rule's direction. (`ret.py` behaved this way.) |
| `/*` | iterative, progressive | Apply the first focus. Then search again in the *updated* form, starting at the end of the rewritten span (L→R) or its start (R→L). The next focus must start strictly beyond the previous one, so contexts see earlier changes; this is how spreading is written. The number of applications is capped at `MaxIterations` (default 1000); reaching the cap is an error. |
| `/:*` | repeat | Re-apply the rule (in `/:1` mode, unless `/*` is also given) to its own output until nothing changes. Stops with an error if a form repeats (a cycle) or the cap is reached. |

**[Δ] Default mode.** The notes' examples imply that a rule fires once unless `/*` is given, but
`p --> f` changing only the first `p` surprises nearly every user of a sound-change applier.
The global default is therefore simultaneous. `!set DefaultMode = once` restores the notes'
behaviour for scripts that expect it.

### 8.4 Filters
- `/:i+ E` and `/:i- E` — the input form, before the rule runs, must (or must not) contain a
  match of pattern `E` anywhere.
- `/:o+ E` and `/:o- E` — the same test on the output form. A blocked output causes the next
  candidate focus to be tried (in *once* and *iterative* modes), or the focus to be dropped (in
  *simultaneous* mode, where each focus is tested on its own single-focus output).
- `E` may use `___`. In an input filter it matches the LHS span; in an output filter it matches
  the replaced span. The filter bindings are shared with β, so `/:i+ E` without `___` behaves
  like a context that may be anywhere.

The notes give these three as equivalent formulations of "delete a word-final vowel except in
monosyllables":

```
V --> 0 / ___# /! #C*___        %% (C* not (C)*: both are accepted)
V --> 0 / ___# /:i- #C*V#
V --> 0 / ___# /:o+ V
```

They are equivalent **only on single-word forms**:

- The context (`/!`) is tested around each focus, so the first rule works word by word.
- The filters (`/:i-`, `/:o+`) without `___` test the **whole form**. On a phrase such as
  `pata#ka`, the second rule is blocked because the monosyllable `ka` matches `#C*V#`, and
  the third rule allows any deletion that leaves at least one vowel anywhere in the phrase.

Use a context when a condition should hold per word, and a whole-form filter when it should
hold for the entire form. A test in `tests/test_rules.py` checks all three on single words,
and documents where they diverge on phrases.

### 8.5 Visibility
- `/:F+ S` makes the segments that match `S` invisible to this rule. `/:F- S` makes the
  segments that do *not* match `S` invisible. Use it to project tiers, for example vowel
  harmony across consonants: `V --> {(a)Back} / V:{(a)Back} ___ /:F- V /*`.
- Invisible segments are skipped exactly like gaps. The boundaries in skipped material are
  unioned into the gap that is visible.
- Invisible segments are never rewritten. Deleting a visible neighbour leaves invisible
  segments where they were.

### 8.6 Lexical, dialect and category restrictions
- `/:L+ {spec}` and `/:L- {spec}` — the record's lexical feature bundle must (or must not)
  match the spec. Lexical features are declared automatically when first used in a lexicon
  (they are Unary unless they carry a value, as in `+Romance`). Example: `[yi] --> [i] /:L+ {!N}`.
- `/:D+ G`, `/:D- G`, `/:D+ (G1 G2)` — dialect filter (§10.4).
- `/:C+ N`, `/:C- N`, `/:C+ (N V)` — **[Δ]** the rule applies only to foci whose domain is (or
  is not) a bracket of those categories.
  - A focus's **domain** is the innermost bracket that encloses it. A focus in no bracket has
    the whole form as its domain, which has no category.
  - Focus and contexts are confined to the domain, and material outside it is invisible. (This
    answers the open question in the notes.)
  - The domain behaves as a form of its own: its edges match `#` and `##`, and brackets
    outside it are not visible to `<:N`/`>:N`.
  - Each domain is processed in turn, innermost first.
  - Inside a domain, a tier-only rule sees only the autosegments within it: floating ones
    anchored in its gaps, and linked ones whose lines all lie in its segments.
- `/:C*` — **cyclic categorical application** (for groups; on a single rule it wraps the rule in
  a one-rule group). It repeats three steps:
  1. The domains are the innermost brackets.
  2. Apply the group once to each domain, with `/:C±` checked against that domain's label.
  3. Erase those brackets.

  When no brackets remain, the whole form is processed once as the final (unlabelled) cycle.
  Example: `<V:<N:CAD>CAD>`, with the `/:C+V` group `A --> B / C ___ D` and the `/:C+N` group
  `A --> E / C ___ D`, gives `CEDCBD`.

### 8.7 Other modifiers

| Modifier | Meaning |
|---|---|
| `/:~` (also `/~`) | weak match: matching counts as success even if nothing changes |
| `/:Raw` | skip implications after this rule |
| `/:$` | re-syllabify before applying |
| `/:T Tier` | tier-only rule (§6.5) |
| `/:@n` | date of the rule (§8.10) |
| `/%n` · `/%n:each` | stochastic: apply with probability n% — once per invocation (all foci or none) or per focus. Uses the seeded RNG (`!set Seed = n`, default 0) |
| `/???` · `/???:each` | optional: fork the derivation (§8.11) |
| `/" Name` | rule name, for traces, `!only`/`!skip`, and `$Rules.Name` references |
| `/::` | persistent (§8.9) |
| `/\` | line continuation (§3.3) |

Modifiers may be written in any order. `/*/:>` and `/:> /*` are the same. **[Δ]** In the notes,
the order of `/???` and `/*` mattered; here the scope comes from `:each` instead.

### 8.8 Rule groups

| Syntax | Kind | Semantics | Succeeds iff |
|---|---|---|---|
| `[[ … ]]` | sequence (weak conjunction) | apply every member in order | at least one member applied |
| `&&[[ … ]]` | strong conjunction | apply the members in order; at the first failure, undo the whole group | all members applied |
| `\|\|[[ … ]]` | disjunction | apply members in order until one applies | one applied |

- Modifiers written right after `[[`, on the same line, are **inherited** by every member. A
  member's own modifier of the same kind overrides the inherited one; contexts and filters are
  added together.
- Modifiers after the closing `]]` apply to the group *as a rule*. The ones allowed there are:
  - the modes `/:1` (meaning once through; the default for a group) and `/:*` (repeat the whole
    group until nothing changes);
  - `/:C±`, `/:C*`, `/:L±`, `/:D±`, `/:@`;
  - `/%`, `/???`, `/"`, `/:~`;
  - whole-form filters `/:i±` and `/:o±` (without `___`).

  Contexts, `/*`, `/:F` and `/:T` after `]]` are errors; put them at the start of the group
  instead.
- `$Name` on a line of its own inside Rules invokes a previously named `Rules` section or named
  group as a rule; this gives reusable rule blocks.
- The notes' `||[[ '[t] --> '[d] ... ]]` chain shift works: the first rule that applies wins.

### 8.9 Persistent rules
A rule marked `/::` is a *persistent* rule. It works like this:

1. It applies once where it is declared.
2. From then until the end of the enclosing group, including nested groups, it re-applies after
   every rule application that changes the form.
3. When several persistent rules are active, they run in declaration order, repeating until
   nothing changes. The iteration cap (§8.3) applies.
4. A change made by a persistent rule does not trigger the persistent rules again, except
   through that fixed-point loop.

Persistent syllabification (§5.7) is the same concept, built in.

### 8.10 Dates (wishlist)
- `!date n` inside Rules sets the date of the rules that follow; `/:@n` sets it for one rule.
  Dates are integers and may be negative.
- Dates MUST be non-decreasing in file order; a decrease is a load-time error.
- A record may carry a date (§10.2), for example a loanword `television 1954`. A rule applies to
  that record only if the rule's date is strictly greater than the record's date.
- Undated rules have date −∞, so they never apply to a dated record.
- The CLI options `--from A --to B` restrict the rules to dates in `[A, B]`. Undated rules are
  included unless `--from` is given.

### 8.11 Multiple outputs
- `$_` holds a list of **variants**, normally just one. Each variant carries a derivation
  label: the sequence of optional-rule decisions, for example `R12:yes`.
- Each rule applies to each variant.
- `/???` splits each variant in two: one where the rule applied, one where it did not.
  `/???:each` splits once per focus, into all 2ᵏ combinations.
- Identical variants are merged, and their labels are joined with `|`. **[Δ]** A label is a
  sequence of space-separated decision tokens (`R12:yes`, `Voicing:no`, a paradigm cell name).
  A later decision is appended to every `|`-alternative.
- `MaxVariants` (default 64) caps the number of variants; exceeding it is an error.
- Output prints every variant (§10.5).

## 9. Morphology: paradigms (wishlist)

```
$Noun := Paradigm [[
   Nom.Sg  : $_
   Gen.Sg  : $_ - [is]
   Nom.Pl  : $_ - [es]  /:L- {!Irregular}
   Dat.Pl  : <N: $_ > - [ibus]        %% brackets are allowed, for cyclic rules
]]
```

- Each cell is `Label : template`. The template is a sequence of `$_` (the stem), orthographic
  strings, and boundary or bracket symbols. It may carry `/:L±` or `/:D±` restrictions.
- **[Δ]** `!paradigm $Noun` inside a `Rules` block expands the current variant into one variant
  per cell, unless the record is tagged with another paradigm.
  - A record whose `paradigm` column names a paradigm is expanded by it before the script's
    statements run, if the script contains no `!paradigm` command.
  - `--paradigm Noun` tags every record that has no `paradigm` column value.
  - Each variant's label is its cell name.
- The following rules then apply to every cell. This lets a user derive every inflected form of
  a root in sequence.

## 10. Script runtime

### 10.1 Variables
- `$name := expr` assigns. `expr` is `$_`, another variable, an orthographic string `[...]`
  (parsed with the input orthography), or `$field[n]`.
- `$_` is the current form, as a list of variants. `$in` is the parsed input form. `$raw` is the
  raw input text.
- Record fields are `$field[1..]`, `$NF` and `$NR`. **[Δ]** The notes' awk-style `$1` would
  clash with rule back-references, so fields have their own names.
- The notes also write `$mid ::= $_`; `::=` is accepted as a synonym of `:=`.
- Assigning to `$_` replaces the current form.

### 10.2 Input records (lexicon)
Input is a text file, or stdin.

- **Format.** The default is TSV. `!set InputFormat = tsv|csv|lines|regex:<pattern>` selects
  another; with `regex`, named groups become fields.
- **Columns.** A header line `#! form gloss features date dialect` names the columns. Without a
  header, column 1 is the form and the other columns are fields with no special meaning.
- **Special columns:**
  - `form` — the phonological input, in the input orthography;
  - `features` — lexical features, `+Romance !N -Common` (space-separated `vName` items);
  - `date` — an integer (§8.10);
  - `dialect` — restricts the record to these dialects (§10.4);
  - `paradigm` — a paradigm name (§9).
- **Other lines.** Blank lines and lines starting with `%%` are skipped.

### 10.3 Choosing active definitions
- `!use $X` makes the definition `$X` active for its kind.
- `!orthography input $A output $B` sets the input and output orthographies separately. The
  default for both is the active orthography.
- `!syllabify [$S]` syllabifies the current form now.

### 10.4 Dialects
- `!dialects (A B C)` declares daughter languages. Each record is then processed once per
  dialect, unless its `dialect` column restricts it.
- Rules with `/:D±` apply only in the matching dialects.
- In output, each result is labelled with its dialect. Output can also be one column per
  dialect: `--wide`.

### 10.5 Commands

| Command | Effect |
|---|---|
| `!include "file"` | include a file; the path is relative to the including file; each file is included at most once. **[Δ]** `"lib:NAME"` names `NAME` in the standard library directory `yasc/lib/` (for example `!include "lib:ipa.yasc"`) |
| `!print "fmt" x1 … xn` | formatted output (see below) |
| `!date n` | set the current rule date (§8.10) |
| `!set Name = value` | runtime settings: `DefaultMode`, `MaxIterations`, `MaxVariants`, `Seed`, `OnUnparsable`, `EnforceConstraints`, `InputFormat`, `Trace` |
| `!use`, `!orthography`, `!syllabify`, `!dialects`, `!paradigm` | see above |
| `!associate`, `!ocp` | tier helpers (§5.5) |
| `!only Name…` · `!skip Name…` | run only these rules, or skip these rules, by name (for experiments) |
| `!assert "expected"` | compare the current output with the expected text; a mismatch is reported. Used by regression tests and by LLM-driven checks. |

`!print` format directives:

| Directive | Meaning |
|---|---|
| `%O{i}` | argument i in the output orthography |
| `%O[$Orth]{i}` | argument i in the given orthography |
| `%S{i}` | segment bundles, e.g. `{+Syll +High …}` |
| `%I{i}` | raw input text |
| `%F{i}` | a field |
| `%L{i}` | the variant label |
| `\n`, `\t`, `%%` | the usual escapes |

Arguments are numbered from 0.

If a script executes no `!print` for a record, the default output is `input<TAB>output` per
variant, with a label column added when variants or dialects exist.

## 11. Interfaces

### 11.1 CLI
```
python -m yasc SCRIPT [LEXICON ...] [-o OUT]
       [--trace] [--json] [--from A] [--to B] [--dialect D]
       [--only NAME ...] [--skip NAME ...] [--check] [--list-rules] [--word FORM ...]
```

- `--check` parses and compiles the script, reports every error, and exits.
- `--list-rules` prints each rule's number, name, date, source line and canonical text; it
  supports reordering and editing workflows.
- `--word` runs ad-hoc forms instead of a lexicon.
- `--trace` prints every rule application that changed a form, as
  `rule-id  name  line  before → after  (focus i..j)`.
- `--json` emits one JSON object per record:
  `{input, outputs:[{form, label, dialect, approximate:[...]}], trace:[...]}`.

### 11.2 Python API
```python
import yasc
sc = yasc.load("script.yasc")            # or yasc.loads(text)
res = sc.apply("pater", features="+N", date=None, dialect=None, trace=True)
res.outputs        # list[Variant]; str(variant) renders in the output orthography
res.trace          # list[TraceStep]
sc.rules           # inspectable rule tree, with a canonical .source for each rule
sc.phonology.segment("{+Syll +High}")    # build a segment; sc.orthography.render(seg)
```

### 11.3 Canonical printing
Every compiled object (rule, pattern, segment spec, feature declaration) has a `canonical()`
method. It returns a parseable string in which aliases are resolved, spacing is normalised and
modifiers are in a fixed order. Parsing that output MUST round-trip to an equal object, and the
test suite checks this.

## 12. Errors and diagnostics
- **Load-time errors** (`YascSyntaxError`, `YascDefinitionError`) carry the file, line and
  column, a caret excerpt, and a hint where possible. Loading reports all errors, up to 20, not
  just the first.
- **Run-time errors** (`YascRuntimeError`) name the record, the rule id and name, and the
  source line. The CLI reports the error and continues with the next record; exit status 2
  means some records failed.
- **Warnings** — renderings that are only approximate, no-op writes to syllable features, and
  undated rules alongside dated records — go to stderr. `--json` includes them.
- **Determinism.** The same inputs, script and seed MUST produce byte-identical output.

## Appendix A — Deviations from the notes (summary)

| # | Notes | Specification | Reason |
|---|---|---|---|
| A1 | `#` as a comment | `%%` comments; `# ` only at the start of a line | clashes with the word boundary |
| A2 | boundaries as elements of the string | boundary marks in gaps; zero-width assertions | "ignored unless addressed" without special cases |
| A3 | default: apply once | default: simultaneous; `/:1` = once; `!set DefaultMode` | usual expectation |
| A4 | `$n` in context before the locus is illegal | legal (LHS matched first) | removes a restriction |
| A5 | `{}'$1` numbering | `$n` = top-level LHS items | clearer regex-like numbering |
| A6 | op lists applied left to right | composition order | readability |
| A7 | `PhoneSeparator == [.]` | `SyllableSeparator` added; no default phone separator | X-SAMPA `.` |
| A8 | `[#\']` | no escapes inside `[...]` | X-SAMPA uses `\` |
| A9 | `...` in value and op lists | elision only; must be written in full | ambiguity |
| A10 | `/???` scope depends on modifier order | order-free; `:each` suffix | predictability |
| A11 | awk `$1` fields | `$field[1]` | clash with back-references |
| A12 | tone as a plain many-valued feature | Tier features with a segment view | autosegmental phenomena |
| A13 | `(a)High <--(-)--> (a)Low` example | kept as syntax; example changed | phonologically wrong |
| A14 | `ret.py` "skip garbage" in input | error by default (`OnUnparsable`) | silent data loss |
| A15 | stress as a segment feature | `Scope(Syllable)` features on the syllable tier | wishlist: stress marks |
| A16 | syllable features across re-syllabification: unspecified | a new syllable starts unspecified; a recomputed one inherits through its nucleus (§5.4) | notes.md §4 S1, S4: stress survives `/:$` and `Persistent` |
| A17 | a syllable that loses its nucleus leaves its segments unsyllabified | the orphans are re-attached with the templates, onset first (§5.4) | notes.md §4 S2: stress marks stay placeable (CĪVITĀTEM, SENIŌREM) |
| A18 | "maximal runs" of `Nucleus`; `NucleusPreference` for every VV | each `Nucleus` match is a nucleus (hiatus); `NucleusPreference` only where a margin parse exists (§5.7) | notes.md §4 S3: Latin FĪ.LI.UM; Phonix *bui* |
| A19 | the coda remainder matches `Coda` or is dropped; canon scores left open | the longest matching prefix is the coda; canon `r` of `m` scores `m − r`, fewest unsyllabified first (§5.7) | deterministic, graceful partial parses |
| A20 | tier behaviour left open | levels and anchoring; a floating tone belongs to the preceding word; the OCP after every change; association word by word; links follow moved and copied segments (§5.5, §6.5) | P8: deterministic autosegmental behaviour |
| A21 | `/:C±`: "inside or outside brackets" | a focus's domain is its innermost bracket, and the domain acts as a form of its own (§8.6) | P9: exact, and composes with `/:C*` |
| A22 | constraints as whole-form `/:o-` filters | anchored at the rewritten span (§4.6) | P9: otherwise a violation already present would freeze the word |
| A23 | `--paradigm` for tagged records | `!paradigm` skips records tagged with another paradigm; tagged records expand automatically when the script has no `!paradigm` (§9) | P9: one script can hold several paradigms |
| A24 | `include` of files by path only | `!include "lib:NAME"` reads the standard library (§10.5) | P10: scripts share `lib:ipa.yasc` from any directory |
| A25 | RHS `[x]` left open; the notes write `'[t] --> '[d]` | `[x]` replaces the segment; `+[x]` merges x's declared features (§8.2.4) | "becomes this sound" is the usual intent; merging made hybrid segments |

## Appendix B — The example script, revised

```
$P := Phonology [[
  Syll Binary
  Voice Binary
  Nasal Binary
  Place Node(Labial Coronal Dorsal)
  Labial Unary
  Coronal Unary
  Dorsal Unary
  High Binary
    == Hi hi
  Low Binary
  {+High} --> {-Low}
  {+Low} --> {-High}
  Stress Scalar(0,2) Scope(Syllable)
    (<Max>) 2 2 2
  Tone [H] [L] [M] [HL] [LH] [HM] [MH] Tier(TBU={+Syll}, Stray=float)
  {+Syll} ~~> {+Voice}
  {+Nasal} --> {+Voice}
]]

$O := Orthography [[
  [p t k] {-Syll -Voice -Nasal}
  [b d g] {-Syll +Voice -Nasal}
  [m n]   {-Syll +Nasal}
  [p b m] {!Labial}
  [t d n] {!Coronal}
  [k g]   {!Dorsal}
  [i u] {+Syll +High}
  [a]   {+Syll +Low}
  {-Voice} ==> [#_0]
  {[H]Tone} ==> [#']
  {[L]Tone} ==> [#`]
  MorphemeSeparator == [+]
  SyllableSeparator == [.]
  SyllableMark {2Stress} == ["]
]]

Syllabification [[
  Onset (C)
  Nucleus V
  Coda (C)
]]

V === {+Syll}
C === {-Syll}

$R := Rules [[
  $input := $_
  !date 100
  V --> 0 / ___#  /! #C*___                  %% final V loss except in monosyllables
  {+Nasal} --> {(p)Place} / ___ C:{(p)Place} %% nasal place assimilation
  !date 400
  V --> {+Nasal} / {+Nasal} ___  /*          %% rightward nasal spreading
  V^0 --> V^=h / V^*=h C* ___  /*            %% tone spreading onto toneless vowels
  ||[[
    '[t] --> '[d] / #___
    '[d] --> '[t] / #___
  ]]
  !print "%O{0} > %O{1}\n" $input $_
]]
```

