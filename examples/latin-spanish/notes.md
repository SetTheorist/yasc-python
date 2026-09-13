# Latin → Spanish in YASC: notes

Files in `examples/latin-spanish/`:

| File | Contents |
|---|---|
| `latin-spanish.yasc` | the script: Phonology, three orthographies ($Lat, $Sp, $IPA), Syllabification, dated Rules |
| `lexicon.tsv` | 48 Latin words (46 derived, 2 flagged `NOT DERIVED`) with expected IPA and spelling (spec §10.2 header) |
| `notes.md` | this file: derivations, block table, features used, issues |

The script cannot be executed yet (the rule engine is the next phase). Every derivation below
is done by hand, following the spec's semantics:
- rules apply simultaneously by default (§8.3);
- contexts are matched against the input form of each rule (§8.2);
- implications run after every change (§4.5);
- a `||[[ ]]` group stops at its first member that applies (§8.8);
- a `/::` rule re-applies after every later rule that changes the form (§8.9).

Notation in the derivations: `ˈ` marks the stressed syllable (a `Scope(Syllable)` value, not a
segment). "—" means the rule does not apply. Only rules that change the word, or that are worth
noting because they do *not* apply, are listed.

## 1. Hand derivations

### 1.1 FACTUM → hecho [ˈetʃo]

Input `FACTUM` in $Lat: /f a k t u m/, all vowels short (`-Long`).

| Date | Rule | Form | Comment |
|---|---|---|---|
| — | input | fak.tum | `!syllabify $Syl`: k is a coda, t an onset |
| 0 | Unstressed | fak.tum | both vowels `0Stress` |
| 0 | Stress (StressDisyllable) | ˈfak.tum | StressMonosyllable fails (two vowels). StressDisyllable matches `#C* ___ C* V C*#` with C\* = f, then k t, V = u, C\* = m. The `\|\|` group stops here. |
| 100 | FinalMLoss | ˈfaktu | m is before `#`. The negative context `#C* V ___` needs only consonants between `#` and the vowel before m, but it meets a, so it fails and the rule applies. |
| 200 | QualityForQuantity | ˈfaktʊ | `(a)Long` = − gives −ATR, so u becomes ʊ. For a, the implication `{+Low} --> {-ATR}` holds anyway. |
| 200 | LaxHighLowering | ˈfakto | ʊ (+High −ATR) becomes o |
| 300 | VelarPalatalisation | — | k is followed by t, whose `Back` is unspecified, so `{-Back}` does not match |
| 300 | KtToJt | ˈfajto | Velar k in `V ___ [t]` is replaced (`'[j]`) by the glide j |
| 500 | Lenition | — | t follows j (a C), not a V |
| 700 | YodMetaphony / ARaising | ˈfejto | a before [j] becomes tense e (`-Low -Back +ATR`) |
| 700 | JtToTsh | ˈfetʃo | `[j] [t] --> TSH`: j is reshaped into tʃ; the excess LHS segment t is deleted (§8.2.4) |
| 800 | Diphthongisation | — | e is +ATR (only lax ɛ ɔ diphthongise) |
| 1100 | Apocope | — | the final vowel is o, not e |
| 1300 | FToH | ˈhetʃo | f after `#`, followed by e: neither `{+Cons}` nor `{-Syll +Round}`, so neither negative context blocks it |
| 1500 | `$spell := $_` | ˈhetʃo | $Sp spells h‧e‧ch‧o = **hecho** |
| 1550 | HLoss | ˈetʃo | The syllable keeps its stress (only the onset is deleted, §5.4 upkeep). |
| 1550 | SibilantDevoicing | ˈetʃo | tʃ is already −Voice, so the form does not change |
| 1600 | Velarisation, Interdentalisation | — | tʃ is −Cont (not Velarisation), and −Anterior (not Interdentalisation) |

Output: `FACTUM	[ˈetʃo]	hecho`.

### 1.2 OCULUM → ojo [ˈoxo]

Input /o k u l u m/, all short vowels.

