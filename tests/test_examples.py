"""Every example directory runs through the CLI and matches its expected-output file
(plan P10 acceptance: "each example has an expected-output test").

``EXAMPLES`` lists each directory of ``examples/`` with its script, lexicon, extra CLI
arguments and expected file. ``test_every_directory_is_listed`` fails when a new example
directory is added without an entry here. The outputs are regression snapshots: for
``latin-spanish`` one word of 48 is known to differ from its ``expected_ipa`` column
(``examples/latin-spanish/notes.md``; ``tests/test_latin_spanish.py`` marks it), and the
snapshot records the current output.
"""

import contextlib
import io
import os
import unittest

from yasc.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_DIR = os.path.join(ROOT, "examples")

#: directory -> (script, lexicon, extra arguments, expected-output file)
EXAMPLES = {
    "simple": ("grimm.yasc", "lexicon.tsv", [], "expected.out"),
    "latin-spanish": ("latin-spanish.yasc", "lexicon.tsv", [], "expected.out"),
    "tone": ("tone.yasc", "lexicon.tsv", [], "expected.out"),
    "dialects": ("proto.yasc", "lexicon.tsv", ["--wide"], "expected-wide.out"),
    "paradigm": ("declension.yasc", "lexicon.tsv", [], "expected.out"),
    "conlang": ("conlang.yasc", "lexicon.tsv", [], "expected.out"),
    "ashkari": ("ashkari.yasc", "lexicon.tsv", ["--wide"], "expected-wide.out"),
}


def run_main(argv):
    """Call the CLI from the project root; return (status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    cwd = os.getcwd()
    try:
        os.chdir(ROOT)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(argv)
    finally:
        os.chdir(cwd)
    return status, out.getvalue(), err.getvalue()


class ExampleTests(unittest.TestCase):
    def test_every_directory_is_listed(self):
        dirs = sorted(d for d in os.listdir(EXAMPLES_DIR) if os.path.isdir(os.path.join(EXAMPLES_DIR, d)))
        self.assertEqual(dirs, sorted(EXAMPLES))

    def test_expected_outputs(self):
        for name, (script, lexicon, extra, expected) in EXAMPLES.items():
            with self.subTest(example=name):
                d = os.path.join("examples", name)
                status, out, err = run_main([os.path.join(d, script), os.path.join(d, lexicon)] + extra)
                self.assertEqual(status, 0, err)
                with open(os.path.join(ROOT, d, expected), encoding="utf-8") as fh:
                    self.assertEqual(out, fh.read())

    def test_scripts_compile_strictly(self):
        scripts = [os.path.join("examples", n, s) for n, (s, _, _, _) in EXAMPLES.items()]
        scripts.append(os.path.join("examples", "revised-example.yasc"))
        for script in scripts:
            with self.subTest(script=script):
                status, out, err = run_main([script, "--check"])
                self.assertEqual(status, 0, err)
                self.assertIn(" 0 warnings", out)

    def test_examples_readme_mentions_every_example(self):
        with open(os.path.join(EXAMPLES_DIR, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        for name in list(EXAMPLES) + ["revised-example.yasc"]:
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
