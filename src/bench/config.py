"""Configuration loading with environment-variable interpolation.

No credential is ever stored in the repository. Every secret in
config/platforms.yaml is a ``${VAR}`` or ``${VAR:-default}`` reference resolved
against the environment at load time. A platform whose required values are
missing is marked unconfigured and reported as skipped in the results, rather
than being dropped -- an absent platform in a results table looks like an
oversight, whereas an explicit "not configured" row is honest.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(value):
    """Recursively resolve ${VAR} / ${VAR:-default} references."""
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")
        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v) for v in value]
    return value


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables win, so an explicit
    export can always override the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


@dataclass
class PlatformConfig:
    id: str
    name: str
    adapter: str
    connection: dict
    spec: dict
    flavor: str | None = None
    skip_reason: str | None = None

    @property
    def configured(self) -> bool:
        return self.skip_reason is None

    def adapter_kwargs(self) -> dict:
        kwargs = dict(self.connection)
        kwargs["name"] = self.name
        kwargs["spec"] = self.spec
        if self.flavor:
            kwargs["flavor"] = self.flavor
        return kwargs


@dataclass
class BenchmarkConfig:
    parity_tier: dict
    defaults: dict
    platforms: list[PlatformConfig] = field(default_factory=list)

    @property
    def configured_platforms(self) -> list[PlatformConfig]:
        return [p for p in self.platforms if p.configured]

    @property
    def skipped_platforms(self) -> list[PlatformConfig]:
        return [p for p in self.platforms if not p.configured]


#: Connection keys that must be non-empty for a platform to be runnable.
#: Auth keys are required per flavor rather than per adapter: Memgraph and
#: FalkorDB accept unauthenticated connections by default, so demanding a
#: username or password would wrongly report them as unconfigured.
REQUIRED_KEYS = {
    "bolt": ["uri"],
    "arango": ["uri", "user", "password"],
    "falkor": ["host", "port"],
}

#: Adapter/flavor combinations that additionally require credentials.
REQUIRED_AUTH = {
    ("bolt", "neo4j"): ["user", "password"],
}


def load(path: Path, env_file: Path | None = None) -> BenchmarkConfig:
    if env_file is not None:
        load_dotenv(env_file)

    raw = yaml.safe_load(Path(path).read_text())
    resolved = interpolate(raw)

    platforms = []
    for entry in resolved.get("platforms", []):
        connection = entry.get("connection", {}) or {}
        adapter = entry["adapter"]

        required = list(REQUIRED_KEYS.get(adapter, []))
        required += REQUIRED_AUTH.get((adapter, entry.get("flavor")), [])
        missing = [
            key for key in required if not str(connection.get(key, "")).strip()
        ]

        skip_reason = None
        if missing:
            skip_reason = f"not configured (missing: {', '.join(missing)})"

        platforms.append(
            PlatformConfig(
                id=entry["id"],
                name=entry["name"],
                adapter=adapter,
                connection=connection,
                spec=entry.get("spec", {}) or {},
                flavor=entry.get("flavor"),
                skip_reason=skip_reason,
            )
        )

    return BenchmarkConfig(
        parity_tier=resolved.get("parity_tier", {}),
        defaults=resolved.get("defaults", {}),
        platforms=platforms,
    )


def build_adapter(platform: PlatformConfig):
    """Instantiate the adapter for a platform. Imported lazily so a missing
    optional driver only breaks the platform that needs it."""
    if platform.adapter == "bolt":
        from .adapters.bolt import BoltAdapter

        return BoltAdapter(platform.adapter_kwargs())
    if platform.adapter == "arango":
        from .adapters.arango import ArangoAdapter

        return ArangoAdapter(platform.adapter_kwargs())
    if platform.adapter == "falkor":
        from .adapters.falkor import FalkorAdapter

        return FalkorAdapter(platform.adapter_kwargs())
    raise ValueError(f"unknown adapter type: {platform.adapter}")
