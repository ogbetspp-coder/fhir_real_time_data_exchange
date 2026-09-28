"""The base of every generated contract model: absent is not ``null``.

Zod, the contracts' source of truth, refuses ``null`` for an optional field: ``.optional()``
means the key may be absent, never that its value may be ``null``, and no contract field is
nullable. datamodel-code-generator writes an optional field as ``X | None = None``, so a
generated model took ``{"runId": null}`` for an absent ``runId`` and dumped it back without the
key, a document whose canonical JSON, and so whose hash, is not the one it read (audit C-7).
``scripts/generate_models.py`` makes this class the base of every generated model, and the
validator below refuses ``null`` for any field the model declares.

The FHIR resources inside a Bundle are open models (``extra="allow"``); an unknown key there is
not a declared field, so its value is carried as it is, ``null`` included, as Zod's
``looseObject`` carries it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


class ContractModel(BaseModel):
    """A generated contract model: every declared field is present with a value, or absent."""

    @model_validator(mode="before")
    @classmethod
    def _absent_is_not_null(cls, data: Any) -> Any:
        """Refuses ``null`` for a declared field.

        Args:
            data: The input, before field validation.

        Returns:
            The input, unchanged.

        Raises:
            ValueError: A declared field is ``null``.
        """
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                key = field.alias or name
                if key in data and data[key] is None:
                    raise ValueError(f"{key} is null; an optional field is absent, never null")
        return data
