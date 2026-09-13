# Proto-Ashkari and its daughters

A conlanging sample: an invented proto-language, **Proto-Ashkari**, and two daughter
languages derived from it by 97 ordered sound changes in `ashkari.yasc`. The common
changes run first; `!dialects (Tolmen Sirevi)` then splits every word, and each daughter
has its own block of rules (`[[ ... ]] /:D+ Tolmen`, `[[ ... ]] /:D+ Sirevi`).

```
python3 -m yasc examples/ashkari/ashkari.yasc examples/ashkari/lexicon.tsv --wide
python3 -m yasc examples/ashkari/ashkari.yasc --word kalita:nas --trace
```

## Proto-Ashkari

| | |
|---|---|
| consonants | p t k kʷ · b d g gʷ · s h · m n · l r · j w |
| vowels | a e i o u · aː eː iː oː uː (X-SAMPA `a` is a front vowel) |
| syllables | (C)V(C); medial clusters; a few initial clusters (*sr-*, *tl-*, *sn-*, *hr-*) |
| stress | the first long vowel, otherwise the first syllable |

Stress is derived, not typed: the script syllabifies, marks every vowel `0Stress`, and a
`||[[ ]]` group takes the first rule that applies (`StressLong /:1`, then `StressInitial`).
The daughters read it as `2Stress` / `0Stress` on the syllable tier; a final `Destress`
rule removes it so the output is not cluttered with stress marks (`--trace` shows them).

**Common Ashkari** (19 rules): intervocalic *h* is lost and the hiatus contracts (*pihi* >
*piː*) or glides (*tehunas* > *tewnas*); nasals take the place of a following obstruent
(*anku* > *aŋku*); labiovelars lose their rounding before a round vowel (*sokʷu* > *soku*,
*ekʷos* > *ekos*); *mr nr sr tl* are repaired to *mbr ndr str kl*; final obstruents
devoice and final *-m* becomes *-n*.

## Tolmen (north): 39 rules

A consonant-heavy language of closed syllables, in the Germanic mould.

1. **Umlaut** before *i iː j* in the next syllable: *o u* > *ø y*, *a* > *e* (*doru* /
   *dori* > *dor* / *dør*; *kuni* > *tʃyn*); then **a-umlaut** of short *i u* > *e o*
   before *a* (*wira* > *ver*, *wulpa* > *olp*).
2. *s*: nasal loss before *s* with compensatory lengthening (*mansa* > *maːs* > *moːs*);
   Verner-style voicing after an unstressed vowel and rhotacism (*wasaː* > *waraː* >
   *varoː*).
3. **Unstressed vowels**: apocope of a final short vowel, syncope of a medial one
   (right-to-left, iterative), shortening of a final long one, and reduction of the rest to
   *ə* outside the first syllable (*kalitaːnas* > *keltaːnəs*). Final devoicing is
   **persistent** (`/::`), so it catches every consonant that apocope later exposes
   (*bodja* > *bødj* > *bødʒ* > *bøtʃ*).
4. Labiovelars become *kv gv* before a vowel (*kʷatu* > *kvat*, *gʷiːwas* > *gvejvəs*) and
   plain velars elsewhere (*bukʷa* > *bok*).
5. **Clusters**: voicing assimilation; schwa epenthesis before a final sonorant
   (*kambr* > *kambər*); *h-* and *k-* lost before initial consonants (*hrenka* > *riŋk*);
   *w* > *v* before vowels; *k g* > *tʃ j* before front vowels, which umlaut has just
   created (*kalima* > *kelim* > *tʃeləm*); *sj tj dj* > *ʃ tʃ dʒ*; *s* > *ʃ* in initial
   clusters (*sraka* > *ʃtrak*); *ln* > *ll*, nasal + voiced stop > geminate nasal
   (*kambər* > *kammər*, *kandər* > *kannər*); *p k* spirantize before a stop
   (*nakti* > *next*, *laktis* > *lextəs*).
6. **Long vowels**, in counter-feeding order: *iː yː uː* break to *ej øj ow*
   (*pihi* > *piː* > *pej*, *luːkis* > *lyːkəs* > *løjkəs*, *muːna* > *mown*); then
   *eː oː øː* rise to *iː uː yː* (*beːra* > *biːr*); then *aː* rounds to *oː*, which
   is too late to rise (*gʷenaː* > *gvenoː*).

