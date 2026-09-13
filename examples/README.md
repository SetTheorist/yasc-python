# YASC examples

Every directory here has an expected-output file, and `tests/test_examples.py` runs each one
through the command line and compares its output with that file. Run the commands from the
project root.

| Example | What it shows | Run it |
|---|---|---|
| `simple/` — Grimm's law | a hand-written Phonology and Orthography; dated rules (`!date`) and dated loanwords; a lexical restriction (`/:L+ {!N}`); variables and `!print` | `python3 -m yasc examples/simple/grimm.yasc examples/simple/lexicon.tsv` |
| `latin-spanish/` — Latin → Spanish | 40 rules: feature geometry, strong and weak implications, syllabification with derived stress, three orthographies (Latin in, IPA and Spanish spelling out), a saved `!date 1500` form for spelling. 47 of 48 words are right; `notes.md` explains the design and the one miss (FĒMINAM) | `python3 -m yasc examples/latin-spanish/latin-spanish.yasc examples/latin-spanish/lexicon.tsv` |
| `tone/` — Bantu-style tone | a `Tier` feature; floating tones in the input; `!associate`, H doubling, default L, `!ocp`, and downstep as a tier-only rule (`/:T Tone`) | `python3 -m yasc examples/tone/tone.yasc examples/tone/lexicon.tsv` |
| `dialects/` — a proto-language splits | `!dialects (West East)`, rules restricted with `/:D+` and `/:D-`, a `dialect` column, one column per daughter with `--wide` | `python3 -m yasc examples/dialects/proto.yasc examples/dialects/lexicon.tsv --wide` |
| `paradigm/` — a Latin-style declension | `Paradigm` sections, a `paradigm` column, cells restricted by lexical features, a bracketed cell `<N: $_ >` feeding a cyclic rule (`/:C*`), merged identical cells | `python3 -m yasc examples/paradigm/declension.yasc examples/paradigm/lexicon.tsv` |
| `conlang/` — an SCA-style conlang | the quick style: `!include "lib:ipa.yasc"` (X-SAMPA in, IPA out), macros over orthographic classes, class correspondence (`Stop --> Voiced`), counter-feeding order, place assimilation through geometry, vowel harmony with `/*` and `/:F- V`, an optional rule (`/???`) with labelled variants | `python3 -m yasc examples/conlang/conlang.yasc examples/conlang/lexicon.tsv` |
| `ashkari/` — a proto-language and two daughters | a conlanging sample: Proto-Ashkari (X-SAMPA in, IPA out) splits with `!dialects` into Tolmen and Sirevi, about a hundred rules in three blocks; derived stress on the syllable tier, umlaut, apocope and syncope, persistent final devoicing, class correspondence, lenition, nasal vowels, metaphony and breaking; `notes.md` walks through the changes | `python3 -m yasc examples/ashkari/ashkari.yasc examples/ashkari/lexicon.tsv --wide` |
| `revised-example.yasc` | the example script of the specification (Appendix B): every major construct in one short file | `python3 -m yasc examples/revised-example.yasc --word tabi papi` |

Useful options for any of them:

```
python3 -m yasc SCRIPT --check                 # compile only; report every error
python3 -m yasc SCRIPT --list-rules            # id, name, date, line and text of each rule
python3 -m yasc SCRIPT --word FORM --trace     # one form, showing every change
python3 -m yasc SCRIPT LEXICON --json          # one JSON object per record
```

The user manual (`docs/manual.md`) takes its worked examples from these scripts.
