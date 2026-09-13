"""Runnable snippets in the documentation (plan P10; design §13 entry 169).

Convention, documented in ``docs/manual.md`` §0:

- a fenced block whose info string is ``yasc`` is a **complete script**: it must compile
  strictly (no errors, no warnings);
- a line ``<!-- run: INPUT -> OUTPUT; INPUT -> OUTPUT -->`` directly before the fence also
  applies the script to each INPUT and compares the outputs, joined with `` | `` when there
  are several variants. ``INPUT [!N +Loan]`` passes lexical features and ``INPUT @1954``
  a record date (both optional, in that order);
- fragments that are not complete scripts use another info string (``text``).
"""

import os
import re
import unittest

import yasc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ["docs/manual.md", "docs/llm-guide.md", "README.md"]

BLOCK = re.compile(r"(?:<!--\s*run:(?P<run>[^\n]*?)-->[ \t]*\n)?^```yasc[ \t]*\n(?P<body>.*?)^```[ \t]*$",
                   re.S | re.M)
PAIR = re.compile(r"^(?P<inp>.*?)(?:\s+\[(?P<feat>[^\]]*)\])?(?:\s+@(?P<date>-?\d+))?\s*->\s*(?P<out>.*)$")


def snippets(path):
    """``(line, body, [(input, features, date, expected)])`` for every ``yasc`` block of ``path``."""
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for m in BLOCK.finditer(text):
        line = text.count("\n", 0, m.start("body")) + 1
        pairs = []
        for item in (m.group("run") or "").split(";"):
            if item.strip():
                p = PAIR.match(item.strip())
                if p is None:
                    raise ValueError("%s:%d: bad run pair %r" % (path, line, item))
                date = int(p.group("date")) if p.group("date") else None
                pairs.append((p.group("inp").strip(), p.group("feat"), date, p.group("out").strip()))
        out.append((line, m.group("body"), pairs))
    return out


class DocSnippetTests(unittest.TestCase):
    def check(self, path):
        found = snippets(path)
        for line, body, pairs in found:
            with self.subTest(doc=path, line=line):
                sc = yasc.loads(body, "%s:%d" % (path, line))
                self.assertEqual([str(w) for w in sc.compiled.warnings], [])
                for inp, feat, date, expected in pairs:
                    res = sc.apply(inp, features=feat or None, date=date)
                    self.assertIsNone(res.error, res.error and res.error.format())
                    self.assertEqual(" | ".join(v.text for v in res.outputs), expected,
                                     "%s:%d: %s" % (path, line, inp))
        return found

    def test_manual(self):
        found = self.check("docs/manual.md")
        self.assertGreaterEqual(len(found), 20)
        self.assertGreaterEqual(sum(1 for _, _, p in found if p), 15)

    def test_llm_guide(self):
        found = self.check("docs/llm-guide.md")
        self.assertGreaterEqual(len(found), 3)

    def test_readme(self):
        self.assertGreaterEqual(len(self.check("README.md")), 1)

    def test_extractor(self):
        text = "<!-- run: pa -> ba; ta [!N] -> da | ta -->\n```yasc\nX\n```\n"
        m = BLOCK.search(text)
        self.assertEqual(m.group("body"), "X\n")
        p = PAIR.match("ta [!N] -> da | ta")
        self.assertEqual((p.group("inp"), p.group("feat"), p.group("out")), ("ta", "!N", "da | ta"))
        p = PAIR.match("tele fon [!N] @1954 -> telefon")
        self.assertEqual((p.group("inp"), p.group("feat"), p.group("date")), ("tele fon", "!N", "1954"))


if __name__ == "__main__":
    unittest.main()
