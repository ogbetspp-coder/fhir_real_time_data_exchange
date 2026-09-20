"""Create or update this agent on Vertex AI Agent Engine. Documented, scripted, not executed.

Nothing here has been run against a real project. It refuses to run without an explicit
environment — no project inferred from Application Default Credentials, no region defaulted, no
bucket guessed — because an agent deployed into the wrong project is a data-residency incident,
not a typo.

Requirements are pinned from ``uv.lock``, so the runtime installs exactly the versions the gate
ran against. Read ``deploy/README.md`` before using this: the Agent Engine deploy is one of
five steps, and the other four are console and Terraform work.

    AGENT_ENGINE_PROJECT=... AGENT_ENGINE_LOCATION=... AGENT_ENGINE_STAGING_BUCKET=gs://... \\
    QUERY_SERVICE_MCP_URL=... AGENT_MODEL=... AGENT_SERVICE_VERSION=... \\
    uv run --frozen python deploy/deploy_agent_engine.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = AGENT_ROOT / "uv.lock"
PACKAGE_NAME = "verifiable-answer-agent"

# Pinned in pyproject.toml; Agent Engine defaults to 3.10, which is four minors behind the lock.
PYTHON_VERSION = "3.14"

REQUIRED_ENV = (
    "AGENT_ENGINE_PROJECT",
    "AGENT_ENGINE_LOCATION",
    "AGENT_ENGINE_STAGING_BUCKET",
    # The agent's own runtime configuration, passed through as environment variables so the
    # deployed agent reads them the same way the local one does.
    "QUERY_SERVICE_MCP_URL",
    "AGENT_MODEL",
    "AGENT_SERVICE_VERSION",
)

RUNTIME_ENV = ("QUERY_SERVICE_MCP_URL", "AGENT_MODEL", "AGENT_SERVICE_VERSION")


def requirements_from_lock(lockfile: Path = LOCKFILE) -> list[str]:
    """Every runtime dependency at the exact version ``uv.lock`` resolved, and no other.

    The lock is the only place a version is decided in this project, so the deployed runtime is
    reconstructed from it rather than from a hand-written list that can drift. Dev-only
    packages are excluded by walking the dependency graph from this package's own
    ``dependencies``, not by listing everything the lock mentions.
    """
    lock: dict[str, Any] = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    root = packages[PACKAGE_NAME]

    wanted: set[str] = set()
    frontier = [dependency["name"] for dependency in root.get("dependencies", [])] + [
        dependency["name"]
        for group in root.get("optional-dependencies", {}).values()
        for dependency in group
    ]
    while frontier:
        name = frontier.pop()
        if name in wanted or name == PACKAGE_NAME:
            continue
        wanted.add(name)
        package = packages.get(name)
        if package is None:
            continue
        frontier.extend(dependency["name"] for dependency in package.get("dependencies", []))
        frontier.extend(
            dependency["name"]
            for group in package.get("optional-dependencies", {}).values()
            for dependency in group
        )
    return sorted(f"{name}=={packages[name]['version']}" for name in wanted if name in packages)


def read_environment() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(
            "refusing to deploy: set "
            + ", ".join(missing)
            + ". There is no default project, region, or bucket."
        )
    settings = {name: os.environ[name].strip() for name in REQUIRED_ENV}
    bucket = settings["AGENT_ENGINE_STAGING_BUCKET"]
    if not bucket.startswith("gs://"):
        raise SystemExit("AGENT_ENGINE_STAGING_BUCKET must start with gs://")
    return settings


def build_config(settings: dict[str, str], resource_name: str | None) -> dict[str, Any]:
    """The ``config`` argument for ``client.agent_engines.create`` / ``.update``."""
    config: dict[str, Any] = {
        "display_name": "Verifiable-answer agent",
        "description": (
            "Answers from the ePI query service's tools, quotes verbatim with citations, and "
            "re-checks every quotation through verify_quote before showing it."
        ),
        "staging_bucket": settings["AGENT_ENGINE_STAGING_BUCKET"],
        "requirements": requirements_from_lock(),
        "extra_packages": [str(AGENT_ROOT / "src" / "verifiable_answer_agent")],
        "python_version": PYTHON_VERSION,
        "env_vars": {name: settings[name] for name in RUNTIME_ENV},
    }
    if resource_name:
        config["name"] = resource_name
    return config


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent and exit without contacting Google Cloud",
    )
    parser.add_argument(
        "--update",
        metavar="RESOURCE_NAME",
        default=None,
        help="projects/.../locations/.../reasoningEngines/... to update instead of create",
    )
    arguments = parser.parse_args(argv[1:])

    settings = read_environment()
    config = build_config(settings, arguments.update)

    print(f"project           {settings['AGENT_ENGINE_PROJECT']}")
    print(f"location          {settings['AGENT_ENGINE_LOCATION']}")
    print(f"staging bucket    {settings['AGENT_ENGINE_STAGING_BUCKET']}")
    print(f"python            {PYTHON_VERSION}")
    print(f"requirements      {len(config['requirements'])} pinned from uv.lock")
    print(f"operation         {'update ' + arguments.update if arguments.update else 'create'}")

    if arguments.dry_run:
        print("\n--dry-run: nothing was sent.")
        return 0

    # Imported here, not at module scope: the Vertex AI SDK is a deploy-time dependency and is
    # deliberately absent from this project's runtime lock. See deploy/README.md.
    try:
        import vertexai
    except ModuleNotFoundError:
        raise SystemExit(
            "the Vertex AI SDK is not installed. It is a deploy-time dependency, kept out of "
            "uv.lock on purpose:\n"
            "  uv run --with 'google-cloud-agentplatform[agent-engines,adk]==2.1.3' "
            "python deploy/deploy_agent_engine.py"
        ) from None

    from verifiable_answer_agent.agent import build_agent
    from verifiable_answer_agent.config import AgentConfig

    agent = build_agent(AgentConfig.from_env())
    client = vertexai.Client(
        project=settings["AGENT_ENGINE_PROJECT"], location=settings["AGENT_ENGINE_LOCATION"]
    )
    if arguments.update:
        engine = client.agent_engines.update(name=arguments.update, agent=agent, config=config)
    else:
        engine = client.agent_engines.create(agent=agent, config=config)
    print(f"\nresource name     {engine.api_resource.name}")
    print("Register this resource name in the Agent Gallery — see deploy/README.md step 3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
