"""`python -m aliasgate` runs the `aliasgate` command (see `aliasgate.cli`)."""

import sys

from aliasgate.cli import main

if __name__ == "__main__":
    sys.exit(main())
