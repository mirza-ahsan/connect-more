"""uv run python -m coordinator ... (see coordinator/run.py)"""

import sys

from coordinator.run import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
