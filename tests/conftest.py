"""Suite-wide hermeticity: tests must never read the developer's `.env`.

`Settings` resolves from `.env` by default (dev convenience), so a developer with a real,
populated `.env` in the repo root would otherwise leak credentials and mode flags into every
test. The flag below is read at `actionsplane.config` import time, which is why it is set here —
conftest runs before any test module imports app code.
"""

import os

os.environ["ACTIONSPLANE_DISABLE_ENV_FILE"] = "1"
