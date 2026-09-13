"""Entry point for ``python -m yasc`` (spec §11.1); delegates to :func:`yasc.cli.main`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
