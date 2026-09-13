# YASC User Manual

*Yet Another Sound Changer*: sound changes written the way phonologists write them, over
distinctive features, in pure Python 3. This manual is for historical linguists and
conlangers. Part I is a tutorial; Part II is a reference that points to the
[specification](specification.md) for the fine print. It supersedes the unfinished
`orig-notes/Yasc-Manual.tex`.

## Contents

- [0. About this manual](#0-about-this-manual)
- **Part I — Tutorial**
  - [1. Quick start](#1-quick-start)
  - [2. How YASC thinks about forms](#2-how-yasc-thinks-about-forms)
  - [3. Writing your own feature system](#3-writing-your-own-feature-system)
- **Part II — Reference**
  - [4. Phonology](#4-phonology)
  - [5. Orthography](#5-orthography)
  - [6. Syllabification](#6-syllabification)
  - [7. Patterns](#7-patterns)
  - [8. Rules and modifiers](#8-rules-and-modifiers)
  - [9. Groups and persistent rules](#9-groups-and-persistent-rules)
  - [10. Dates](#10-dates)
  - [11. Variants, dialects and paradigms](#11-variants-dialects-and-paradigms)
  - [12. Tone tiers](#12-tone-tiers)
  - [13. Running scripts](#13-running-scripts)
  - [14. The Python API](#14-the-python-api)
  - [15. The standard library](#15-the-standard-library)
  - [16. The tools](#16-the-tools)
  - [17. Troubleshooting and common pitfalls](#17-troubleshooting-and-common-pitfalls)

## 0. About this manual

Every block marked `yasc` below is a **complete script** that the test suite
(`tests/test_docs.py`) compiles strictly: no errors and no warnings. A comment line of the form

```text
<!-- run: pataka -> padak; pa -> pa -->
```

directly before a block also runs the script on each input and checks the output (several
variants are joined with ` | `; `input [!N]` passes lexical features). The comments are
invisible when the Markdown is rendered, so the expected outputs are also written next to
each block. Blocks marked `text` are fragments or command lines.

---

# Part I — Tutorial

## 1. Quick start

YASC needs only Python 3.9 or later. There is nothing to install: run it from the project
directory as `python3 -m yasc`. Save this as `first.yasc`:

<!-- run: pataka -> padak; Sapa -> ʃap; pa -> pa; ak_ha -> akʰ -->
```yasc
!include "lib:ipa.yasc"                  %% standard features, X-SAMPA and IPA
!orthography input $XSAMPA output $IPA   %% type X-SAMPA, read IPA

V === {+Syll}
C === {-Syll}

Rules [[
  {-Son} --> {+Voice} / V ___ V          /" Voicing
  V --> 0 / ___ #  /! # C* ___           /" Apocope
  {-Son} --> {-Voice} / ___ #            /" Devoicing
]]
```

and run it on a few words:

```text
$ python3 -m yasc first.yasc --word pataka Sapa pa
pataka	padak
Sapa	ʃap
pa	pa
```

Read the rules as a phonologist would:

1. **Voicing**: an obstruent (`{-Son}`) becomes voiced between vowels. `V` is a *macro*
   defined with `===`; `___` marks where the changing segment sits.
2. **Apocope**: a word-final vowel (`___ #`) is deleted (`--> 0`), except (`/!`) when only
   consonants precede it in the word (`# C* ___`), so monosyllables keep their vowel.
3. **Devoicing**: an obstruent that has become word-final is devoiced.

The rules apply in order, each to the output of the one before: *pataka* → *padaga* →
*padag* → *padak*. Add `--trace` to watch it happen:

```text
$ python3 -m yasc first.yasc --word pataka --trace
== pataka
  1  Voicing  8  pataka → padaga  (focus 2..3, 4..5)
  2  Apocope  9  padaga → padag  (focus 5..6)
  3  Devoicing  10  padag → padak  (focus 4..5)
pataka	padak
```

Each trace line gives the rule number, its name (from `/"`), its source line, the form
before and after, and the segment positions it rewrote. *Voicing* rewrote two places at once:
by default a rule applies **simultaneously** everywhere it matches in its input (§8.3).

A lexicon is a tab-separated file, one word per line; `python3 -m yasc first.yasc words.tsv`
prints `input<TAB>output` for each line. Section 13 covers the options.

## 2. How YASC thinks about forms

A **form** is what rules rewrite: the current word, or phrase, of the record being processed.
It has up to four layers (spec §5.1):

1. **Segments.** Each segment is a feature bundle, such as `{-Syll +Cons -Son -Voice ...}`.
   Letters exist only in the orthography, which translates text into bundles on input and
   bundles back into text on output.
2. **Gaps and boundaries.** Between two segments, and at each edge, is a *gap*. Boundaries
   live in gaps, not in the segment string: syllable `.`, morpheme `-` (typed `+`), clitic
   `=`, word `#` and phrase `##`. A stronger boundary implies the weaker ones, so a word
   boundary is also a morpheme and syllable boundary (spec §5.2).
3. **Brackets.** Labelled spans such as `<N: ... >` for cyclic, category-sensitive rules
   (§8.6 of the spec; §11 here).
4. **Tiers.** A syllable tier (built by a `Syllabification`) and autosegmental tiers such as
   tone (§6, §12).

Because boundaries sit in gaps, a pattern **skips** them unless it names them. `V ___ V`
matches across a morpheme boundary; `V - ___` requires one:

<!-- run: ta+pa -> ta+ba; tapa -> tapa; ta+pa#pa -> ta+ba ba -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  [p] --> [b] / {+Syll} - ___            %% only right after a morpheme boundary
]]
```

`ta+pa` becomes `ta+ba` and `tapa` is unchanged. In `ta+pa#pa`, the second `p` also follows
a word boundary, which implies a morpheme boundary, so it changes too. In the output a word
boundary inside the form is written as a space.

**Matching a segment.** A spec in braces, `{-Son +Voice}`, matches any segment that has
those values; features you do not mention are ignored. An orthographic string `[p]` matches
segments with the features of *p*, so with a rich feature system `[p]` matches exactly *p*.
On the right-hand side, a spec *changes* only the features it mentions, so `{+Voice}` turns
*p* into *b*, *s* into *z*, and *a* into itself.

**Unspecified values.** A feature can also be *unspecified* on a segment (written `_F`).
Patterns treat `_F` as a value of its own: `{-Round}` does not match a segment whose Round is
unspecified (§4.2 below; spec §4.2).

## 3. Writing your own feature system

The standard library is convenient, but a script can define exactly the features it needs.
Three sections make a script: a `Phonology` (the features), an `Orthography` (letters ↔
bundles) and `Rules`. This is a cut-down version of `examples/simple/grimm.yasc`:

<!-- run: pater -> faθer; dekan -> texan; bʰrater -> βraθer; gʰostis -> ɣostis; genu -> kenu -->
```yasc
$P := Phonology [[
  Syll Binary
  Son Binary
  Cont Binary
  Voice Binary
  Asp Binary                 %% aspiration: bʰ dʰ gʰ
  Strident Binary            %% s (vs θ)
  Place Node(Labial Coronal Dorsal)
  Labial Unary
  Coronal Unary
  Dorsal Unary
  High Binary
  Low Binary
  Back Binary
  {+Syll} --> {+Son +Cont +Voice -Asp}
]]

$O := Orthography [[
  [p t k b d g bʰ dʰ gʰ f θ x β ð ɣ s] {-Syll -Son}
  [p t k b d g bʰ dʰ gʰ]  {-Cont}
  [f θ x β ð ɣ s]         {+Cont}
  [p t k f θ x s]         {-Voice -Asp}
  [b d g β ð ɣ]           {+Voice -Asp}
  [bʰ dʰ gʰ]              {+Voice +Asp}
  [s]                     {+Strident}
  [p t k b d g bʰ dʰ gʰ f θ x β ð ɣ] {-Strident}
  [m n r]                 {-Syll +Son +Voice -Asp}
  [m n]                   {-Cont}
  [r]                     {+Cont}
  [p b bʰ f β m]          {!Labial}
  [t d dʰ θ ð s n r]      {!Coronal}
  [k g gʰ x ɣ]            {!Dorsal}
  [a e i o u]             {+Syll}
  [i u]                   {+High -Low}
  [e o]                   {-High -Low}
  [a]                     {-High +Low}
  [i e]                   {-Back}
  [a o u]                 {+Back}
]]

Rules [[
  {-Son -Cont -Voice} --> {+Cont} /! [s] ___       /" Grimm1
  {-Son -Cont +Voice -Asp} --> {-Voice}            /" Grimm2
  {+Asp} --> {-Asp +Cont}                          /" Grimm3
]]
```

*pater* → *faθer*, *dekan* → *texan*, *bʰrater* → *βraθer*, *gʰostis* → *ɣostis* (the *t*
after *s* is protected by `/! [s] ___`), *genu* → *kenu*.

- **The Phonology** declares each feature with its type. `Binary` features have the values
  `+` and `-`; `Unary` features are either present (`!Labial`) or absent. `Place` is a
  geometry **node** over three daughters: a segment "has Place" whenever one of them is
  specified. The last line is an **implication**: every vowel is sonorant, continuant, voiced
  and unaspirated, whatever the orthography or a rule says.
- **The Orthography** is additive: each line gives some graphemes some features, and a
  grapheme's bundle is the sum of all its lines. Grouping by natural class keeps the table
  short and makes gaps obvious. Two graphemes with the same bundle would be indistinguishable,
  so give every letter at least one distinguishing value.
- **The Rules** are ordered. *Grimm2* follows *Grimm1*, so the new *p* from *b* does not
  become *f*: the rules are in a **counter-feeding** order, as the history requires.

The orthography also decides the output. A segment that no grapheme matches exactly is
rendered with the nearest grapheme plus diacritics, and flagged as *approximate* in `--json`
output (§5).

`python3 -m yasc.tools.make_ortho` writes an orthography like this one for any list of
X-SAMPA phones, from the standard library's bundles (§16).

---

# Part II — Reference

## 4. Phonology

`[$Name :=] Phonology [[ ... ]]` (or `Phonetics`) declares features and implications
(spec §4). The most recent Phonology is the active one.

### 4.1 Feature types

| Declaration | Values | Built-in operations |
|---|---|---|
| `High Binary` | `+ -` | `(-)` swaps them |
| `Labial Unary` (or `Labial !`) | `!` (present); otherwise unspecified | — |
| `Height Scalar(1,3)` (default bounds 0..9) | `1 2 3` | `(++)` and `(--)`, saturating |
| `Place [lab] [cor] [dor]` | named values | — |
| `High + -` | any listed value tokens | — |
| `Place Node(Labial Coronal Dorsal)` | a class node: no values of its own | — |

Indented lines after a feature add to it:

- `== Hi hi high` gives **aliases**, accepted everywhere the name is;
- `(op) r1 r2 ... rN` defines an **operation**, one result per value in order; `_` means
  "undefined here". An op is applied in a spec as `op(a)F`: `++(h)Height`, `-(a)Voice`.
  Several ops compose right to left: `op1#op2(a)` is op1(op2(a)) (spec §4.4).
- the modifiers `Scope(Syllable)` (a syllable feature, §6) and `Tier(...)` (§12) follow the
  type on the same line.

A Scalar feature and an enumerated one at work. Every vowel rises one step (`++` saturates at
3), and *k* becomes *t* before a front vowel. Rules apply one after the other, so *Fronting*
sees the raised vowels:

<!-- run: pate -> poti; tupa -> tupo; kiku -> tiku; kape -> kopi -->
```yasc
$P := Phonology [[
  Syll Binary
  Height Scalar(1,3)
  Back Binary
  Place [lab] [cor] [dor]
]]
$O := Orthography [[
  [p t k] {-Syll}
  [p] {[lab]Place}
  [t] {[cor]Place}
  [k] {[dor]Place}
  [a]   {+Syll 1Height +Back}
  [e o] {+Syll 2Height}
  [i u] {+Syll 3Height}
  [e i] {-Back}
  [o u] {+Back}
]]
Rules [[
  {+Syll (h)Height} --> {++(h)Height}      /" Raising
  [k] --> [t] / ___ {+Syll -Back}          /" Fronting
]]
```

`(h)Height` binds the variable `h` to the vowel's height, and `++(h)Height` writes the next
value up. Variables work on every feature type; see §7.2.

### 4.2 Unspecified values

Any feature can be unspecified on a segment, written `_F`. `{_Voice}` matches only segments
with no Voice value; on the right-hand side `{_Voice}` removes the value. Unspecification is
what makes weak implications (below) and the weak rewrite `~{...}` (§8.2) useful.

### 4.3 Feature geometry

`Place Node(Labial Coronal Dorsal)` makes Place a class node (spec §4.3). Nodes store nothing:
`!Place` holds when some daughter is specified, and `_Place` when none is. Nodes can nest
(`Labial Node(Round Labiodental)`). A variable on a node binds the whole sub-bundle, which is
how place assimilation is written:

<!-- run: anpa -> ampa; inka -> iŋka; aNta -> anta; amla -> anla -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  {+Nasal +Cons} --> {(p)Place} / ___ {-Syll (p)Place}      /" NasalPlace
]]
```

The nasal takes the following consonant's entire Place sub-bundle: its old Coronal values
are removed, not merged. On the right-hand side `{_Place}` would delink the whole subtree.

### 4.4 Implications

Implications are redundancy rules that YASC maintains automatically (spec §4.5).

| Syntax | Meaning |
|---|---|
| `S --> T` | strong: a segment matching S gets T's values |
| `S ~~> T` | weak (default): fills only T's features that are unspecified |
| `S <--> T` | both directions |
| `S <--(op)--> T` | both directions, with `op` applied to variable values in the targets |
| `S <~~--> T`, `S <--~~> T`, ... | mixed: the left half is T → S, the right half S → T |

They run on segments produced by the orthography and after every rule that changes a segment
(not after `/:Raw` rules), in declaration order, until nothing changes. A cycle that never
settles is an error.

**When an implication fires.** An implication re-runs on a segment only when a feature its
*trigger* reads has changed. This matters for "correcting" implications. The Latin → Spanish
example lengthens and tenses in one rule, `{+Syll (a)Long} --> {(a)ATR -Long}`, and relies on
`{+Low} --> {-ATR}` to keep *a* lax. But that rule changes ATR, not Low, so `{+Low} --> {-ATR}`
does not re-fire, and long *ā* comes out as a tense low vowel (the renderer flags it as
approximate). The fix is an implication whose trigger reads ATR:

<!-- run: ta:t -> tat; tE:t -> tet; tet -> tEt -->
```yasc
$P := Phonology [[
  Syll Binary
  Low Binary
  ATR Binary
  Long Binary
  {+Low} --> {-ATR}
  {+Low +ATR} --> {-ATR}      %% re-fires when a rule sets +ATR on a low vowel
  {} ~~> {-Long}
]]
$O := Orthography [[
  [t] {-Syll}
  [a] {+Syll +Low}
  [e] {+Syll -Low +ATR}
  [E] {+Syll -Low -ATR}
  {+Long} ==> [#:]
]]
Rules [[
  {+Syll (a)Long} --> {(a)ATR -Long}      /" QualityForQuantity
]]
```

Without the second implication, `ta:t` still prints `tat`, but with the warning
`approximate rendering of segment 1 (a) in 'tat': ATR is +, rendered -`.

Two more cautions. `{(a)High} <--(-)--> {(a)Low}` is legal but says that −High implies +Low;
write `{+High} --> {-Low}` and `{+Low} --> {-High}` instead (spec §4.5). And implications may
not mention syllable features (§6).

### 4.5 Constraints

A Phonology line `Constraint * {-Syll +Voice}{-Syll -Voice}` names a sequence the language
forbids (spec §4.6). Constraints do nothing until `!set EnforceConstraints = on`; then a rule
output that creates a violation in the rewritten span is blocked, and the rule leaves that
spot alone. `Script.violations(text)` lists violations from Python (§14). Constraints belong to
a Phonology section, so a script that uses the standard library's phonology cannot add any.

## 5. Orthography

`[$Name :=] Orthography [[ ... ]]` maps graphemes to feature bundles and back (spec §5.6).

| Line | Meaning |
|---|---|
| `[p t k] {-Voice}` | these graphemes have (at least) these features; lines add up |
| `{+Long} ==> [#:]` | a diacritic: `#` stands for the base, so `[#:]` follows it, `[ˈ#]` precedes it, `[(#)]` surrounds it |
| `*{Long ATR}` | features ignored when rendering (a broad transcription) |
| `MorphemeSeparator == [+]` | how a boundary is written (also `SyllableSeparator`, `CliticSeparator`, `WordSeparator`, `PhraseSeparator`, `PhoneSeparator`); `[]` disables one |
| `BracketOpen == [<]`, `BracketLabelEnd == [:]`, `BracketClose == [>]` | category brackets such as `<N:...>` |
| `SyllableMark {2Stress} == [']` | a mark written before a syllable, setting a syllable feature (§6) |
| `FloatingPrefix == [^]` | how floating tones are written (§12) |

A grapheme is exactly the text between spaces inside `[...]`; nothing is escaped there, so
`[J\ t_s]` declares X-SAMPA `J\` and `t_s`. Parsing picks, at each point, the reading that
covers the most text with the fewest pieces (so `ts` is one grapheme when declared, and `t` +
`s` otherwise). Rendering uses an exact grapheme when one exists; otherwise it takes the
nearest grapheme and adds diacritics, and a leftover difference is reported as
*approximate* (in `--json` output and as a warning).

A script may define several orthographies. The last one defined is active for input and
output; `!orthography input $A output $B` separates them, and `[...]` in patterns is read
with the input orthography. Here a broad transcription goes in and a romanization comes out,
with a combining macron (U+0304) for length:

<!-- run: kama -> kāma; tuNa -> tūnga; pa:ti -> pāti -->
```yasc
$P := Phonology [[
  Syll Binary
  Voice Binary
  Nasal Binary
  High Binary
  Back Binary
  Long Binary
  Place [lab] [cor] [dor]
  {} ~~> {-Long}
]]
$Broad := Orthography [[
  [p t k] {-Syll -Voice -Nasal}
  [m n N] {-Syll +Voice +Nasal}
  [p m] {[lab]Place}
  [t n] {[cor]Place}
  [k N] {[dor]Place}
  [a] {+Syll -High +Back}
  [i] {+Syll +High -Back}
  [u] {+Syll +High +Back}
  {+Long} ==> [#:]
]]
$Rom := Orthography [[
  [p t k] {-Syll -Voice -Nasal}
  [m n ng] {-Syll +Voice +Nasal}
  [p m] {[lab]Place}
  [t n] {[cor]Place}
  [k ng] {[dor]Place}
  [a] {+Syll -High +Back}
  [i] {+Syll +High -Back}
  [u] {+Syll +High +Back}
  {+Long} ==> [#̄]
]]
!orthography input $Broad output $Rom
Rules [[
  {+Syll} --> {+Long} / ___ {+Nasal}         /" Lengthening
]]
```

Input that cannot be parsed is an error, reported with its column. `!set OnUnparsable = skip`
drops such characters and `keep` keeps them as opaque segments that render verbatim. Inside
`!print`, `%O[$Rom]{1}` renders an argument in a named orthography (§13.4).

**Defaults.** Separators are syllable `.`, morpheme `+`, clitic `=`, word `#` (and space),
phrase `##`; there is no phone separator. When a phone separator is declared, it is written
only between two segments that would otherwise be read differently. The standard library's
X-SAMPA orthography changes some of these (§15).

## 6. Syllabification

A `Syllabification` section builds a syllable tier (spec §5.4, §5.7):

```text
[$Name :=] Syllabification [[
  Onset    << C | {-Son}{+Son -Syll} >>   %% templates: patterns without boundaries
  Nucleus  V
  Coda     (C)
  OnsetRequired      no      %% yes: V-initial syllables only word-initially
  Algorithm          MaxOnset   %% or Canon, with  Canons  CV > CVC > V
  NucleusPreference  first   %% in VV, which vowel is the nucleus
  Persistent         no      %% yes: re-syllabify after every change
  Domain             word    %% or phrase
]]
```

`MaxOnset` finds the nuclei, gives each the longest onset the template allows, and puts the
rest in the previous coda. `Canon` chooses the best parse into the listed canons. Syllables
are built by the command `!syllabify` (or the rule modifier `/:$`, or `Persistent yes`), and
then maintained as rules delete and insert segments.

Once the tier exists, rules can see it:

- the **role pseudo-features** `SylOnset`, `SylNucleus`, `SylCoda` and `Syllabified`;
- the pattern symbol `.`, which matches any gap at a syllable edge;
- **syllable features**, declared `Scope(Syllable)`: a segment sees its syllable's value, and
  writing the feature on a segment writes it to the syllable. A `SyllableMark` in the
  orthography writes such a feature as a mark before the syllable.

Coda devoicing and penultimate stress:

<!-- run: abad -> 'abat; badan -> 'badan; bugdi -> 'bukdi; lab -> 'lap; ab.lu -> 'ap.lu -->
```yasc
$P := Phonology [[
  Syll Binary
  Son Binary
  Voice Binary
  High Binary
  Back Binary
  Place [lab] [cor] [dor]
  Stress Scalar(0,2) Scope(Syllable)
]]
$O := Orthography [[
  [p t k b d g] {-Syll -Son}
  [p t k] {-Voice}
  [b d g m n l] {+Voice}
  [m n l] {-Syll +Son}
  [p b m] {[lab]Place}
  [t d n l] {[cor]Place}
  [k g] {[dor]Place}
  [m n] {-High}
  [l] {+High}
  [a] {+Syll +Son +Voice -High +Back}
  [i] {+Syll +Son +Voice +High -Back}
  [u] {+Syll +Son +Voice +High +Back}
  SyllableMark {2Stress} == [']
]]
Syllabification [[
  Onset (C)
  Nucleus V
  Coda (C)
]]
V === {+Syll}
C === {-Syll}
Rules [[
  !syllabify
  {-Son SylCoda} --> {-Voice}                  /" CodaDevoicing
  V --> {2Stress} / ___ C* V C* #              /" Penult
  V --> {2Stress} / # C* ___ C* #              /" Monosyllable
]]
```

`!syllabify` is required: without it no syllable tier exists, `SylCoda` matches nothing and
stress has nowhere to go. An explicit `.` in the input (as in `ab.lu`) is a fixed syllable
boundary. The Latin → Spanish example derives stress from syllable weight the same way, and
its `notes.md` discusses what happens to syllables when rules delete vowels (spec §5.4).

## 7. Patterns

Patterns are regular expressions over forms, used in left-hand sides, contexts, filters,
macros, syllable templates and constraints (spec §6).

| Pattern | Matches |
|---|---|
| `{-Son +Voice}` | one segment with these values (§7.1) |
| `[ts]` | the segments that `ts` parses to in the input orthography; `[p t k]` is a class |
| `'S` | `S` with *every* feature identical, unspecified ones included |
| `V:{-High}` | one segment matching both, here a macro refined by a spec |
| `Name` | a macro defined with `Name === pattern` |
| `0` | nothing |
| `.` `-` `=` `#` `##` | a syllable, morpheme, clitic, word or phrase boundary (zero width) |
| `<:N` `>:N` | the opening or closing of an N bracket |
| `<< P \| Q >>` | P or Q, tried in order; the index of the one that matched is remembered |
| `(P)`, `P*`, `P+` | optional, zero or more, one or more |
| `...` | any run of segments |
| `___` | the position of the focus, in contexts |
| `$n` | a copy of what LHS item *n* matched |
| `^[H]`, `V^[H]` | autosegments (§12) |

A pattern steps over boundaries unless it names them, and `...` never crosses a gap it is
told to test. A bare identifier in a pattern is always a macro, since feature names appear
only inside braces.

### 7.1 Segment specs

| Constraint | Matches | On the right-hand side |
|---|---|---|
| `+F`, `!F`, `2F`, `[H]F` | F has that value | set F |
| `_F` | F is unspecified | unset F (a Node: delink its subtree) |
| `{+ -}F` | F is one of these values | (error) |
| `>1F`, `<=2F` | Scalar comparison | (error) |
| `(a)F` | F is specified; bind `a` or check it | set F to `a` |
| `(?a)F` | anything; bind `a`, even to "unspecified" | copy F exactly |
| `op(a)F` | F = op(a) | set F to op(a) |
| `F` | shorthand for `!F` on a Unary feature | |

### 7.2 Variables (alpha notation)

A variable is shared by the whole rule: left-hand side, contexts, filters and right-hand side
(spec §6.2). Vowel harmony copies three features from the vowel before:

<!-- run: kitopu -> kitepi; kutipe -> kutupo -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  V --> {(b)Back (f)Front (r)Round} / V:{(b)Back (f)Front (r)Round} ___  /:F- V  /*   /" Harmony
]]
```

`/:F- V` hides everything that is not a vowel, so the consonants in between do not count
(§8.5), and `/*` lets each changed vowel trigger the next (§8.3).

### 7.3 Macros and orthographic classes

`V === {+Syll}` defines a macro; macros may use earlier macros, and `Name:{spec}` refines one.
A macro whose body is a disjunction keeps its alternatives' indices, which gives
**class correspondence**, the SCA idiom: when a left-hand item and its right-hand partner are
disjunctions of the same length, the *n*th alternative becomes the *n*th (spec §8.2.4).

<!-- run: pater -> faθer; stoka -> stoxa -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Stop === <<[p]|[t]|[k]>>
Fric === <<[f]|[T]|[x]>>
Rules [[
  Stop --> Fric /! [s] ___                /" Grimm1
]]
```

### 7.4 Disjunctions

`<< [i] | [e] >>` matches either. Here *k* becomes *tʃ* before a front vowel; `[t_S]` on the
right-hand side replaces the segment (§8.1):

<!-- run: kika -> t͡ʃika; keka -> t͡ʃeka; kuka -> kuka -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  [k] --> [t_S] / ___ << [i] | [e] >>      /" Palatalization
]]
```

### 7.5 Anything: `...`

`...` matches any run of segments, which makes long-distance rules easy. Latin *peregrinus*
dissimilated to *pelegrinus*: an *r* becomes *l* when another *r* follows later in the word.

<!-- run: peregrinus -> pelegrinus; rarara -> lalara -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  [r] --> [l] / ___ ... [r]                /" Dissimilation
]]
```

### 7.6 Back-references

`$1`, `$2`, … number the top-level items of the left-hand side (spec §6.4). On the right
they insert copies, which is how metathesis and gemination are written:

<!-- run: akra -> arka; pasla -> palsa -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
C === {-Syll}
Rules [[
  C {-Syll +Son} --> $2 $1 / V ___ V       /" Metathesis
]]
```

<!-- run: satja -> sattja; lagja -> laggja -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
C === {-Syll}
Rules [[
  C --> $1 $1 / V ___ [j]                  /" Gemination
]]
```

In a left-hand side or context, `$n` matches an exact copy: `{+Syll} $1` is a doubled vowel.

## 8. Rules and modifiers

```text
LHS --> RHS  / C ___ D  /! E ___ F  /:modifier ...  /" Name
```

The left-hand side is a pattern (or `0` for insertion); the right-hand side is a sequence of
items (or `0` for deletion). Each `/` context must hold and no `/!` context may hold; several
`/` contexts are all required (write `<< | >>` inside one context for "or"). The left context
is matched backward from the focus and the right context forward, so both may use variables
and back-references bound by the left-hand side (spec §8.1–§8.2).

### 8.1 What the right-hand side does

Right-hand items are aligned with the matched segments one by one (spec §8.2.4):

| Item | Effect on its segment |
|---|---|
| `{+Voice}` | changes only the features it mentions |
| `~{+Voice}` | fills only features that are unspecified |
| `[b]` (or `'[b]`) | **replaces** the segment with *b* (its features, implications applied) |
| `+[b]` | **merges** b's declared features onto the segment; others are kept |
| `'{...}` | **replaces** the segment with the spec alone |
| `$n` | a copy of what LHS item *n* matched |
| `0` (the whole RHS) | deletes the match |

Extra left-hand segments are deleted; extra right-hand items are inserted after the last
matched segment. An inserted spec builds a new segment from the spec alone, and implications
fill in the rest.

**`[x]` replaces; `+[x]` merges.** `[k] --> [t_S]` means "k becomes tʃ" (§7.4). Write
`+[x]` when only x's declared features should change and the segment's other features must
survive. In the Latin → Spanish example, Lenition is `VoicelessStop --> +VoicedStop`
(`+` also works on a macro or a `<< | >>`). The non-strict `[t]` there also matches the
affricate *ts*, which must become *dz*, not plain *d*. Be careful with a rich feature system:
`[k] --> +[t_S]` keeps *k*'s Dorsal values, and adds *tʃ*'s, which gives a hybrid segment
that renders approximately.

**Insertion** uses `0` on the left. A word-initial *e* before *s* + consonant (Spanish
*estar*):

<!-- run: stala -> estala; spata -> espata; sala -> sala -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
C === {-Syll}
Rules [[
  0 --> [e] / # ___ [s] C                  /" Prothesis
]]
```

### 8.2 Application modes and direction

| Modifier | Mode |
|---|---|
| (none) | **simultaneous**: find every match in the input form, rewrite them all at once; contexts see the input |
| `/:1` | once: the first match only |
| `/*` | iterative: rewrite a match, then search again from there in the *changed* form (spreading) |
| `/:*` | repeat the rule until the form stops changing |
| `/:>`, `/:<` | left to right (default) or right to left |

The simultaneous default is what historical linguists usually mean: `p --> f` changes every
*p*. It also means a rule does not feed itself. Compare the same rule in the two modes on
*petaka*:

<!-- run: petaka -> peteke -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
C === {-Syll}
Rules [[
  [a] --> [e] / [e] C ___  /*              /" Spread
]]
```

With `/*` the change spreads (*petaka* → *peteke*). Without it the second *a* still has an
*a* before it in the input, so only the first changes (*petaka* → *peteka*). `/:1 /:<`
changes only the last *a* (*tata* → *tato*). `/:*` repeats to a fixed point:

<!-- run: baaaa -> ba -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  V V --> $1  /:*                          /" Degemination
]]
```

`!set DefaultMode = once` makes `/:1` the default for scripts written that way. `/*` and
`/:*` stop with an error after `MaxIterations` (1000) steps or when a form repeats.

### 8.3 Filters

Filters test the whole form before (`/:i+ E`, `/:i- E`) or after (`/:o+ E`, `/:o- E`) the
rule; with `___` inside, `E` is anchored on the rewritten span (spec §8.4). Final vowel loss,
unless no vowel would be left:

<!-- run: pata -> pat; pa -> pa -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  V --> 0 / ___ #  /:o+ V                  /" Apocope
]]
```

The spec shows three equivalent ways to write this rule, and where they differ on phrases:
a context holds word by word, a filter without `___` looks at the whole form.

### 8.4 Visibility

`/:F- S` hides every segment that does not match S; `/:F+ S` hides the ones that do. Hidden
segments are skipped like gaps and never rewritten, so `V --> ... / V ___ /:F- V` sees only
vowels, as in the harmony rule of §7.2 (spec §8.5).

### 8.5 Other modifiers

| Modifier | Meaning |
|---|---|
| `/" Name` | the rule's name, for traces, `--only`/`--skip` and `$Name` |
| `/:L+ {!N}`, `/:L- {...}` | only for records whose lexical features match (or do not) |
| `/:D+ West`, `/:D- (A B)` | only in these dialects (§11) |
| `/:C+ N`, `/:C- N`, `/:C*` | only inside N brackets; cyclic application (§11) |
| `/:@1200` | the rule's date (§10) |
| `/???`, `/???:each` | optional: fork the derivation (§11) |
| `/%30`, `/%30:each` | apply with probability 30% (seeded) |
| `/:~` | count a match as success even if nothing changed (for groups) |
| `/:Raw` | skip implications after this rule |
| `/:$` | re-syllabify before applying |
| `/:T Tone` | a rule over the tone tier (§12) |
| `/::` | persistent (§9) |
| `/\` | continue the rule on the next line |

Modifiers may come in any order. A modifier written right after a group's `[[` is inherited
by every rule inside it (§9).

## 9. Groups and persistent rules

Rules can be grouped (spec §8.8):

| Group | Applies | Succeeds when |
|---|---|---|
| `[[ ... ]]` | every member, in order | at least one member applied |
| `&&[[ ... ]]` | members in order; at the first failure the whole group is undone | all applied |
| `\|\|[[ ... ]]` | members in order until one applies | one applied |

A `Rules` section is itself a sequence group. Modifiers after the opening `[[` on the same
line are inherited by the members (a member's own modifier of the same kind wins; contexts and
filters add up). After the closing `]]` a group takes modifiers that make sense for a whole
group: `/:1` or `/:*`, `/:C±`, `/:C*`, `/:L±`, `/:D±`, `/:@`, `/%`, `/???`, `/"`, `/:~` and
whole-form filters. `$Name` on its own line runs a named Rules section or group again.

**Chain shifts** need no group: put the rules in counter-feeding order, so *e* → *i* runs
before *a* → *e* (*pate* → *peti*). A **disjunction** is for alternatives of which only one may
apply. This word-initial swap would undo itself as a plain sequence (*tada* → *dada* →
*tada*); with `||` the first rule that applies wins:

<!-- run: tada -> dada; data -> tata -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  ||[[
    '[t] --> '[d] / # ___
    '[d] --> '[t] / # ___
  ]]                                       /" Swap
]]
```

A **strong conjunction** applies all of its rules or none. Here a final *i* is lost only after
it has palatalized the *k* before it:

<!-- run: paki -> pat͡ʃ; kiku -> kiku -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  &&[[
    [k] --> [t_S] / ___ [i] #
    [i] --> 0 / [t_S] ___ #
  ]]                                       /" Apocope
]]
```

**Persistent rules** (`/::`) apply where they stand and then again after every later rule
that changes the form, to the end of the enclosing group (spec §8.9). Final devoicing that
stays true after apocope:

<!-- run: tagu -> tak; tag -> tak -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  {-Son} --> {-Voice} / ___ #   /::        /" FinalDevoicing
  V --> 0 / ___ #                          /" Apocope
]]
```

## 10. Dates

Rules can carry dates, and records can too, so that loanwords escape the changes that happened
before they were borrowed (spec §8.10).

- `!date 500` inside Rules dates the rules after it; `/:@500` dates one rule. Dates are
  integers (negative for BCE) and must not decrease through the file.
- A record's date comes from a `date` column in the lexicon (`#! form gloss date`) or from the
  API. A rule applies to a dated record only if the rule's date is **later** than the
  record's. Undated rules never apply to dated records.
- `--from A --to B` runs only the rules dated between A and B (undated rules are included
  unless `--from` is given).

<!-- run: kima -> t͡ʃime; kima @1000 -> kime; kima @2000 -> kima -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
Rules [[
  !date 500
  [k] --> [t_S] / ___ [i]                  /" Palatalization
  !date 1500
  [a] --> [e] / ___ #                      /" Fronting
]]
```

An undated *kima* goes through both rules; borrowed in 1000 it only fronts; borrowed in 2000 it
is untouched. `examples/simple/` does the same with a lexicon (`telefon 1950`).

## 11. Variants, dialects and paradigms

The current form can hold several **variants** at once; every rule applies to each, and
identical variants merge (spec §8.11). Each variant has a *label*, the decisions that made it.

**Optional rules.** `/???` splits each variant in two: the rule applied, and it did not.
`/???:each` splits once per matching place. Here apocope is optional:

<!-- run: pata -> pat | pata; lupus -> lupuh -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  V --> 0 / ___ #  /???                    /" Apocope
  [s] --> '[h] / ___ #                     /" Debuccalization
]]
```

The command line prints one line per variant, with its label: `pata  pat  Apocope:yes` and
`pata  pata  Apocope:no`. *lupus* has no final vowel, so it does not fork. `MaxVariants`
(64 by default) caps the number of variants. **Stochastic** rules (`/%30`) apply with a
probability instead, using a random generator seeded by `!set Seed = n` (default 0), so a run
is still reproducible.

**Dialects.** `!dialects (North South)` inside Rules turns each variant into one per dialect;
later rules can be restricted with `/:D+` and `/:D-` (spec §10.4):

<!-- run: kita -> t͡ʃito | kido -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  [a] --> [o] / ___ #                      /" Common
  !dialects (North South)
  [k] --> [t_S] / ___ [i]    /:D+ North    /" Palatalization
  [t] --> [d] / V ___ V      /:D- North    /" Voicing
]]
```

A `dialect` column in the lexicon limits a record to some dialects; `--dialect South` runs
one; `--wide` prints one column per dialect (see `examples/dialects/`).

**Paradigms.** A `Paradigm` lists cells built from the stem `$_`, strings and boundaries;
`!paradigm $Noun` in Rules replaces each variant by one per cell, labelled with the cell's
name, and the following rules apply to all of them (spec §9):

<!-- run: lupa -> lupa | lup+i | lup+os -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
$Noun := Paradigm [[
  Nom : $_
  Pl  : $_ - [i]
  Gen : $_ - [os]
]]
Rules [[
  !paradigm $Noun
  V --> 0 / ___ - V                        /" Hiatus
]]
```

Cells may carry `/:L±` restrictions, and a record's `paradigm` column can name its paradigm
instead (`examples/paradigm/`).

**Brackets and cycles.** Input such as `<V:<N:CAD>CAD>` (with the library's X-SAMPA
orthography: `(V:(N:...)...)`) marks morphological constituents. `/:C+ N` confines a rule to
N brackets, and `/:C*` applies a group cyclically, innermost brackets first, erasing each
layer of brackets after its cycle (spec §8.6).

## 12. Tone tiers

A feature declared with `Tier(...)` is autosegmental: its values live on a tier of their own,
linked to *tone-bearing units* by association lines (spec §5.5, §6.5).

```text
Tone [H] [L] [M] Tier(TBU={+Syll}, Stray=float, OCP=off)
```

- Each segment *sees* the tones it is linked to: `{[H]Tone}` matches a vowel linked to H, and
  writing `{[L]Tone}` relinks it. Plain feature rules therefore work on tone.
- The link notation addresses the tier directly: `V^[H]` (a vowel linked to H), `V^0`
  (toneless), `^[H]` (a floating H), `=h` to capture an autosegment, and `V^=h` on the
  right-hand side to link a vowel to it (spreading).
- `!associate Tone` links floating tones to toneless vowels one to one (the Association
  Convention); `!ocp Tone merge` fuses adjacent identical tones.
- `/:T Tone` makes a rule operate on the string of tones alone.
- In the orthography tones are diacritics (`{[H]Tone} ==> [#']`), and a floating tone is
  written with the `FloatingPrefix` (default `^`), e.g. `^'`.

This is a short version of `examples/tone/`:

<!-- run: ^'kapaku -> ka'pa'ku`; kapaku -> ka`pa`ku`; ^'^`^'pakatita -> pa'ka`ti-ta` -->
```yasc
$P := Phonology [[
  Syll Binary
  High Binary
  Back Binary
  Place [lab] [cor] [dor]
  Tone [H] [L] [M] Tier(TBU={+Syll}, Stray=float)
]]
$O := Orthography [[
  [p t k] {-Syll}
  [p] {[lab]Place}
  [t] {[cor]Place}
  [k] {[dor]Place}
  [a] {+Syll -High +Back}
  [i] {+Syll +High -Back}
  [u] {+Syll +High +Back}
  {[H]Tone} ==> [#']
  {[L]Tone} ==> [#`]
  {[M]Tone} ==> [#-]
]]
V === {+Syll}
C === {-Syll}
Rules [[
  !associate Tone spread=none
  V^0 --> V^=h / V^[H]=h C* ___ C* V         /" Doubling
  V^0 --> V^[L]                              /" DefaultL
  [H] --> [M] / [H] [L] ___  /:T Tone        /" Downstep
]]
```

The floating H of *^'kapaku* docks on the first vowel, doubles onto the second (not onto the
final vowel), and the last vowel gets a default L. In *^'^`^'pakatita* the third tone, an H
after H L, is downstepped to M by the tier-only rule. The standard library has no tone
feature: declare one in your own Phonology as above.

## 13. Running scripts

### 13.1 The command line

```text
python3 -m yasc SCRIPT [LEXICON ...] [-o OUT] [--trace] [--json] [--from A] [--to B]
                [--dialect D] [--wide] [--paradigm NAME] [--only NAME ...] [--skip NAME ...]
                [--check] [--list-rules] [--word FORM ...] [--profile]
```

| Option | Effect |
|---|---|
| `--check` | compile only and report every error (up to 20) |
| `--list-rules` | one line per rule: id, name, date, source line, canonical text |
| `--word F ...` | run these forms instead of a lexicon |
| `--trace` | print every change that a rule made |
| `--json` | one JSON object per record (§13.3) |
| `--from A --to B` | only rules dated between A and B |
| `--only N ...`, `--skip N ...` | run only, or skip, the rules with these names |
| `--dialect D`, `--wide` | one dialect, or one column per dialect |
| `--paradigm NAME` | expand untagged records with `$NAME` |
| `-o OUT`, `--profile` | write to a file; time each rule |

Options that take several values (`--word`, `--only`, `--skip`) swallow everything after
them, so put the lexicon first: `python3 -m yasc s.yasc words.tsv --only Voicing`.

**Exit status**: 0 on success; 1 when the script does not load; 2 on a usage error or when
some record failed. A failing record is reported on stderr and the others are still
processed.

### 13.2 Lexicons

A lexicon is tab-separated by default; `!set InputFormat = csv`, `lines` or
`regex:<pattern>` (named groups become fields) choose another format (spec §10.2). A header
line names the columns:

```text
#! form	gloss	features	date	dialect	paradigm
pater	father	!N
telefon	telephone	!N	1950
```

`form` is the input, in the input orthography; `features` holds lexical features (`!N
+Romance`), read by `/:L±`; `date` makes the record a loanword of that date; `dialect`
restricts it to some dialects; `paradigm` names its paradigm. Other columns are fields,
available as `$field[2]` or through `%F`. Without a header, column 1 is the form. Blank lines
and lines starting with `%%` are skipped.

### 13.3 Output

Without `!print`, each variant prints `input<TAB>output`, plus a label or dialect column when
there is one. `!print` takes over, formatting its arguments:

| Directive | Prints |
|---|---|
| `%O{i}` | argument *i* in the output orthography |
| `%O[$Name]{i}` | argument *i* in orthography `$Name` |
| `%S{i}` | the segments as feature bundles |
| `%I{i}` | the raw input text |
| `%F{i}` | a field |
| `%L{i}` | the variant label |
| `\n`, `\t`, `%%` | newline, tab, percent |

`$in` is the parsed input, `$_` the current form, `$raw` the input text; `$x := $_` saves the
form at some point, which is how `examples/latin-spanish/` spells words from their 1500 form.
`!assert "text"` reports a mismatch between the current output and the expected text.

**Trace lines** read `rule-id  name  line  before → after  (focus i..j)`. **JSON output**
has one object per record:

```text
{"input": "Sapa", "nr": 1, "source": "<word>:1",
 "outputs": [{"form": "ʃap", "label": null, "dialect": null, "approximate": []}],
 "printed": null,
 "trace": [{"rule_id": 1, "name": "Voicing", "line": 8, "before": "ʃapa", "after": "ʃaba",
            "foci": [[2, 3]], "note": null}, ...],
 "warnings": []}
```

A failed record also has an `"error"` string. `approximate` lists segments that no grapheme
matched exactly, with the features that the rendering got wrong. The keys are stable; new
ones may be added.

### 13.4 Settings

`!set Name = value` at top level or in Rules: `DefaultMode` (`simultaneous` or `once`),
`MaxIterations` (1000), `MaxVariants` (64), `Seed` (0), `OnUnparsable` (`error`, `skip`,
`keep`), `EnforceConstraints` (`on`/`off`), `InputFormat`, `Trace`. Other commands: `!include
"file"` (relative to the including file, or `lib:NAME`), `!use $X`, `!orthography input $A
output $B`, `!syllabify`, `!dialects (...)`, `!paradigm $P`, `!only`/`!skip`, `!date`.

## 14. The Python API

```python
import yasc

sc = yasc.load("examples/simple/grimm.yasc")      # or yasc.loads(text, "name.yasc")
res = sc.apply("pater", features="!N", trace=True)  # also date=, dialect=
res.text                   # 'pater\tpater\tfaθer\t\n'  (what the CLI would print)
[str(v) for v in res.outputs]   # ['faθer']  one Variant per output (.text .label .dialect)
res.trace[0]               # TraceStep(rule_id=1, name='GrimmVoiceless', ...)
res.to_dict()              # the --json object

[(r.id, r.name, r.date) for r in sc.rules]   # [(1, 'GrimmVoiceless', -500), ...]
seg = sc.phonology.segment("{+Syll +High}")  # a bundle (no implications applied)
sc.orthography.render_segment(seg)           # its rendering, and whether it is exact
sc.violations("pabta")     # constraint violations (§4.5); sc.well_formed(text)
```

Loading raises `yasc.YascLoadError` (with `.errors`, each with a file, line, column and hint).
`sc.apply` raises a record's error (a `yasc.YascRuntimeError`, for example for unparsable
input); the command line instead reports it, stores it on the record's `Result.error` (the
JSON `error` key) and goes on. `yasc.format_trace` is not
exported; `yasc.runtime.format_trace(step, orthography)` formats a trace line as the CLI does.
The same inputs, script and seed always give the same result.

The tools of §16 are modules too: `yasc.tools.minimize.minimize(phones, forced)` and
`yasc.tools.make_ortho.make_ortho(phones, minimal=True)`.

## 15. The standard library

`!include "lib:ipa.yasc"` reads `yasc/lib/ipa.yasc`, wherever the including script is. It
defines three things and no macros, so it never clashes with a script's own names:

- **`$IPAPhonology`**, a Hayes-style feature system with geometry:

  | Group | Features |
  |---|---|
  | major class | `Syll` `Cons` `Approx` `Son` |
  | manner | `Cont` `DelRel` `Nasal` `Lateral` `Tap` `Trill` `Strident` `Click` |
  | Laryngeal node | `Voice` `SpreadGl` `ConstrGl` |
  | Place node | `Labial` node (`Round` `Labiodental`), `Coronal` node (`Anterior` `Distributed`), `Dorsal` node (`High` `Low` `Front` `Back`), `Pharyngeal` node (`Epiglottal`) |
  | vowels | `ATR` (alias `Tense`), `Reduced` (schwa) |
  | prosody | `Long`; `Stress Scalar(0,2) Scope(Syllable)` |

  Implications keep `High`/`Low` and `Front`/`Back` exclusive, `SpreadGl`/`ConstrGl` and
  `Tap`/`Trill` exclusive, approximants sonorant, and fill vowel and sonorant defaults
  weakly; every segment defaults to `-Long`. Aliases such as `Syllabic`, `Voiced`, `SG` and
  `Ant` are accepted.
- **`$XSAMPA`**, the full X-SAMPA inventory of `orig-notes/xsampa` (pulmonic consonants,
  vowels plus `U`, clicks, implosives, ejectives, `W w H H\ <\ >\ s\ z\ l\ x\`, `k_p`, the
  affricates `t_s d_z t_S d_Z`), with the diacritics `_0 _v _h _k _< _> = _= _^ ~ _~ _w _j
  _G _?\ _d _A _q :` and the retroflex hook written as a backquote after the base. It is
  active after the include.
- **`$IPA`**, the same bundles in Unicode IPA, with combining diacritics after the base
  (decomposed, NFD) and ʰ ʷ ʲ ˠ ˤ ʼ ː ˈ ˌ. `g` is also accepted as `ɡ`.

Write `!orthography input $XSAMPA output $IPA` to type X-SAMPA and read IPA; `[...]` in rules
is then X-SAMPA.

**X-SAMPA settings.** Some X-SAMPA symbols collide with YASC's defaults, so `$XSAMPA` changes
them: syllabic `=` is a diacritic, so the **clitic separator is `/`**; `_<`, `_>`, `<\` and
`>\` use angle brackets, so **category brackets are `(N:...)`**; `-` is the phone
separator, written only where two phones would otherwise run together (`t-s` is *t* + *s*,
`t_s` the affricate); `"` and `%` are syllable marks for primary (`2Stress`) and secondary
(`1Stress`) stress. `$IPA` keeps the defaults (`=` clitic, `<N:...>`), with `ˈ` and `ˌ`.

**What is left out**, and why: `P` and `v\` are the same sound (both render as `P`);
breathy `_t` would tie with `_h` (breathy voice is `_h` on a voiced base); `_O _c _+ _- _"
_x _r _o _a _m _N _n _l _} _e`, `:\` and `_X` are phonetic detail with no feature;
the tone letters `_T _H _M _L _B _R _F`, `!` and `^` need a `Tier` feature, which a general
library cannot impose (declare your own, §12); `|`, `||`, `-\`, `<R>` and `<F>` are
intonation. X-SAMPA `a` is front and `6` near-open central; near-open vowels (`{ 6`) are
distinguished from open ones by `+ATR`.

Because the library's Phonology is fixed, a script that includes it cannot add
implications or `Constraint` lines; copy the file and edit it for that.

## 16. The tools

Both tools read the standard library and run as modules.

**`python3 -m yasc.tools.minimize [SCRIPT] [--phones "..."] [--force F ...] [--json]`**
finds a small set of features that distinguishes every phone (of `--phones`, or of the
script's orthography; `SCRIPT` defaults to `lib:ipa.yasc`). It adds, one at a time, the
feature that splits the phones most evenly (highest entropy), keeps any `--force`d
features, then drops choices made redundant, and reports which other features follow from
one or two of the chosen ones:

```text
$ python3 -m yasc.tools.minimize --phones "p t k b d g m n a i u" --no-predict
features (4): Round Voice Son High
  p  {-Round -Voice -Son}
  t  {-Voice -Son}
  k  {-Voice -Son +High}
  ...
  u  {+Round +Voice +Son +High}
```

"Unspecified" counts as a value, so *t* (no Round) differs from *p* (−Round). Phones with
identical bundles are listed as not distinguishable.

**`python3 -m yasc.tools.make_ortho PHONE ... [--minimal] [--force F ...] [--ipa] [--name N]`**
writes an `Orthography` block for an inventory, grouping phones by shared values:

```text
$ python3 -m yasc.tools.make_ortho "p t k b d g m n s a i u" --minimal --name O
%% generated by yasc.tools.make_ortho from: p t k b d g m n s a i u
%% minimal features: Strident Voice Round High
$P := Phonology [[
  Strident Binary
  Laryngeal Node(Voice)
  ...
]]
$O := Orthography [[
  [s]               {+Strident}
  [p t k b d g]     {-Strident}
  [b d g m n a i u] {+Voice}
  ...
]]
```

Without `--minimal` it keeps the library's full bundles and is meant to follow
`!include "lib:ipa.yasc"`; with `--minimal` the output is a standalone Phonology and
Orthography. `--ipa` writes IPA graphemes.

## 17. Troubleshooting and common pitfalls

Start with `--check`: it lists every load error with its line, column, a caret and often a
hint (`did you mean 'Voice'?`). Then `--word FORM --trace` shows which rule did what.

**`#` is not a comment in the middle of a line.** Comments are `%%`. A `#` is the word
boundary; only `# ` (hash and a space) at the very start of a line is a comment. So a rule
that starts with a word boundary must not have a space after the `#`:

<!-- run: sala -> ala; asa -> asa -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
# This line is a comment: a hash and a space at the start of the line.
Rules [[
  #[s] --> 0                               %% word-initial s is lost
]]
```

**`VC` is one name.** A bare identifier is a macro name, and `VC` is not `V` followed by `C`:
`unknown macro 'VC'`. Write `V C`.

**Everything changes at once.** Rules are simultaneous by default: every match is found in
the input, then all are rewritten, and a rule never feeds itself. Use `/*` for spreading and
`/:1` for "first match only" (§8.2).

**The output letter is odd, or flagged approximate.** `+[x]` on the right-hand side merges
x's declared features into the segment (`[k] --> +[t_S]` gives a velar-postalveolar hybrid);
plain `[x]` replaces it (§8.1). An approximate rendering also means the orthography has no letter
for a bundle a rule created: add one, or a diacritic, or `*{F}` to ignore a feature.

**An implication does not fix a rule's output.** Implications re-run only when a feature read
by their *trigger* changes. `{+Low} --> {-ATR}` will not undo a rule that sets `+ATR` on a low
vowel, because Low did not change; add `{+Low +ATR} --> {-ATR}` (§4.4; the Latin example had
exactly this bug).

**`{-F}` does not match.** The segment may have F unspecified, which is not `-`. Check with
`%S{0}` in `!print`, test `_F`, or bind with `(?a)F`.

**Syllable features and `SylCoda` do nothing.** Nothing has syllabified the form: add
`!syllabify` (or `/:$`, or `Persistent yes`) (§6).

**A loanword still changes.** Only dated rules can skip a dated record, and undated rules
never apply to dated records; date both (§10).

**`--word` eats the lexicon.** Options that take several values consume what follows; put
them last, or put `--` before the script.

**Unparsable input.** `cannot parse '$'` means no grapheme covers that character. Declare it,
or `!set OnUnparsable = skip` (drop) or `keep` (pass through). With the library's IPA
orthography, input must be decomposed (NFD): `á` is `a` + U+0301.

**Two letters come out the same.** Their bundles are identical: give one of them a
distinguishing feature. `python3 -m yasc.tools.minimize` reports such pairs.

**Performance.** 1,000 words × 100 rules take about two seconds. `--profile` shows the time
spent in each rule; rules with `...` or many variables are the slowest.

For anything not covered here, the [specification](specification.md) is the reference, and
[design.md](design.md) §13 records the decisions behind each behaviour.
