# YASC for LLM agents

A compact reference for agents that write or run YASC scripts. The full language is in
[specification.md](specification.md); the [user manual](manual.md) has worked examples.
Every `yasc` block below is tested by `tests/test_docs.py` (the `<!-- run: in -> out -->`
comments are checked too).

## 1. Deterministic workflow

```text
python3 -m yasc S.yasc --check                   # 1. compile; fix every error (exit 1 = load error)
python3 -m yasc S.yasc --list-rules              # 2. confirm rule order, names, dates
python3 -m yasc S.yasc --word FORM ... --trace   # 3. test a few forms, see each change
python3 -m yasc S.yasc LEX.tsv --json            # 4. run the lexicon; parse JSON Lines
python3 -m yasc S.yasc --word FORM --trace --json   # debug one form, machine-readably
```

Same script + same input + same `!set Seed` ⇒ byte-identical output. Name every rule (`/" Name`)
so traces and `--only NAME`/`--skip NAME` are readable. Put `!assert "expected"` in Rules for
self-checking scripts.

## 2. Minimal complete script

<!-- run: pataka -> padak; pa -> pa -->
```yasc
!include "lib:ipa.yasc"                  %% features + $XSAMPA (default) and $IPA orthographies
!orthography input $XSAMPA output $IPA   %% X-SAMPA in, IPA out

V === {+Syll}                            %% macros: identifiers, defined before use
C === {-Syll}

Rules [[
  {-Son} --> {+Voice} / V ___ V          /" Voicing
  V --> 0 / ___ #  /! # C* ___           /" Apocope
  {-Son} --> {-Voice} / ___ #            /" Devoicing
]]
```

Without the library, define `Phonology [[ ... ]]` and `Orthography [[ ... ]]` yourself
(manual §3). Every grapheme needs a distinct bundle, or two letters will print the same:

<!-- run: pata -> pada; kipa -> kiba; apa -> aba -->
```yasc
$P := Phonology [[
  Syll Binary
  Voice Binary
  High Binary
  Place [lab] [cor] [dor]
]]
$O := Orthography [[
  [p t k b d g] {-Syll}
  [p t k] {-Voice}
  [b d g] {+Voice}
  [p b] {[lab]Place}
  [t d] {[cor]Place}
  [k g] {[dor]Place}
  [a] {+Syll -High}
  [i] {+Syll +High}
]]
Rules [[
  {-Syll} --> {+Voice} / {+Syll} ___ {+Syll}      /" Voicing
]]
```

## 3. Syntax cheat sheet

| Construct | Syntax |
|---|---|
| comment | `%% ...` anywhere; `# ` (hash + space) only at line start |
| feature types | `F Binary` · `F Unary` · `F Scalar(0,3)` · `F [H] [L]` · `N Node(A B)` · `... Scope(Syllable)` · `... Tier(TBU={+Syll})` |
| implications | `{+High} --> {-Low}` strong · `{+Syll} ~~> {+Voice}` weak (fills unspecified) |
| orthography | `[p t k] {-Voice}` (additive) · `{+Long} ==> [#:]` diacritic · `*{F}` ignore · `XSeparator == [x]` |
| spec | `{+F -G !U 2S [H]T _F (a)F (?a)F -(a)F {+ -}F >1S}` |
| pattern | `[p]` `'S` `V:{-High}` `<<A\|B>>` `(P)` `P*` `P+` `...` `0` `$1` `.` `-` `=` `#` `##` `<:N` `>:N` |
| rule | `LHS --> RHS / C ___ D /! C ___ D` · RHS `0` deletes · LHS `0` inserts |
| RHS items | `{+F}` modify · `~{+F}` fill · `[x]` replace (`'[x]` is the same) · `+[x]` merge x's features · `$n` copy |
| modes | default simultaneous · `/:1` once · `/*` iterative (spreading) · `/:*` repeat · `/:<` right-to-left |
| filters | `/:i± E` input · `/:o± E` output · `/:F± S` visibility |
| restrictions | `/:L± {!N}` lexical · `/:D± D` dialect · `/:C± N` bracket · `/:C*` cyclic · `/:@n` date |
| other | `/" Name` · `/???` optional · `/%n` random · `/::` persistent · `/:Raw` · `/:$` · `/:T Tone` · `/\` continue |
| groups | `[[ ]]` sequence · `&&[[ ]]` all-or-nothing · `\|\|[[ ]]` first that applies |
| commands | `!include "f"` · `!date n` · `!set Name = v` · `!orthography input $A output $B` · `!syllabify` · `!dialects (A B)` · `!paradigm $P` · `!print "%O{0}\n" $_` · `!assert "x"` |
| variables | `$x := $_` · `$in` · `$raw` · `$field[2]` |

## 4. Output formats

**Default:** `input<TAB>output` per variant; a third column holds the variant label or dialect
when there is one. `!print` replaces it.

**Trace** (`--trace`, stdout, after `== input`):
`rule-id  name  line  before → after  (focus i..j, k..l)` — foci are segment index spans.

**JSON** (`--json`): one object per record, per line. Keys are stable; new keys may be added.

```text
{"input": str, "nr": int, "source": "file:line" | null,
 "outputs": [{"form": str, "label": str | null, "dialect": str | null,
              "approximate": [{"index": int, "segment": str,
                               "residual": [{"feature": str, "value": str|null, "rendered": str|null}]}]}],
 "printed": str | null,
 "trace": [{"rule_id": int|null, "name": str|null, "line": int|null,
            "before": str, "after": str, "foci": [[int, int]], "note": str|null}],
 "warnings": [str],
 "error": str}          # only when the record failed
