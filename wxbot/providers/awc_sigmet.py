"""Placeholder for future AWC SIGMET integration."""

from __future__ import annotations

from typing import Mapping, Sequence


class AWCSigmetProvider:
    """Future provider for SIGMET information from AWC."""

    async def fetch_sigmet(self, icaos: Sequence[str]) -> Mapping[str, list[str]]:  # pragma: no cover - stub
        """Fetch SIGMET summaries for the specified stations."""

        raise NotImplementedError("SIGMET provider is not implemented yet")


__all__ = ["AWCSigmetProvider"]
