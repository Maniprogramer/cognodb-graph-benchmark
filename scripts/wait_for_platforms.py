#!/usr/bin/env python3
"""Block until every configured platform accepts connections.

`docker compose up -d` returns as soon as the containers are created, which is
well before a database is ready to serve queries. Starting the benchmark then
would record connection-refused errors as if they were platform failures.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bench import config as config_mod  # noqa: E402

TIMEOUT_SECONDS = 240


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    cfg = config_mod.load(repo / "config" / "platforms.yaml", env_file=repo / ".env")

    pending = list(cfg.configured_platforms)
    if not pending:
        print("No platforms configured.")
        return 0

    deadline = time.time() + TIMEOUT_SECONDS
    last_error: dict[str, str] = {}

    while pending and time.time() < deadline:
        still_pending = []
        for platform in pending:
            adapter = None
            try:
                adapter = config_mod.build_adapter(platform)
                adapter.connect()
                print(f"  ready: {platform.name}", flush=True)
            except Exception as exc:
                last_error[platform.name] = f"{type(exc).__name__}: {exc}"
                still_pending.append(platform)
            finally:
                if adapter is not None:
                    try:
                        adapter.close()
                    except Exception:
                        pass
        pending = still_pending
        if pending:
            time.sleep(3)

    if pending:
        print("\nTimed out waiting for:", file=sys.stderr)
        for platform in pending:
            print(f"  {platform.name}: {last_error.get(platform.name)}", file=sys.stderr)
        return 1

    print("All configured platforms are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