| Date | Rule | Form | Comment |
|---|---|---|---|
| 0 | Stress | ˈo.ku.lum | These fail: StressDisyllable (there is a third vowel), StressLongPenult (no `+Long`) and StressClosedPenult (u follows k directly). StressAntepenult matches `___ C* V C* V C*#` at o. |
| 100 | FinalMLoss | ˈokulu | |
| 200 | QualityForQuantity | ˈɔkʊlʊ | short o and u become lax |
| 200 | LaxHighLowering | ˈɔkolo | ʊ becomes o |
| 300 | VelarPalatalisation | — | the vowel after k is +Back |
| 500 | Lenition | ˈɔgolo | k in `V ___ V`; `<<[p]\|[t]\|[k]>>` alternative 3 selects `[g]` |
| 600 | Syncope | ˈɔglo | The rule runs `/* /:<`. The final o fails the context (no `C V` after it). The medial o is 0Stress and −Low, in `V C ___ C V`, so it is deleted. |
| 600 | PalatalSonorants / MedialKL | ˈɔʎo | `Velar [l]` after a vowel: g is reshaped by `LY` (Place delinked, then −Anterior, +Son +Lateral +Cont +Voice), and l is deleted as excess LHS |
| 800 | Diphthongisation | — | ɔ is stressed and lax, but the negative context `/! ___Palatal` matches ʎ (−Syll −Anterior), so the rule is blocked: the "yod effect" |
| 800 | LaxMidTensing | ˈoʎo | |
| 900 | LateralToZh | ˈoʒo | |
| 1500 | `$spell` | ˈoʒo | $Sp: j spells ʒ, giving **ojo** |
| 1550 | SibilantDevoicing | ˈoʃo | |
| 1600 | Velarisation | ˈoxo | The Place node is used as a unit: `_Place` removes Coronal/Anterior, then `!Dorsal` is set and `_Strident` unset |

Output: `OCULUM	[ˈoxo]	ojo`.

### 1.3 CĪVITĀTEM → ciudad [θjuˈdad]

Input /k iː w i t aː t e m/; syllabified kiː.wi.taː.tem.

| Date | Rule | Form | Comment |
|---|---|---|---|
| 0 | Stress (StressLongPenult) | kiː.wi.ˈtaː.tem | The rule is simultaneous, but only aː satisfies `___ C* V C*#`: for iː, the context meets aː where it needs `#`. |
| 100 | FinalMLoss | kiːwiˈtaːte | |
| 100 | WToBeta | kiːβiˈtaːte | w is not after a Velar (the negative context fails), so it becomes β |
| 200 | QualityForQuantity | kiβɪˈtatɛ | long vowels become tense, short ones lax |
| 200 | LaxHighLowering | kiβeˈtatɛ | |
| 300 | VelarPalatalisation | tsiβeˈtatɛ | k before i (−Back): `_Place +Anterior +DelRel +Strident` |
| 500 | Lenition | tsiβeˈdadɛ | Both t's are between vowels. Simultaneous mode: both foci are found on the input form and rewritten together. |
| 600 | Syncope | tsiβˈdadɛ | The intertonic e is in `V C ___ C V` (i β _ d a). The initial i has no V before it, and the final ɛ has none after. |
| 800 | LaxMidTensing | tsiβˈdade | |
| 1100 | Apocope | tsiβˈdad | d is `{+Anterior +Voice}` (ApocopeC alternative 1), and the vowel a precedes it |
| 1450 | BetaVocalisation | tsiuˈdad | β before d becomes u (`_Place`, +Syll …) |
| 1450 | HiatusGlide (persistent) | tsjuˈdad | The rule was declared `/::` at date 150, so it re-runs after BetaVocalisation changes the form. The i is 0Stress and −Low, and it now stands before a vowel (§8.9). |
| 1500 | `$spell` | tsjuˈdad | $Sp: ts is c, j is i: **ciudad** |
| 1600 | Interdentalisation | θjuˈdad | `{+DelRel +Anterior}` becomes +Cont, `_DelRel`, −Strident |

Output: `CĪVITĀTEM	[θjuˈdad]	ciudad`.

The syncope step also dissolves the syllable of e, which leaves its onset β unsyllabified
(§5.4 upkeep). The u made from β is therefore unsyllabified too. That does no harm here: the
focus of HiatusGlide is the i, which still belongs to its 0Stress syllable. But an IPA
rendering at this stage might not place u in any syllable (see issue S2).

### 1.4 SCHOLAM → escuela [esˈkwela]

Input `SCHOLAM`: the CH is one grapheme (the tokenizer prefers fewest tokens), giving /s k o l a m/ with a short o.

