"""The run manifest's rules that its JSON Schema can only describe, enforced for a verifier.

Run manifest 4.0.0 names every package the validator loaded in ``standards.packages`` and the
Global ePI and EMA packages beside them. Two rules tie those fields together, and JSON Schema cannot
state either, so the published schema carries them as the ``ManifestStandards`` description and
Zone B's Zod schema enforces them (``src/contracts/run-manifest.ts``, ``STANDARDS_RULES``):

- ``globalEpiPackage`` and ``emaPackage`` are each the package of an entry of ``packages``;
- no two entries of ``packages`` name the same package (``id#version``); one id may appear at
  several versions.

The generated model (``zone_a.contracts.run_manifest``) cannot carry a validator, since it is
regenerated from the schema, so ``VerifiedRunManifest`` adds one on top of it. An outside verifier
that parses with it refuses what the worker refuses (audit B07, review round 1, Low-2).
"""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from zone_a.contracts.run_manifest import RunManifest


class VerifiedRunManifest(RunManifest):
    """A 4.0.0 run manifest whose standards also satisfy the rules the schema describes."""

    @model_validator(mode="after")
    def _standards_name_what_they_pin(self) -> Self:
        """Refuses a named package that is not pinned, and a package pinned twice.

        Returns:
            The manifest, unchanged.

        Raises:
            ValueError: A rule of the standards does not hold.
        """
        standards = self.root.standards
        named = [entry.package.root for entry in standards.packages]
        for field, value in (
            ("globalEpiPackage", standards.globalEpiPackage.root),
            ("emaPackage", standards.emaPackage),
        ):
            if value not in named:
                raise ValueError(f"{field} is not among the pinned packages")
        if len(set(named)) != len(named):
            raise ValueError("a package is pinned more than once")
        return self
