#!/usr/bin/env python3
"""Not a terminal trust root.

The first executable terminal decision is the fixed root-owned helper
``/usr/lib/engineering-system/benchmark-receipt-verify``. Importing this
module does not verify a tree, load a finalizer, consume telemetry, or
record a result. A same-UID replacement of this file is not authority.
"""

HOST_TERMINAL_HELPER = "/usr/lib/engineering-system/benchmark-receipt-verify"
