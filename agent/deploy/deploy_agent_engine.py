r"""Create or update this agent on Vertex AI Agent Engine (Agent Runtime).

First run against a real project 2026-09-22. It refuses to run without an explicit
environment — no project inferred from Application Default Credentials, no region defaulted, no
bucket guessed — because an agent deployed into the wrong project is a data-residency incident,
not a typo.

Requirements are pinned from ``uv.lock``, so the runtime installs exactly the versions the gate
ran against. Read ``deploy/README.md`` before using this: the Agent Engine deploy is one of
five steps, and the other four are console and Terraform work.

    AGENT_ENGINE_PROJECT=... AGENT_ENGINE_LOCATION=... AGENT_ENGINE_STAGING_BUCKET=gs://... \
    QUERY_SERVICE_MCP_URL=... AGENT_MODEL=... \
    uv run --frozen python deploy/deploy_agent_engine.py --dry-run

``AGENT_SERVICE_VERSION`` is not typed by the operator: it is derived as
``agent/<package version>+<commit>``, and the script refuses to package an ``agent/`` tree that
differs from its commit, so the version on every audit record names the code that wrote it
(audit AG-9: the deployed build was once typed by hand and predated the fixes it was assumed to
carry).
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
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
)

# Passed through when set; AGENT_SERVICE_VERSION is always set, by this script.
RUNTIME_ENV = ("QUERY_SERVICE_MCP_URL", "AGENT_MODEL", "AGENT_SERVICE_VERSION")
OPTIONAL_RUNTIME_ENV = ("MCP_TIMEOUT_SECONDS",)

# A Secret Manager secret in the agent's project holding the key the audit record's
# principalDigest is made under. Optional: without it a withheld (e-mail) session user is not
# identified in the agent's record at all. Passed as a secret reference, never as a value, so the
# key is neither in this process nor in the engine's configuration; the runtime's identity needs
# roles/secretmanager.secretAccessor on it.
PRINCIPAL_DIGEST_SECRET = "AGENT_PRINCIPAL_DIGEST_SECRET"
# The service account the engine runs as. Optional: without it Agent Engine uses the project's
# shared Reasoning Engine service agent, which also mints the agent's Cloud Run edge token, so
# anything else deployed on Agent Engine in the project could reach the query service's edge too.
SERVICE_ACCOUNT = "AGENT_ENGINE_SERVICE_ACCOUNT"

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
    """The deploy settings, every variable in ``REQUIRED_ENV`` read from the environment.

    The optional ones (``OPTIONAL_RUNTIME_ENV``, ``AGENT_PRINCIPAL_DIGEST_SECRET``,
    ``AGENT_ENGINE_SERVICE_ACCOUNT``) are included when set.

    Raises:
        SystemExit: A variable is unset or blank, the staging bucket is not a ``gs://`` URL, or
            ``AGENT_SERVICE_VERSION`` is set (it is derived, never typed).
    """
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(
            "refusing to deploy: set "
            + ", ".join(missing)
            + ". There is no default project, region, or bucket."
        )
    if os.environ.get("AGENT_SERVICE_VERSION", "").strip():
        raise SystemExit(
            "refusing to deploy: unset AGENT_SERVICE_VERSION. It is derived from the package "
            "version and the commit, so it always names the code that is deployed."
        )
    settings = {name: os.environ[name].strip() for name in REQUIRED_ENV}
    for name in (*OPTIONAL_RUNTIME_ENV, PRINCIPAL_DIGEST_SECRET, SERVICE_ACCOUNT):
        value = os.environ.get(name, "").strip()
        if value:
            settings[name] = value
    bucket = settings["AGENT_ENGINE_STAGING_BUCKET"]
    if not bucket.startswith("gs://"):
        raise SystemExit("AGENT_ENGINE_STAGING_BUCKET must start with gs://")
    return settings


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=AGENT_ROOT, check=True, capture_output=True, text=True
    ).stdout


# The branch a deployed commit must already be on: what is deployed has been through the merge
# gate. Read from the local remote-tracking ref, so fetch before deploying.
RELEASED_REF = "origin/main"


def service_version() -> str:
    """``agent/<package version>+<commit>``, for a clean ``agent/`` tree at a released commit.

    Raises:
        SystemExit: Anything under ``agent/`` differs from the commit (modified, staged or
            untracked, ignored files aside — the upload is taken from the commit itself, see
            ``export_package``), the commit is not on ``origin/main``, or it cannot be read.
    """
    try:
        dirty = _git("status", "--porcelain", "--untracked-files=all", "--", ".").strip()
        commit = _git("rev-parse", "HEAD").strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"refusing to deploy: cannot read the commit ({error})") from None
    if dirty:
        raise SystemExit(
            "refusing to deploy: agent/ differs from its commit, so the deployed code would not "
            "be the code the version names. Commit or remove:\n" + dirty
        )
    try:
        _git("merge-base", "--is-ancestor", commit, RELEASED_REF)
    except OSError, subprocess.CalledProcessError:
        raise SystemExit(
            f"refusing to deploy: {commit[:12]} is not on {RELEASED_REF}. Deploy a merged commit "
            "(git fetch first, so the local ref is current)."
        ) from None
    from verifiable_answer_agent import __version__

    return f"agent/{__version__}+{commit}"


def export_package(commit: str, destination: Path) -> Path:
    """The package exactly as ``commit`` holds it, written under ``destination``; its parent.

    ``git archive`` of ``agent/src/verifiable_answer_agent`` at the commit: no ignored file
    (``__pycache__``, a stray ``.pyc``), no untracked file and no edit reaches the upload, which
    a clean ``git status`` alone cannot promise (review of PR #129, L4).
    """
    root = Path(_git("rev-parse", "--show-toplevel").strip())
    archive = subprocess.run(
        ["git", "archive", "--format=tar", f"{commit}:agent/src", "verifiable_answer_agent"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    return destination


def build_config(settings: dict[str, str]) -> dict[str, Any]:
    """The ``config`` argument for ``client.runtimes.create`` / ``.update``.

    ``settings`` must carry ``AGENT_SERVICE_VERSION`` (``service_version``).
    """
    env_vars: dict[str, Any] = {name: settings[name] for name in RUNTIME_ENV}
    env_vars.update({name: settings[name] for name in OPTIONAL_RUNTIME_ENV if name in settings})
    if PRINCIPAL_DIGEST_SECRET in settings:
        # A reference Agent Engine resolves at run time: the key never passes through here.
        env_vars["AGENT_PRINCIPAL_DIGEST_KEY"] = {
            "secret": settings[PRINCIPAL_DIGEST_SECRET],
            "version": "latest",
        }
    config: dict[str, Any] = {
        "display_name": "Verifiable-answer agent",
        "description": (
            "Answers from the ePI query service's tools, quotes verbatim with citations, and "
            "re-checks every quotation through verify_quote before showing it."
        ),
        "staging_bucket": settings["AGENT_ENGINE_STAGING_BUCKET"],
        "requirements": [*requirements_from_lock(), AGENT_PLATFORM_SDK, AIPLATFORM, CLOUDPICKLE],
        # Relative, and main() runs the upload from an exported src/: the SDK archives each one by
        # the path as given (``tar.add(path)``), so an absolute path would nest the code under the
        # deploying machine's home directory and the runtime could not import it.
        "extra_packages": ["verifiable_answer_agent"],
        "python_version": PYTHON_VERSION,
        "env_vars": env_vars,
    }
    if SERVICE_ACCOUNT in settings:
        config["service_account"] = settings[SERVICE_ACCOUNT]
    return config


def main(argv: list[str]) -> int:
    """Checks the settings and the requirements, then creates or updates the agent.

    With ``--dry-run`` it prints what would be sent and stops before contacting Google Cloud;
    with ``--update`` it updates the named Agent Engine resource instead of creating one.

    Args:
        argv: The command line, program name first.

    Returns:
        The exit status: 0 after a dry run or once the agent is created or updated.

    Raises:
        SystemExit: The environment is incomplete, the requirements do not resolve, or the
            Agent Platform SDK is not installed.
    """
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
    settings["AGENT_SERVICE_VERSION"] = service_version()
    # The agent is built from this process's environment below and pickled with its version.
    os.environ["AGENT_SERVICE_VERSION"] = settings["AGENT_SERVICE_VERSION"]
    config = build_config(settings)

    print(f"service version   {settings['AGENT_SERVICE_VERSION']}")
    print(f"project           {settings['AGENT_ENGINE_PROJECT']}")
    print(f"location          {settings['AGENT_ENGINE_LOCATION']}")
    print(f"staging bucket    {settings['AGENT_ENGINE_STAGING_BUCKET']}")
    print(f"python            {PYTHON_VERSION}")
    runs_as = settings.get(SERVICE_ACCOUNT, "the shared Reasoning Engine service agent")
    print(f"runs as           {runs_as}")
    print(
        "principal digest  "
        + (
            f"keyed by secret {settings[PRINCIPAL_DIGEST_SECRET]}"
            if PRINCIPAL_DIGEST_SECRET in settings
            else "off: an e-mail session user is withheld and not identified"
        )
    )
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
    # The upload runs from a copy of the package as the commit holds it, not from the working
    # tree (see extra_packages in build_config, and export_package).
    upload_root = Path(tempfile.mkdtemp(prefix="agent-upload-"))
    commit = settings["AGENT_SERVICE_VERSION"].rsplit("+", 1)[1]
    os.chdir(export_package(commit, upload_root))
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
