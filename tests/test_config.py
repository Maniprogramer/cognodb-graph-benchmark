import os

import pytest

from bench import config as config_mod


@pytest.fixture
def yaml_path(tmp_path):
    p = tmp_path / "platforms.yaml"
    p.write_text(
        """
parity_tier:
  vcpu: "0.5 vCPU"
defaults:
  batch_size: 500
platforms:
  - id: cloud
    name: Cloud DB
    adapter: bolt
    flavor: neo4j
    connection:
      uri: "${TEST_URI}"
      user: "${TEST_USER:-defaultuser}"
      password: "${TEST_PASSWORD}"
    spec:
      tier: Free
  - id: nomemauth
    name: Memgraph-like
    adapter: bolt
    flavor: memgraph
    connection:
      uri: "bolt://localhost:7688"
      user: ""
      password: ""
    spec: {}
"""
    )
    return p


class TestInterpolate:
    def test_plain_variable(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert config_mod.interpolate("${FOO}") == "bar"

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        assert config_mod.interpolate("${NOPE:-fallback}") == "fallback"

    def test_env_wins_over_default(self, monkeypatch):
        monkeypatch.setenv("SET", "real")
        assert config_mod.interpolate("${SET:-fallback}") == "real"

    def test_missing_without_default_is_empty(self, monkeypatch):
        monkeypatch.delenv("GONE", raising=False)
        assert config_mod.interpolate("${GONE}") == ""

    def test_nested_structures(self, monkeypatch):
        monkeypatch.setenv("X", "1")
        value = {"a": ["${X}", {"b": "${X}"}]}
        assert config_mod.interpolate(value) == {"a": ["1", {"b": "1"}]}

    def test_non_strings_untouched(self):
        assert config_mod.interpolate(42) == 42
        assert config_mod.interpolate(None) is None


class TestDotenv:
    def test_loads_pairs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DOTENV_KEY", raising=False)
        env = tmp_path / ".env"
        env.write_text('# comment\nDOTENV_KEY="value"\n\nBAD LINE\n')
        config_mod.load_dotenv(env)
        assert os.environ["DOTENV_KEY"] == "value"

    def test_existing_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRESET", "fromenv")
        env = tmp_path / ".env"
        env.write_text("PRESET=fromfile\n")
        config_mod.load_dotenv(env)
        assert os.environ["PRESET"] == "fromenv"

    def test_missing_file_is_noop(self, tmp_path):
        config_mod.load_dotenv(tmp_path / "absent")  # must not raise


class TestLoad:
    def test_unconfigured_platform_is_skipped_not_dropped(self, yaml_path, monkeypatch):
        monkeypatch.delenv("TEST_URI", raising=False)
        monkeypatch.delenv("TEST_PASSWORD", raising=False)
        cfg = config_mod.load(yaml_path)
        cloud = next(p for p in cfg.platforms if p.id == "cloud")
        assert cloud.configured is False
        assert "uri" in cloud.skip_reason and "password" in cloud.skip_reason
        # Still present in the list -- absence would look like an oversight.
        assert len(cfg.platforms) == 2

    def test_configured_when_env_present(self, yaml_path, monkeypatch):
        monkeypatch.setenv("TEST_URI", "bolt://host:7687")
        monkeypatch.setenv("TEST_PASSWORD", "secret")
        cfg = config_mod.load(yaml_path)
        cloud = next(p for p in cfg.platforms if p.id == "cloud")
        assert cloud.configured is True
        assert cloud.connection["user"] == "defaultuser"

    def test_memgraph_flavor_needs_no_credentials(self, yaml_path):
        cfg = config_mod.load(yaml_path)
        mem = next(p for p in cfg.platforms if p.id == "nomemauth")
        assert mem.configured is True, mem.skip_reason

    def test_configured_and_skipped_partitions(self, yaml_path, monkeypatch):
        monkeypatch.delenv("TEST_URI", raising=False)
        monkeypatch.delenv("TEST_PASSWORD", raising=False)
        cfg = config_mod.load(yaml_path)
        assert [p.id for p in cfg.configured_platforms] == ["nomemauth"]
        assert [p.id for p in cfg.skipped_platforms] == ["cloud"]

    def test_defaults_and_parity_tier_exposed(self, yaml_path):
        cfg = config_mod.load(yaml_path)
        assert cfg.defaults["batch_size"] == 500
        assert cfg.parity_tier["vcpu"] == "0.5 vCPU"

    def test_adapter_kwargs_include_name_and_spec(self, yaml_path):
        cfg = config_mod.load(yaml_path)
        mem = next(p for p in cfg.platforms if p.id == "nomemauth")
        kwargs = mem.adapter_kwargs()
        assert kwargs["name"] == "Memgraph-like"
        assert kwargs["flavor"] == "memgraph"
        assert "spec" in kwargs


class TestBuildAdapter:
    def test_unknown_adapter_raises(self):
        bad = config_mod.PlatformConfig(
            id="x", name="X", adapter="nosuch", connection={}, spec={}
        )
        with pytest.raises(ValueError, match="unknown adapter"):
            config_mod.build_adapter(bad)


class TestRealConfigFile:
    """The shipped config must stay loadable and must never contain a secret."""

    def test_repo_config_loads(self):
        from pathlib import Path

        repo_config = Path(__file__).resolve().parents[1] / "config" / "platforms.yaml"
        cfg = config_mod.load(repo_config)
        assert {p.id for p in cfg.platforms} == {
            "cognodb", "neo4j", "memgraph", "arangodb", "falkordb",
        }

    def test_config_contains_no_literal_secrets(self):
        from pathlib import Path

        repo_config = Path(__file__).resolve().parents[1] / "config" / "platforms.yaml"
        text = repo_config.read_text()
        # Every cognodb credential must be an env reference, never a literal.
        for line in text.splitlines():
            if "COGNODB_PASSWORD" in line or "COGNODB_URI" in line:
                assert "${" in line, f"credential not env-referenced: {line}"