| Date | Rule | Form | Comment |
|---|---|---|---|
| — | `!syllabify` | s.ko.lam | sk is not an Onset, so MaxOnset gives k to the syllable and leaves s unsyllabified (there is no preceding syllable to take it as a coda) |
| 0 | Stress (StressDisyllable) | sˈko.lam | `#C* ___`: C\* spans s k |
| 100 | FinalMLoss | sˈkola | |
| 200 | QualityForQuantity | sˈkɔla | |
| 300 | Prothesis | esˈkɔla | Epenthesis (LHS `0`) at gap 0: `#` holds, and `[s] C` follows. The macro TenseE creates the new segment from the spec alone (§8.2.4 "Excess RHS"). |
| 500 | Lenition | — | k follows s, not a vowel |
| 600 | Syncope | — | the only 0Stress vowel is final; e has no syllable, hence no Stress value |
| 800 | Diphthongisation | esˈkwela | ɔ is stressed and lax, and l is not Palatal. RHS item 1 modifies ɔ itself: −Syll +High, keeping +Back +Round, gives w. RHS item 2 (TenseE) is inserted after it and joins w's stressed syllable (§5.4). |
| 1500 | `$spell` | esˈkwela | $Sp: k is c and w is u, giving **escuela** |
| 1550 | SibilantDevoicing | — | s is already −Voice |

Output: `SCHOLAM	[esˈkwela]	escuela`.

### 1.5 CLĀVEM → llave [ˈʎabe]

Input /k l aː w e m/; syllabified klaː.wem (kl matches the first Onset alternative).

| Date | Rule | Form | Comment |
|---|---|---|---|
| 0 | Stress (StressDisyllable) | ˈklaː.wem | |
| 100 | FinalMLoss | ˈklaːwe | |
| 100 | WToBeta | ˈklaːβe | w follows aː, not a Velar |
| 200 | QualityForQuantity | ˈklaβɛ | aː gets +ATR from `(a)Long`, then the strong implication `{+Low +ATR} --> {-ATR}` resets it. Its trigger reads ATR, so the rule's change re-fires it (§4.5); plain `{+Low} --> {-ATR}` would not re-fire, because Low is unchanged. |
| 600 | MedialKL | — | The context `V ___` fails, because k follows `#`. This matters: if it applied here, the ʎ would become ʒ at 900, giving \*jave. |
| 800 | LaxMidTensing | ˈklaβe | |
| 900 | LateralToZh | — | there is no ʎ yet |
| 1000 | InitialClusterPalatalisation | ˈʎaβe | `<<[p]\|[k]\|[f]>> [l]` after `#`. `LY` reshapes k into ʎ, and l is deleted as excess LHS. LateralToZh (900) comes first, so it cannot apply to this ʎ: a counter-feeding order. |
| 1100 | Apocope | — | β is not in ApocopeC (it is not +Anterior) |
| 1500 | `$spell` | ˈʎaβe | $Sp: ʎ is ll and β is v, giving **llave** |
| 1600 | BetaMerger | ˈʎabe | |

Output: `CLĀVEM	[ˈʎabe]	llave`.

### 1.6 Words the rules get wrong (flagged `NOT DERIVED` in lexicon.tsv)

- **FĒMINAM → hembra.** The rules produce ˈfeːmina, then ˈfemena (ĭ > e), then ˈfemna
  (Syncope), then ˈhemna. The output is [ˈemna], spelled *hemna*. Real Spanish repairs the
  new cluster m'n: it becomes mr by dissimilation, then mbr by b-epenthesis. That needs two
  more rules at about date 1200, e.g. `[n] --> [r] / [m]___` and `0 --> [b] / [m]___[r]`.
  They are left out to keep the rule set small.
- **DECEM → diez.** The rules produce ˈdɛkɛ, then ˈdɛtsɛ (VelarPalatalisation), then ˈdjetse,
  then [ˈdjeθe], spelled *diece*. Real Spanish voices the affricate between vowels (ts > dz,
  part of Lenition), and apocope after dz then gives *diez*, with final z. Two changes would
  fix it: extend `VoicelessStop`/`VoicedStop` with a ts/dz pair, and make $Sp context-sensitive
  (word-final θ as z). The second cannot be expressed in a context-free orthography; it would
  need a respelling rule block.
