"""Stub module for future GAMET integration."""

from __future__ import annotations

from typing import Mapping, Sequence


class GAMETProvider:
    """Placeholder provider for GAMET products."""

    async def fetch_gamet(self, icaos: Sequence[str]) -> Mapping[str, list[str]]:  # pragma: no cover - stub
        """Fetch GAMET bulletins for the specified stations."""

        raise NotImplementedError("GAMET provider is not implemented yet")


__all__ = ["GAMETProvider"]
