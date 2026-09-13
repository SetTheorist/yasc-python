# YASC: Yet Another Sound Changer

YASC applies sound changes written the way phonologists write them (`A --> B / C ___ D`) over
distinctive-feature bundles. It has feature geometry, strong and weak implications,
syllabification with syllable features, autosegmental tone tiers, ordered and grouped rules,
dated rules and loanwords, optional rules with labelled variants, dialects and paradigms. It
is for historical linguists, conlangers and LLM-driven research, and is pure Python 3 (3.9 or
later), standard library only.

**Status:** version 1 is complete (plan phases P0–P10). It includes a standard library of IPA
and X-SAMPA features and orthographies, and two tools.

## Try it

There is nothing to install: run it from this directory with `python3 -m yasc`. A complete
script, using the standard library:

<!-- run: pataka -> padak; Sapa -> ʃap; pa -> pa -->
```yasc
!include "lib:ipa.yasc"                  %% standard features, X-SAMPA and IPA
!orthography input $XSAMPA output $IPA   %% type X-SAMPA, print IPA
V === {+Syll}
C === {-Syll}
Rules [[
  {-Son} --> {+Voice} / V ___ V          /" Voicing
  V --> 0 / ___ #  /! # C* ___           /" Apocope
  {-Son} --> {-Voice} / ___ #            /" Devoicing
]]
```

```
$ python3 -m yasc first.yasc --word pataka Sapa pa
pataka	padak
Sapa	ʃap
pa	pa
```

More commands:

```
python3 -m yasc examples/simple/grimm.yasc examples/simple/lexicon.tsv   # a lexicon (TSV)
python3 -m yasc SCRIPT --word pater --trace          # show each change
python3 -m yasc SCRIPT LEXICON --json                # JSON Lines, one object per record
python3 -m yasc SCRIPT --list-rules                  # id, name, date, line, rule
python3 -m yasc SCRIPT --check                       # compile only, report every error
python3 -m yasc SCRIPT LEXICON --from -450 --to 150  # only rules dated in [A, B]
python3 -m yasc.tools.make_ortho "p t k a i u"       # an Orthography for an inventory
python3 -m yasc.tools.minimize --phones "p t k a i u"   # a minimal distinguishing feature set
```

Exit status: 0 on success, 1 when the script does not load, 2 on usage errors or when a record
failed (the other records are still processed).

From Python:

```python
import yasc
sc = yasc.load("examples/simple/grimm.yasc")
res = sc.apply("pater", features="!N", trace=True)
print([str(v) for v in res.outputs], res.trace[0])
```

## Documents

- [docs/manual.md](docs/manual.md): the user manual, a tutorial and reference with tested
  examples.
- [docs/llm-guide.md](docs/llm-guide.md): a compact reference for LLM agents (syntax, JSON
  schema, workflow, pitfalls).
- [docs/specification.md](docs/specification.md): the language and its interfaces.
- [docs/design.md](docs/design.md): the implementation design, package layout and decisions
  log.
- [examples/README.md](examples/README.md): the examples (Grimm's law, Latin → Spanish, Bantu
  tone, dialects, a paradigm, an SCA-style conlang, a proto-language with two daughters) and
  how to run them.
- `yasc/lib/ipa.yasc`: the standard library; `plan.md`: the implementation phases.
- `orig-notes/`: the original design notes and the prototype `ret.py`, kept read-only.

## Running the tests

```
python3 -m unittest discover -s tests -v
```

The suite includes the examples' expected outputs and every script shown in the manual, the
LLM guide and this README.