## Sirevi (south): 38 rules

A vowel-heavy language of open syllables, in the Romance mould.

1. Labiovelars become labials by delinking Dorsal (*kʷatu* > *padu*, *gʷenaː* > *bɛna*);
   initial *w* > *b*, medial *w* > *v* (*wira* > *bira*, *rawi* > *ravi*); initial
   *j* > *dʒ* (*jukom* > *dʒugɔ̃*).
2. **Yod** and front vowels palatalize, with class correspondence in one rule
   (`<<[t]|[k]|[d]|[g]>> [j] --> <<[t_s]|[t_S]|[d_Z]|[d_Z]>>`): *matja* > *matsa* > *masa*,
   *tukjas* > *tutʃa*, *kalias* > *kaʎa* > *kaja*; *k* > *tʃ* before *e i* (*kelu* >
   *tʃelu*).
3. *s* and *h*: prothetic *i-* before *sC* and loss of *s* before a stop with compensatory
   lengthening (*stuːla* > *istuːla* > *iːtuːla* > *idula*); *s* > *z* between vowels
   (*kasu* > *kazu*); *h* > 0.
4. **Geminates** from stop + stop and stop + nasal (*nakti* > *natti*, *supnas* > *sunna*),
   which are then immune to **lenition**: between vowels *b d g* > *v ð ɣ* and *p t k* >
   *b d g* (*kʷatu* > *padu*, *tlaka* > *klaka* > *kjaka* > *kjaga*), in counter-feeding order so
   the new voiced stops stay stops.
5. **Nasal vowels**: a nasal before an obstruent or word-finally nasalizes the vowel and
   vanishes (*kanta* > *kãta*, *jukom* > *dʒugɔ̃*). This runs *after* lenition, so the
   *t* of *kãta* was never intervocalic: the classic opaque interaction.
6. *l* vocalizes before a consonant and the diphthong monophthongizes (*elma* > *ewma* >
   *oma*, *dalgas* > *dawgas* > *doːgas* > *doga*).
7. **Vowels**: short *e o* lax to *ɛ ɔ*, giving seven qualities; **metaphony** raises a
   stressed mid vowel before final *-i -u* (*doru* > *dɔru* > *doru*, but *sela* > *sɛla*);
   length is lost; stressed *ɛ ɔ* break to *jɛ wɔ* in open syllables (*sela* > *sjɛla*,
   *bodja* > *bɔʒa* > *bwɔʒa*).
8. Final obstruents are lost, so *kasu* 'house' and *kasus* 'houses' merge as *kazu*;
   final sonorants would take a paragogic *-e*; *r...r* dissimilates to *l...r*; *ʎ* > *j*.

## Vocabulary

