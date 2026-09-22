"""Create or update this agent on Vertex AI Agent Engine (Agent Runtime).

First run against a real project 2026-09-22. It refuses to run without an explicit
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
import subprocess
import sys
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[1]

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

# The deploy-time SDK, and also a runtime requirement: the runtime loads the AdkApp wrapper this
# script builds, and AdkApp is how Gemini Enterprise's calls arrive (its
# ``streaming_agent_run_with_events`` puts each authorization's end-user token into session state
# as ``temp:<authorization id>``, the key ``tools.USER_TOKEN_STATE_KEY`` reads). Kept out of
# uv.lock because the agent's own code never imports it.
AGENT_PLATFORM_SDK = "google-cloud-agentplatform[agent-engines,adk]==2.1.3"
# The SDK checks that the runtime's requirements name cloudpickle, which serialises the AdkApp;
# pinned at the version the SDK above resolves to.
CLOUDPICKLE = "cloudpickle==3.1.2"
# Still required at runtime after the 2.0 split: the AdkApp's set_up and Agent Engine's own serving
# code import google.cloud.aiplatform, and the second deploy (2026-09-22) failed to start without
# it. Same release as the SDK.
AIPLATFORM = "google-cloud-aiplatform[agent-engines,adk]==2.1.3"
# Google's build machines: Linux on x86_64, the runtime's Python.
BUILD_PLATFORM = "x86_64-manylinux_2_28"


def check_resolves(requirements: list[str]) -> None:
    """Resolve the runtime's requirements for Google's build platform before uploading anything.

    A conflict otherwise surfaces ten minutes later as "Build failed" in Agent Engine's build log.
    Resolution only: nothing is downloaded beyond package metadata, nothing is installed.
    """
    uv = os.environ.get("UV") or str(AGENT_ROOT / ".uv-bootstrap" / "bin" / "uv")
    result = subprocess.run(
        [
            uv,
            "pip",
            "compile",
            "-",
            "--python-version",
            PYTHON_VERSION,
            "--python-platform",
            BUILD_PLATFORM,
            "--no-header",
            "--quiet",
        ],
        input="\n".join(requirements) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"the runtime requirements do not resolve for {BUILD_PLATFORM} / Python "
            f"{PYTHON_VERSION}:\n{result.stderr.strip()}"
        )


def requirements_from_lock() -> list[str]:
    """Every runtime dependency at the exact version ``uv.lock`` resolved, with its platform marker.

    Exported by uv itself (``uv export --frozen --no-dev``), the tool that wrote the lock, rather
    than read from the lock by hand. The first real deploy (2026-09-22) failed on a hand reading
    that dropped environment markers: ``pywin32`` is locked for ``sys_platform == 'win32'``
    only, and without its marker the Linux build tried to install it and stopped.
    """
    uv = os.environ.get("UV") or str(AGENT_ROOT / ".uv-bootstrap" / "bin" / "uv")
    exported = subprocess.run(
        [
            uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--all-extras",
            "--format",
            "requirements-txt",
        ],
        cwd=AGENT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = [line.strip() for line in exported.splitlines()]
    return sorted(line for line in lines if line and not line.startswith(("#", "-")))


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
    """The ``config`` argument for ``client.runtimes.create`` / ``.update``."""
    config: dict[str, Any] = {
        "display_name": "Verifiable-answer agent",
        "description": (
            "Answers from the ePI query service's tools, quotes verbatim with citations, and "
            "re-checks every quotation through verify_quote before showing it."
        ),
        "staging_bucket": settings["AGENT_ENGINE_STAGING_BUCKET"],
        "requirements": [*requirements_from_lock(), AGENT_PLATFORM_SDK, AIPLATFORM, CLOUDPICKLE],
        # Relative, and main() runs the upload from src/: the SDK archives each extra package by
        # the path as given (``tar.add(path)``), so an absolute path would nest the code under the
        # deploying machine's home directory and the runtime could not import it.
        "extra_packages": ["verifiable_answer_agent"],
        "python_version": PYTHON_VERSION,
        "env_vars": {name: settings[name] for name in RUNTIME_ENV},
    }
    del resource_name  # update takes the name as its own argument, not in the config
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
    print(f"requirements      {len(config['requirements']) - 3} pinned from uv.lock, plus the SDKs")
    check_resolves(config["requirements"])
    print(f"resolves          yes, for {BUILD_PLATFORM} / Python {PYTHON_VERSION}")
    print(f"operation         {'update ' + arguments.update if arguments.update else 'create'}")

    if arguments.dry_run:
        print("\n--dry-run: nothing was sent.")
        return 0

    # Imported here, not at module scope: the Agent Platform SDK is a deploy-time dependency and
    # is deliberately absent from this project's runtime lock. See deploy/README.md. Since the
    # 2.0 split (2026-08-28) it is the ``agentplatform`` module, not ``vertexai``, and Agent
    # Engine's create/update live on ``client.runtimes``.
    try:
        import agentplatform
        from agentplatform.frameworks import AdkApp
    except ModuleNotFoundError:
        raise SystemExit(
            "the Agent Platform SDK is not installed. It is a deploy-time dependency, kept out "
            f"of uv.lock on purpose:\n  uv run --with '{AGENT_PLATFORM_SDK}' "
            "python deploy/deploy_agent_engine.py"
        ) from None

    from verifiable_answer_agent.agent import build_agent
    from verifiable_answer_agent.config import AgentConfig

    app = AdkApp(agent=build_agent(AgentConfig.from_env()))
    os.chdir(AGENT_ROOT / "src")  # see extra_packages in build_config
    client = agentplatform.Client(
        project=settings["AGENT_ENGINE_PROJECT"], location=settings["AGENT_ENGINE_LOCATION"]
    )
    if arguments.update:
        engine = client.runtimes.update(name=arguments.update, agent=app, config=config)
    else:
        engine = client.runtimes.create(agent=app, config=config)
    print(f"\nresource name     {engine.api_resource.name}")
    print("Register this resource name in the Agent Gallery — see deploy/README.md step 3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
