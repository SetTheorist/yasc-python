"""YASC -- Yet Another Sound Changer.

A sound-change applier built on generative phonology (distinctive features, feature geometry,
autosegmental tiers), in pure Python 3 with no dependencies. See ``docs/specification.md``
for the language and ``docs/design.md`` for the implementation.

Status: phases P0–P10 complete (version 1). The following exist:

- the error hierarchy of spec §12 (``yasc.errors``);
- the feature system and segments of spec §4 and §6.2 (``yasc.features``, ``yasc.segment``);
- boundary marks and the form protocol (``yasc.marks``);
- the pattern AST, NFA and matcher of spec §6 (``yasc.pattern``, ``yasc.nfa``,
  ``yasc.matcher``);
- forms and orthography of spec §5.1–§5.3 and §5.6 (``yasc.form``, ``yasc.orthography``);
- the lexer, parser, AST, compiler and rule IR of spec §3–§10 (``yasc.lexer``,
  ``yasc.parser``, ``yasc.syntax``, ``yasc.compile``, ``yasc.ir``);
- the rule engine of spec §8: basic rules, the four application modes, filters, visibility,
  groups, persistent rules and dates (``yasc.rules``);
- the script runtime, record readers and CLI of spec §10–§11 (``yasc.runtime``,
  ``yasc.lexicon``, ``yasc.cli``);
- syllabification and syllable-scope features of spec §5.4 and §5.7 (``yasc.syllable``);
- autosegmental tiers of spec §5.5 and §6.5 (``yasc.tiers``);
- category domains, cyclic/optional/stochastic rules, variants, dialects, constraints and
  paradigms of spec §4.6, §8.6–§8.11, §9 and §10.4 (``yasc.variants``, ``yasc.paradigm``);
- the standard library ``yasc/lib/ipa.yasc`` (``!include "lib:ipa.yasc"``) and the tools
  ``yasc.tools.minimize`` and ``yasc.tools.make_ortho`` (plan P10; design §13 entries
  163–173). The user manual is ``docs/manual.md`` and the agent guide ``docs/llm-guide.md``.

Public API (spec §11.2)::

    import yasc
    sc = yasc.load("script.yasc")            # or yasc.loads(text)
    res = sc.apply("pater", features="+N", date=None, trace=True)
    res.outputs        # list[Variant]; str(variant) renders in the output orthography
    res.trace          # list[TraceStep]
    sc.rules           # compiled rules, each with .id, .name, .date, .loc and .source
"""

__version__ = "0.0.1"

from .errors import (  # noqa: E402
    NotImplementedYet,
    SourceLoc,
    YascDefinitionError,
    YascError,
    YascLoadError,
    YascRuntimeError,
    YascSyntaxError,
    YascWarning,
)
from .runtime import Result, Script, Variant, load, loads  # noqa: E402

__all__ = [
    "__version__",
    "load",
    "loads",
    "Script",
    "Result",
    "Variant",
    "SourceLoc",
    "YascError",
    "YascLoadError",
    "YascSyntaxError",
    "YascDefinitionError",
    "YascRuntimeError",
    "YascWarning",
    "NotImplementedYet",
]
