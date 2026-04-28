"""Generate a bcrypt hash to paste into config.yaml.

Usage:
    python -m app.tools.hashpw
    python -m app.tools.hashpw 'mypassword'   # non-interactive (avoid in shell history)
"""
from __future__ import annotations

import getpass
import sys

from ..auth import hash_password


def main() -> int:
    if len(sys.argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2

    if len(sys.argv) == 2:
        pw = sys.argv[1]
    else:
        pw = getpass.getpass("New admin password: ")
        confirm = getpass.getpass("Confirm: ")
        if pw != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        if len(pw) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            return 1

    print(hash_password(pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
