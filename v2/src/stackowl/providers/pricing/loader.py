"""PricingLoader — loads bundled pricing.yaml and estimates per-call cost."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from stackowl.infra.observability import log

PRICING_FALLBACK_KEY = "_local_default"


class PricingLoader:
    """Loads pricing.yaml from the package directory and computes per-call cost.

    The loader is defensive: it never raises during load — it logs the error
    and falls back to an empty pricing table (which then drives every model
    through the `_local_default` zero-cost fallback).
    """

    def __init__(self, path: Path | None = None) -> None:
        log.engine.debug(
            "[pricing] init: entry",
            extra={"_fields": {"path": str(path) if path else None}},
        )
        self._path = path or (Path(__file__).parent / "pricing.yaml")
        self._table: dict[str, dict[str, float]] = self._load()
        log.engine.debug(
            "[pricing] init: exit",
            extra={"_fields": {"models_loaded": len(self._table)}},
        )

    @property
    def table(self) -> dict[str, dict[str, float]]:
        return self._table

    def _load(self) -> dict[str, dict[str, float]]:
        try:
            raw_text = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw_text)
        except FileNotFoundError as exc:
            log.engine.error(
                "[pricing] _load: file missing",
                exc_info=exc,
                extra={"_fields": {"path": str(self._path)}},
            )
            return {}
        except yaml.YAMLError as exc:
            log.engine.error(
                "[pricing] _load: yaml parse failure",
                exc_info=exc,
                extra={"_fields": {"path": str(self._path)}},
            )
            return {}
        except OSError as exc:
            log.engine.error(
                "[pricing] _load: filesystem error",
                exc_info=exc,
                extra={"_fields": {"path": str(self._path)}},
            )
            return {}

        if not isinstance(data, dict):
            log.engine.warning(
                "[pricing] _load: pricing.yaml has no top-level mapping",
                extra={"_fields": {"path": str(self._path)}},
            )
            return {}
        models_block: Any = data.get("models")
        if not isinstance(models_block, dict):
            log.engine.warning(
                "[pricing] _load: 'models' key missing or not a mapping",
                extra={"_fields": {"path": str(self._path)}},
            )
            return {}
        cleaned: dict[str, dict[str, float]] = {}
        for model_name, prices in models_block.items():
            if not isinstance(prices, dict):
                continue
            try:
                cleaned[str(model_name)] = {
                    "input_per_1m": float(prices.get("input_per_1m", 0.0)),
                    "output_per_1m": float(prices.get("output_per_1m", 0.0)),
                }
            except (TypeError, ValueError) as exc:
                log.engine.warning(
                    "[pricing] _load: skipping malformed entry %s: %s",
                    model_name,
                    exc,
                    extra={"_fields": {"model": str(model_name)}},
                )
        return cleaned

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Compute estimated cost (USD). Falls back to _local_default zero-cost."""
        prices = self._table.get(model)
        if prices is None:
            prices = self._table.get(
                PRICING_FALLBACK_KEY,
                {"input_per_1m": 0.0, "output_per_1m": 0.0},
            )
            log.engine.debug(
                "[pricing] estimate: decision — fallback pricing",
                extra={"_fields": {"model": model, "fallback": PRICING_FALLBACK_KEY}},
            )
        cost = (
            input_tokens * prices["input_per_1m"] / 1_000_000.0 + output_tokens * prices["output_per_1m"] / 1_000_000.0
        )
        return float(cost)