| Proto-Ashkari | gloss | Tolmen | Sirevi |
|---|---|---|---|
| *kʷatu* | water | kvat | padu |
| *gʷenaː* | woman | gvenoː | bɛna |
| *sokʷu* | eye | sok | sogu |
| *ekʷos* | horse | ekəs | jɛgɔ |
| *bukʷa* | mouth | bok | buba |
| *gʷiːwas* | alive | gvejvəs | biva |
| *hanti* | hand | hint | ãti |
| *kalima* | to speak | t͡ʃeləm | kalima |
| *kalitaːnas* | speaker | t͡ʃeltoːnəs | kalidana |
| *muːna* | moon | mown | muna |
| *doru* | tree | dor | doru |
| *dori* | trees | dør | dori |
| *stuːla* | star | ʃtowl | idula |
| *luːkis* | light | løjkəs | lut͡ʃi |
| *nakti* | night | next | natti |
| *laktis* | milk | lextəs | latti |
| *beːra* | bear | biːr | bera |
| *rawi* | red | rew | ravi |
| *jukom* | yoke | jukən | d͡ʒugɔ̃ |
| *wira* | man | ver | bira |
| *wulpa* | fox | olp | upa |
| *jiras* | ice | erəs | ira |
| *kanta* | song | kant | kãta |
| *kamra* | room | kammər | kãbra |
| *kanra* | edge | kannər | kãdra |
| *tlaka* | flat | klak | kjaga |
| *sraka* | stream | ʃtrak | idraga |
| *tehunas* | to drink | tewnəs | tona |
| *pihi* | to see | pej | pi |
| *sela* | sun | sel | sjɛla |
| *kelu* | cold | t͡ʃel | t͡ʃelu |
| *dalgas* | long | dalgəs | doga |
| *mansa* | goose | moːs | mãsa |
| *jasi* | wind | jes | d͡ʒazi |
| *kasu* | house | kas | kazu |
| *kasus* | houses | kasəs | kazu |
| *wasaː* | skin | varoː | baza |
| *anku* | iron | aŋk | ãku |
| *tukjas* | hunter | tyt͡ʃəs | tut͡ʃa |
| *matja* | mother | met͡ʃ | masa |
| *bodja* | gift | bøt͡ʃ | bwɔʒa |
| *kalias* | sister | t͡ʃeljəs | kaja |
| *kuni* | kin | t͡ʃyn | kuni |
| *peːkis* | fish | piːkəs | pet͡ʃi |
| *helma* | helmet | helm | oma |
| *alkis* | elk | elkəs | ot͡ʃi |
| *snigʷas* | snow | ʃnegvəs | isniva |
| *supnas* | sleep | sopnəs | sunna |
| *hrenka* | ring | riŋk | rɛ̃ka |
| *niwis* | new | nivəs | nivi |
| *tarkis* | wolf | terkəs | tart͡ʃi |

## Two derivations

`--trace` prints every change. *kalitaːnas* 'speaker' (stress on the long vowel):

```
== kalita:nas
  2  StressLong        kalitaːnas → kaliˈtaːnas
  21  IUmlautLow       kaliˈtaːnas → keliˈtaːnas      a > e before i
  23  AUmlautI         keliˈtaːnas → keleˈtaːnas      i > e before aː
  29  Syncope          keleˈtaːnas → kelˈtaːnas
  31  Reduction        kelˈtaːnas → kelˈtaːnəs
  40  KPalatalization  kelˈtaːnəs → t͡ʃelˈtaːnəs     fed by the umlaut
  58  LongARounding    t͡ʃelˈtaːnəs → t͡ʃelˈtoːnəs
  80  Lenition         kaliˈtaːnas → kaliˈdaːnas
  90  LengthLoss       kaliˈdaːnas → kaliˈdanas
  93  FinalObstruentLoss  kaliˈdanas → kaliˈdana
kalita:nas	t͡ʃeltoːnəs	Tolmen
kalita:nas	kalidana	Sirevi
```

*bodja* 'gift': in Tolmen the umlaut, apocope and the persistent final devoicing; in
Sirevi the yod affricate spirantizes, the vowel laxes and breaks:

```
== bodja
  20  IUmlautRound     ˈbodja → ˈbødja
  27  Apocope          ˈbødja → ˈbødj
  44  DJ               ˈbødj → ˈbød͡ʒ
  28  FinalDevoicing2  ˈbød͡ʒ → ˈbøt͡ʃ       persistent: re-applies after DJ
  63  YodAffrication   ˈbodja → ˈbod͡ʒa
  79  Spirantization   ˈbod͡ʒa → ˈboʒa
  87  MidLaxing        ˈboʒa → ˈbɔʒa
  92  BreakingO        ˈbɔʒa → ˈbwɔʒa
bodja	bøt͡ʃ	Tolmen
bodja	bwɔʒa	Sirevi
```

## Things to know when extending it

- `[...]` in rules is X-SAMPA, and X-SAMPA `a` is `+Front`: a "front vowel" class for
  palatalization or umlaut must say `{+Front -Low}` (or `{+High +Front}`), or every *a*
  triggers it.
- A non-strict `[k]` also matches *kʷ* (its features are a superset), so the labiovelar
  rules come before any rule written with `[k]`.
- Fricatives in the library are `+Cont +DelRel`; a spirantization written as `{+Cont}`
  alone renders approximately (a warning). Both blocks write `{+Cont +DelRel}`.
- The syllable tier carries the stress; an inserted segment joins the syllable it lands
  in, which is why the output is destressed rather than shown with marks.