```

`--list-rules` prints TSV: `id  name  date  line  pending  rule` (canonical rule text).

**Exit codes:** 0 success · 1 the script does not load (every error is listed, up to 20) ·
2 usage error, or at least one record failed (the others are still processed).

**Errors** go to stderr as `file:line:col: error: message`, the source line, a caret line,
and optional `note:`/`hint:` lines:

```text
bad.yasc:3:15: error: unknown feature 'Voic'
    {-Son} --> {+Voic} / {+Syll} ___ {+Syll}
                ^^^^^
hint: did you mean 'Voice'?
```

A record error (for example an unparsable character) names the record and appears in that
record's JSON `error` field; processing continues.

## 5. Pitfalls and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `unknown macro 'VC'` | a bare name is one macro; `VC` is not `V C` | write `V C` (space) |
| rule starting with `#` ignored | `# ` + space at line start is a comment | write `#C --> ...` without the space, or `%%` for comments |
| mid-line `# note` breaks a rule | `#` is the word boundary | comments are `%%` |
| a change fires everywhere at once, no spreading | default mode is simultaneous | add `/*` (iterative) or `/:1` (once) |
| output letter looks wrong / `approximate` warnings | `+[x]` on the RHS **merges** x's features (a hybrid segment), or the orthography lacks a letter | use plain `[x]` to replace the segment |
| a "fixing" implication does not fire | implications re-run only when a feature their trigger reads changes | make the trigger read the changed feature: `{+Low +ATR} --> {-ATR}` (manual §4.4) |
| `SylCoda`, `.`, stress do nothing | no syllable tier yet | put `!syllabify` before the rules (or `Persistent yes`) |
| `{-Round}` fails on a segment | Round is unspecified there, not `-` | test `_Round`, or use `(?r)Round` |
| loanword still changes | undated rules apply to undated records only | date the rules (`!date`) and the record (`date` column) |
| `[á]` does not parse (IPA) | the IPA orthography expects decomposed input | normalise input to NFD |

The replacement with `[x]` and the fix for the most common surprise, "mode" (`/*`), in one script:

<!-- run: kitopu -> t͡ʃitepi; kupo -> kupo -->
```yasc
!include "lib:ipa.yasc"
!orthography input $XSAMPA output $IPA
V === {+Syll}
Rules [[
  [k] --> [t_S] / ___ << [i] | [e] >>       /" Palatalization   %% [x] replaces the segment
  V --> {(b)Back (f)Front (r)Round} / V:{(b)Back (f)Front (r)Round} ___  /:F- V  /*   /" Harmony
]]
```

`/:F- V` makes only vowels visible to *Harmony*, and `/*` lets each changed vowel trigger the
next one; without `/*`, *kitopu* would only become *t͡ʃitepu*.

## 6. Using `lib:ipa.yasc`

- `!include "lib:ipa.yasc"` (from any directory) defines `$IPAPhonology`, `$IPA` and
  `$XSAMPA`; `$XSAMPA` is active until `!orthography` changes it.
- Features (Hayes style): `Syll Cons Approx Son Cont DelRel Nasal Lateral Tap Trill Strident
  Click`; `Laryngeal` node: `Voice SpreadGl ConstrGl`; `Place` node: `Labial(Round
  Labiodental) Coronal(Anterior Distributed) Dorsal(High Low Front Back) Pharyngeal(Epiglottal)`;
  vowels: `ATR` (alias `Tense`), `Reduced`; `Long`; `Stress Scalar(0,2) Scope(Syllable)`.
- X-SAMPA settings: syllabic `=`, so the clitic separator is `/`; category brackets are
  `(N:...)`; `-` separates phones where needed; `"` and `%` mark primary and secondary stress.
- Diacritics: `_0 _v _h _k _< _> = _= _^ ~ _~ _w _j _G _?\ _d \` _A _q :` (IPA: combining
  marks and ʰ ʷ ʲ ˠ ˤ ʼ ː).
- The library has no macros and no constraints; define your own.
- `python3 -m yasc.tools.make_ortho "p t k a i u"` prints an orthography for an inventory;
  `python3 -m yasc.tools.minimize --phones "p t k a i u"` finds a minimal distinguishing
  feature set (manual §16).
