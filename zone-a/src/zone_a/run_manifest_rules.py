"""The run manifest's rules between fields, enforced for a verifier.

Zone B's Zod schema (``src/contracts/run-manifest.ts``) holds four rules that tie one field of a
manifest to another. The generated model (``zone_a.contracts.run_manifest``) cannot carry a
validator, since it is regenerated from the schema, so ``VerifiedRunManifest`` adds them on top of
it, and an outside verifier that parses with it refuses what the worker refuses (audit B07, review
round 1, Low-2; audit C-8):

- ``globalEpiPackage`` and ``emaPackage`` are each the package of an entry of ``packages``;
- no two entries of ``packages`` name the same package (``id#version``); one id may appear at
  several versions;
- a document run, and only one, carries an ``ingestion`` block;
- an authority import, and only one, records what Zone B fetched (``ingestion.authority``).

JSON Schema cannot state the first two, so the published schema carries them as the
``ManifestStandards`` description (``STANDARDS_RULES``). It states the last two as ``if``/``then``
(run manifest 5.0.0, ``REFINEMENTS`` in ``src/contracts/json-schema.ts``), which
datamodel-code-generator does not carry into a model.
"""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from zone_a.contracts.run_manifest import RunManifest


class VerifiedRunManifest(RunManifest):
    """A run manifest that also satisfies the rules between its fields."""

    @model_validator(mode="after")
    def _fields_agree(self) -> Self:
        """Refuses a manifest whose fields break a rule the schema states or describes.

        Returns:
            The manifest, unchanged.

        Raises:
            ValueError: A rule does not hold.
        """
        manifest = self.root
        standards = manifest.standards
        named = [entry.package.root for entry in standards.packages]
        for field, value in (
            ("globalEpiPackage", standards.globalEpiPackage.root),
            ("emaPackage", standards.emaPackage),
        ):
            if value not in named:
                raise ValueError(f"{field} is not among the pinned packages")
        if len(set(named)) != len(named):
            raise ValueError("a package is pinned more than once")
        document = manifest.source.kind.value == "document"
        if document != (manifest.ingestion is not None):
            raise ValueError("a document run, and only one, carries an ingestion block")
        if manifest.ingestion is not None:
            imported = manifest.ingestion.sourceKind.value == "authority-publication"
            if imported != (manifest.ingestion.authority is not None):
                raise ValueError("an authority import, and only one, records what Zone B fetched")
        return self
