"""PricingLoader — loads bundled pricing.yaml and estimates per-call cost."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from stackowl.infra.observability import log

PRICING_FALLBACK_KEY = "_local_default"

#: Conservative default price (USD per 1M tokens, applied to BOTH input and
#: output) used for an unknown CLOUD model. Deliberately high so an unrecognized
#: paid model trips a budget cap rather than silently billing $0 (F128). Override
#: per-deployment via BudgetSettings.unknown_cloud_per_1m_usd.
DEFAULT_UNKNOWN_CLOUD_PER_1M_USD = 15.0


class PricingLoader:
    """Loads pricing.yaml from the package directory and computes per-call cost.

    The loader is defensive: it never raises during load — it logs the error
    and falls back to an empty pricing table.

    A model absent from the table is priced by LOCALITY: a local (self-hosted)
    backend stays $0 (the `_local_default` free fallback); an unknown CLOUD model
    is charged a conservative per-1M fallback (``unknown_cloud_per_1m_usd``) and
    logged at WARNING so an unpriced paid model can never silently bill $0 (F128).
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        unknown_cloud_per_1m_usd: float = DEFAULT_UNKNOWN_CLOUD_PER_1M_USD,
    ) -> None:
        log.engine.debug(
            "[pricing] init: entry",
            extra={"_fields": {
                "path": str(path) if path else None,
                "unknown_cloud_per_1m_usd": unknown_cloud_per_1m_usd,
            }},
        )
        self._path = path or (Path(__file__).parent / "pricing.yaml")
        self._unknown_cloud_per_1m_usd = float(unknown_cloud_per_1m_usd)
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

    def estimate(
        self, model: str, input_tokens: int, output_tokens: int, *, is_local: bool = False
    ) -> float:
        """Compute estimated cost (USD).

        Known model → table price. Unknown model: a LOCAL backend stays $0
        (``_local_default``); an unknown CLOUD model gets the conservative
        ``unknown_cloud_per_1m_usd`` fallback, logged at WARNING (F128) — a paid
        model we don't recognize must never silently bill $0.
        """
        prices = self._table.get(model)
        if prices is None:
            if is_local:
                prices = self._table.get(
                    PRICING_FALLBACK_KEY,
                    {"input_per_1m": 0.0, "output_per_1m": 0.0},
                )
                log.engine.debug(
                    "[pricing] estimate: decision — local model, free fallback",
                    extra={"_fields": {"model": model, "fallback": PRICING_FALLBACK_KEY}},
                )
            else:
                rate = self._unknown_cloud_per_1m_usd
                prices = {"input_per_1m": rate, "output_per_1m": rate}
                log.engine.warning(
                    "[pricing] estimate: UNKNOWN CLOUD MODEL — conservative fallback "
                    "pricing applied (add it to pricing.yaml to price it exactly)",
                    extra={"_fields": {"model": model, "fallback_per_1m_usd": rate}},
                )
        cost = (
            input_tokens * prices["input_per_1m"] / 1_000_000.0 + output_tokens * prices["output_per_1m"] / 1_000_000.0
        )
        return float(cost)