- Every other word in the lexicon has been checked by hand against the rules and derives as
  expected. The stress rule is checked separately for every word: the penult for disyllables,
  StressLongPenult for AMĪCUM, SAPŌREM, CANTĀRE and similar, StressClosedPenult for CAPILLUM,
  and StressAntepenult for OCULUM, FĪLIUM, FOLIAM, AQUAM, ARĀNEAM and FĒMINAM.

## 2. Rule blocks

Dates are rough centuries AD, and they are only illustrative (§8.10 requires them to be
non-decreasing).

| Date | Rules | Historical justification |
|---|---|---|
| 0 | `!syllabify`, Unstressed, `\|\|[[ Stress… ]]` | Classical Latin penultimate law. Stress is fixed before anything else, because the later vowel changes depend on it. |
| 100 | FinalMLoss, WToBeta | -m had been lost in speech since the Republic (inscriptions, Appendix Probi). The b/w merger started early in intervocalic position. |
| 150 | HiatusGlide (`/::`) | Hiatus e, i > yod, the source of most palatals (FĪLIUM, ARĀNEAM). u > w in kw is the same process. |
| 200 | QualityForQuantity, LaxHighLowering | The Vulgar Latin (Western/"Italo-Western") vowel system: quantity becomes quality, and ĭ, ē > e and ŭ, ō > o. |
| 300 | StopPlaceAssimilation, Prothesis, VelarPalatalisation, KtToJt, LtToJt | Late Latin consonant changes: pt > tt, prothetic i-/e- (ISCHOLA in inscriptions), k before a front vowel > ts, and the first step of kt and ult. |
| 500 | Lenition, Degemination | Western Romance lenition, with degemination after it, in counter-feeding order so geminates give voiceless stops (SEPTEM > siete, not \*siede). |
| 600 | Syncope, PalatalSonorants | Syncope of intertonic and post-tonic vowels, and the new k'l and g'l clusters. lj, nj and k'l become ʎ and ɲ. |
| 700 | YodMetaphony, JtToTsh | The yod raises a > e and o > u (leche, mucho), then jt > tʃ (a Castilian feature; Portuguese keeps *feito*). |
| 800 | Diphthongisation, LaxMidTensing | Castilian diphthongisation of stressed ɛ ɔ, blocked by a following palatal. The remaining ɛ ɔ merge with e o. |
| 900 | LateralToZh | Medial ʎ (from lj, k'l) > ʒ, a distinctively Castilian change (Leonese and Aragonese keep ʎ). |
| 1000 | SonorantGeminates, InitialClusterPalatalisation | ll nn > ʎ ɲ, rr > trill, and pl-, kl-, fl- > ʎ, too late for LateralToZh. |
| 1100 | Apocope | Loss of final -e after a single dental or alveolar consonant (Old Spanish). |
| 1300 | FToH | f- > h- (a Cantabrian and Basque-substrate feature), blocked before w and consonants. |
| 1450 | BetaVocalisation | cibdad > ciudad and similar forms. |
| 1500 | `$spell := $_` | The spelling checkpoint (see the header of the .yasc file). |
| 1550–1600 | HLoss, SibilantDevoicing, Velarisation, Interdentalisation, BetaMerger | The Early Modern "reajuste": h is lost, sibilants devoice, ʃ > x, ts > θ, and β merges with b. |

There are 37 basic rules in 3 groups (the `||` Stress group and two sequence groups).

## 3. YASC features exercised

| Feature | Where |
|---|---|
| Binary, Unary, Scalar features; aliases (`==`) | Phonology |
| Feature geometry: nested nodes | `Place Node(Labial Coronal Dorsal)` with `Coronal Node(Anterior)`, so `!Coronal` is derived, never stored |
| Node variable `(p)Place` | StopPlaceAssimilation (pt > tt) |
| Node delinking `_Place` | VelarPalatalisation, `TSH`, `LY`, BetaVocalisation, Velarisation |
| `Scope(Syllable)` feature | Stress, read through segments as `{0Stress}` or `{2Stress}` |
| Strong and weak implications | `{+Low +ATR} --> {-ATR}`, which corrects the output of QualityForQuantity (its trigger reads ATR, so it re-fires); `{+Syll} ~~> {… -Long}` |
| Three orthographies; `!orthography input … output …` | $Lat, $Sp, $IPA |
| Multi-character graphemes, diacritic, ignore set, SyllableMark | `CH PH TH ts tʃ`; `{+Long} ==> [#:]`; `*{Syll Cons ATR Long}`; `[ˈ]` |
| Syllabification section, `!syllabify` | $Syl (MaxOnset, muta cum liquida) |
| Macros, including refinement `Name:{…}` | V, C, Velar, Palatal, …; `Velar:{-Voice}` |
| Alpha variable shared across two features | QualityForQuantity `V:{(a)Long} --> {(a)ATR -Long}` |
| Class correspondence | Lenition `VoicelessStop --> VoicedStop`; SonorantGeminates (3 alternatives mapped to specs) |
| Back-references `$1` | Degemination and SonorantGeminates (a segment followed by an exact copy of itself) |
| Negative contexts `/!` | FinalMLoss, WToBeta, StressClosedPenult, Diphthongisation, FToH (two) |
| `#` boundaries | Stress, FinalMLoss, Prothesis, InitialClusterPalatalisation, Apocope, FToH |
| `/*` with `/:<` | Syncope |
| `\|\|[[ ]]` | Stress (the first applicable member wins) |
| Plain `[[ ]]` groups, named after `]]` | PalatalSonorants, YodMetaphony |
| Persistent `/::` | HiatusGlide |
| Epenthesis (`0 -->`), excess RHS insertion, excess LHS deletion | Prothesis; Diphthongisation; JtToTsh, MedialKL |
| Strict replacement `'[x]` | KtToJt, LtToJt (`'[j]`), FToH (`'[h]`) |
| Merge `[x]` on the RHS | Lenition (`[b] [d] [g]`) |
| `!date`, `/" Name`, variables, `!print` with `%O[$X]` | throughout; `$input`, `$spell` |

Not used, because nothing in this history called for them:
- `/:1`, `/:*` and `&&[[ ]]`;
- filters (`/:i± /:o±`), visibility (`/:F±`), `/:L± /:D± /:C±`;
- `/%` and `/???`;
- `/:~` and `/:Raw`;
- tiers, dialects, paradigms, constraints and `$Name` invocation.

## 4. Spec and compiler issues

Each entry gives the section, the problem, and a suggested resolution. **C** = compiler,
**S** = spec, **O** = orthography, both.

**O1 — working orthography for `[...]` (spec §10.3, design §13 entry 71; C and S).**
- Problem: there is no way to parse rule strings with one orthography while reading input
  with another. Every `Orthography` section sets input, output *and* working orthography.
  `!use $X` does the same. `!orthography input $A` makes `$A` the working orthography. So a
  script whose input is Latin letters must write its rules with Latin letters. Here
  `[ʎ] [tʃ] [ɛ]` cannot be written, so they became feature macros (`LY`, `TSH`, `Palatal`).
  The workaround reads well, but a user will expect `[tʃ]` to work.
- Suggestion: add `!orthography patterns $X` (or `working`), or orthography-qualified
  strings such as `[$IPA:tʃ]`.

**C2 — role pseudo-features rejected (spec §5.4; C).**
- Problem: `{SylCoda}` fails with "unknown feature 'SylCoda'", although §5.4 defines
  `SylOnset`, `SylNucleus`, `SylCoda` and `Syllabified`. Other P7 constructs compile as
  placeholders with a warning; these are hard errors. StressClosedPenult therefore tests
  heaviness segmentally: `___ C C C* V C*#`, with a negative context for muta cum liquida and
  C + glide.
- Suggestion: pre-declare the four pseudo-features in every feature system as read-only
  Unary, compile them with a P7 warning, and reject them on the RHS.

**C3 — `--check` and `--list-rules` require the runtime (spec §11.1; C).**
- Problem: both print "running scripts is not implemented until plan phase P6", although
  they need only the compiler.
- Suggestion: handle them before the runtime gate, with `allow_unimplemented=True`, and
  report placeholders as warnings. This may simply be planned for P6.

**L1 — adjacent macro names need spaces (spec §3.2, §6.1; design §13 entry 64; S/doc).**
- Problem: `C*VC*#` lexes `VC` as one identifier, which gives "unknown macro 'VC' — did you
  mean 'V'?". The rule is consistent (identifiers are maximal munch), but the notes' style
  `#C*___` and `V___` makes gluing look normal.
- Suggestion: add one sentence to §6.1 ("macro names must be separated from each other by
  whitespace or punctuation"). When an unknown name splits into known macros, hint with
  "did you mean `V C`?"

**S1 — syllable features across re-syllabification (spec §5.4, §5.7; S).**
- Problem: Stress lives on syllables, but the spec does not say what happens to it when
  syllabification is recomputed (`/:$`, `!syllabify`, `Persistent yes`). If new syllables
  start blank, every re-syllabification erases stress. This script re-syllabifies only once,
  before stress is assigned.
- Suggestion: a new syllable inherits every syllable-scope value from the old syllable that
  contained its nucleus. If there is none, it uses the feature's default.

**S2 — orphans after a syllable is dissolved (spec §5.4 "Upkeep"; S).**
- Problem: deleting a nucleus (Syncope) dissolves its syllable, and its onset becomes
  unsyllabified. In CĪVITĀTEM, β, and later the u made from it, belong to no syllable. A
  vowel outside any syllable has no Stress and no place for a SyllableMark. Inserted segments
  are attached to a neighbour, but orphans are not.
- Suggestion: treat orphans like inserted segments: they join the syllable of the left
  neighbour in the same word, as a coda, or else the right neighbour's, as an onset. An
  alternative is to state that `!syllabify` must be called explicitly.

**S3 — hiatus in MaxOnset (spec §5.7; S).**
- Problem: "maximal runs that match `Nucleus`, resolving VV runs with NucleusPreference"
  suggests that with `Nucleus V` a VV sequence gives *one* nucleus and makes the other vowel
  a margin. Latin FĪ.LI.UM, A.QU.AM and A.RĀ.NE.AM need hiatus: every V a nucleus. The
  stress rules here are written segmentally, so they do not depend on the answer, but the
  intended syllable count matters for `.` and for SyllableMark.
- Suggestion: the Nucleus template is matched once per nucleus, and NucleusPreference only
  applies when a longer template (e.g. `V (V)`) could take several analyses. Alternatively,
  add a `Hiatus yes|no` setting.

**S4 — "unstressed" and unspecified syllable features (spec §5.4, §6.2; S).**
- Problem: a syllable that is never assigned Stress has it unspecified. Whether `<2 Stress`
  holds for an unspecified value is undefined, so the script first runs `V --> {0Stress}`
  (rule Unstressed). Segments inserted at a word edge next to unsyllabified material (the
  prothetic e) get no syllable, so they see `_Stress`, not `0Stress`.
- Suggestion: say that comparisons fail on unspecified values, and allow a
  `Default=` on `Scope(Syllable)` features (e.g. `Stress Scalar(0,2) Scope(Syllable) Default=0`)
  that new syllables receive.

**S5 — context-free spelling (spec §5.6; S).**
- Problem: rendering is per segment, so Spanish c/qu (k), c/z (θ), g/gu and r/rr cannot be
  expressed. `$Sp` works for this lexicon only because it avoids those contexts: no k or g
  before e/i, no word-initial trill, no θ before a back vowel. DECEM fails partly for this
  reason.
- Suggestion: document the idiom of a respelling `Rules` block over a spelling-oriented
  segment inventory, or add optional context to orthography lines, e.g.
  `[qu] {…k} / ___{-Back +Syll}`.

**S6 — `'` cannot precede a macro (design §13 entry 65; S).**
- Problem: `'TSH` (replace with the macro's bundle) is not allowed, because `'` must be
  written immediately before `{`, `[` or `$n`. The macros `TSH` and `LY` therefore list
  unsets (`_High _Low _Back _Round _Long`) so that a modified glide renders as tʃ.
- Suggestion: allow `'Name` when `Name` expands to a single segment spec. The same applies
  to `~Name`, which §13 entry 81 already allows.

**S7 — graphemes shared across natural classes (spec §5.6; S, minor).**
- Problem: in `$Sp`, both the vowel i and the glide j must spell `<i>`. Declaring `[i]` twice
  is a conflict, so the script ignores `Syll` and `Cons` in `$Sp`. That works, but it means
  $Sp cannot tell vowels from glides for any other purpose.
- Suggestion: none is needed. The ignore set is the right tool; it deserves an example in §5.6.

**Fixed 2026-09-12 (after P5): the ATR reset on long ā.** The notes claimed that
`{+Low} --> {-ATR}` resets the +ATR that QualityForQuantity gives a long ā, as in CĪVITĀTEM
and CLĀVEM. The P5 engine showed it does not: under spec §4.5 an implication re-fires only
when a feature its trigger reads changes, and Low was unchanged. The rendered IPA was
unaffected, but the segment kept +ATR. The fix adds `{+Low +ATR} --> {-ATR}` to the
Phonology. Its trigger reads ATR, so it re-fires. The two step tests in
`tests/test_latin_spanish.py` that had been marked as expected failures now pass. This is
a useful illustration of implication triggering.

**Found by the P6 runtime (2026-09-12, P6).** Running
`python3 -m yasc examples/latin-spanish/latin-spanish.yasc examples/latin-spanish/lexicon.tsv --allow-pending`
skips `!syllabify` (P7) and leaves Stress on the segments (design §13 entry 97), so the IPA
has no `ˈ`. Apart from stress marks it reproduces `expected_ipa` for every derived word
except those listed under E1. The example's rules were **not** changed.
- **E1 — `r` is not a `Liquid` ($Lat; O).** `[R r]` declares no `Nasal` value, and no
  implication supplies one: only `{+Lateral}`, `{-Son}` and `{-Cons}` imply `-Nasal`. So `r`
  fails `Liquid === {+Cons +Son -Nasal}`, and Lenition's `V___(<<Liquid|Glide>>)V` never
  sees a stop before r. PETRAM, CAPRAM and MĀTREM give [ˈpjetɾa], [ˈkapɾa] and [ˈmatɾe]
  (spelled pietra, quapra, matre) instead of piedra, cabra and madre. The same gap affects
  the `Onset` template (P7) and the muta-cum-liquida test of StressClosedPenult. Fix: add
  `-Nasal` to the `[R r]` line.
- **E2 — $Sp spells k as `qu` (S5).** In `$Sp`, `[c]` is the affricate ts
  (`{+DelRel +Strident}`) and `[qu]` is k, so every k is written `qu`: CAPILLUM, CAPRAM,
  SCHOLAM and CANTĀRE come out as quabello, quapra, esquuela and quantar. The comment "k is
  always <c> and ts always <c>" cannot hold, because two different segments cannot share
  a grapheme in a context-free orthography. Fix: spell k `c` and ts `ç`, the Old Spanish
  letter (but ciudad and ciento then need `c` before front vowels, which is issue S5 again),
  or add a respelling `Rules` block.
- **E3 — DECEM is derived after all.** lexicon.tsv flags DECEM as NOT DERIVED, but `[t]` in
  `VoicelessStop` is a non-strict spec, so it also matches the affricate ts (t plus
  `+DelRel +Strident`). So Lenition gives ts > dz between vowels (checked: `dɛtsɛ` →
  `dɛdzɛ`). Then Apocope after dz, SibilantDevoicing and Interdentalisation give [ˈdjeθ],
  spelled *diez*: exactly the expected forms. The note in lexicon.tsv and §1.6 is stale.
  If the match is unintended, use strict `'[t]` alternatives.
- *Cosmetic:* `%O[$Lat]{0} $input` re-renders the parsed input, so SPATHAM, SCHOLAM and AQUAM
  are echoed as SPATAM, SCOLAM and ACUAM; `%I{0}` would echo the raw text.
- Consequence: `tests/test_latin_spanish.py` `test_lexicon_end_to_end`, which skips until
  P7, will fail on PETRAM, CAPRAM, MĀTREM, CAPILLUM, SCHOLAM and CANTĀRE until E1 and E2 are
  fixed.

**Fixed 2026-09-12 (after P6): E1, E2, E3.**
- **E1:** `[R r]` in `$Lat` now declares `-Nasal`, so r is a `Liquid`. Lenition before r now
  applies: PETRAM gives piedra, CAPRAM cabra, MĀTREM madre.
- **E2:** a respelling block at `!date 1500` now runs on a copy of the form. It saves
  `$pron`, marks the copy, stores it as `$spell`, then restores `$_ := $pron`. The marking
  rules are:
  - SpellQu: k before a front vowel or j gets the orthography-only feature `SpellMark`,
    which `$Sp` spells `<qu>`;
  - SpellSoftC: ts before a front vowel is rewritten as k, spelled `<c>`;
  - SpellZ: any other ts becomes dz, spelled `<z>`.

  In `$Sp`, `<c>` is now plain k. This works around issue S5 (the orthography is
  context-free) without language changes. The IPA output never sees the marks.
- **E3:** DECEM is no longer flagged NOT DERIVED in lexicon.tsv. §1.6 above still describes
  the old analysis and is kept as the historical record.
