"""
masxai/oracle.py - objective ground-truth fetchers for forecast resolution.

Design rule: every oracle path fails soft. Missing data returns None so the
validator can defer resolution instead of scoring on bad evidence.
"""

import os
from typing import Any, Optional

import httpx

from masxai import constants as C
from masxai.bt_compat import bt
from masxai.env import load_env
from masxai.protocol import ForecastEventType


async def fetch_price(asset: str = C.FORECAST_ASSET) -> Optional[float]:
    """
    Return current USD price for `asset`, or None on any failure.
    Never raises — resolution must continue across feed outages.
    """
    load_env()
    errors = []
    async with httpx.AsyncClient(timeout=C.ORACLE_TIMEOUT) as client:
        price = await _fetch_coingecko_price(client, asset, errors)
        if price is not None:
            return price
        if asset == C.FORECAST_ASSET:
            price = await _fetch_binance_tao_price(client, errors)
            if price is not None:
                return price
            price = await _fetch_kraken_tao_price(client, errors)
            if price is not None:
                return price

    fallback = os.getenv("MASXAI_FALLBACK_TAO_PRICE_USD")
    if fallback:
        try:
            price = float(fallback)
            bt.logging.warning(
                f"oracle: using MASXAI_FALLBACK_TAO_PRICE_USD={price}; "
                "set only for testnet/dev when public price APIs are unreachable"
            )
            return price
        except ValueError:
            errors.append(f"env fallback invalid: {fallback!r}")

    bt.logging.warning(f"oracle: price fetch failed for {asset}: {' | '.join(errors)}")
    return None


async def _fetch_coingecko_price(
    client: httpx.AsyncClient,
    asset: str,
    errors: list[str],
) -> Optional[float]:
    params = {"ids": asset, "vs_currencies": "usd"}
    try:
        resp = await client.get(C.COINGECKO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        price = data.get(asset, {}).get("usd")
        if price is None:
            errors.append(f"coingecko missing asset={asset}")
            return None
        return float(price)
    except Exception as e:  # noqa: BLE001
        errors.append(f"coingecko: {type(e).__name__}: {e}")
        return None


async def _fetch_binance_tao_price(
    client: httpx.AsyncClient,
    errors: list[str],
) -> Optional[float]:
    try:
        resp = await client.get(C.BINANCE_TAO_URL)
        resp.raise_for_status()
        data = resp.json()
        return float(data["price"])
    except Exception as e:  # noqa: BLE001
        errors.append(f"binance: {type(e).__name__}: {e}")
        return None


async def _fetch_kraken_tao_price(
    client: httpx.AsyncClient,
    errors: list[str],
) -> Optional[float]:
    try:
        resp = await client.get(C.KRAKEN_TAO_URL)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", {})
        first = next(iter(result.values()), None)
        if not first:
            errors.append("kraken missing TAOUSD result")
            return None
        return float(first["c"][0])
    except Exception as e:  # noqa: BLE001
        errors.append(f"kraken: {type(e).__name__}: {e}")
        return None


def resolve_outcome(reference_price: float, resolved_price: float) -> bool:
    """
    Binary outcome for the question "will the price be HIGHER at resolve time?"
    True  = price went up (or flat-up)  -> YES
    False = price went down              -> NO
    """
    return resolved_price >= reference_price


async def fetch_subnet_count(subtensor: Any = None) -> Optional[int]:
    """Return the current subnet count when the SDK exposes it."""
    try:
        if subtensor is None:
            subtensor = bt.subtensor(network=C.NETWORK)
        if hasattr(subtensor, "get_all_subnets_info"):
            return len(subtensor.get_all_subnets_info())
        if hasattr(subtensor, "get_subnets"):
            return len(subtensor.get_subnets())
        if hasattr(subtensor, "subnets"):
            return len(subtensor.subnets())
    except Exception as e:  # noqa: BLE001
        bt.logging.warning(f"oracle: subnet count fetch failed: {e}")
    return None


async def snapshot_reference(event_type: str, asset: str = C.FORECAST_ASSET, subtensor: Any = None) -> Optional[dict]:
    """Fetch the objective issue-time value for an event type."""
    if event_type in {
        ForecastEventType.TAO_PRICE_MOVEMENT.value,
        ForecastEventType.SUBNET_TOKEN_PRICE.value,
    }:
        price = await fetch_price(asset)
        if price is None:
            return None
        return {"reference_value": price, "reference_metadata": {"asset": asset}}

    if event_type == ForecastEventType.NEW_SUBNET_REGISTRATION.value:
        count = await fetch_subnet_count(subtensor=subtensor)
        if count is None:
            return None
        return {"reference_value": float(count), "reference_metadata": {"subnet_count": count}}

    return None


async def resolve_forecast_outcome(forecast: dict, subtensor: Any = None) -> Optional[bool]:
    """Resolve a stored forecast to a boolean outcome, or None if unavailable."""
    event_type = forecast.get("event_type")
    reference_value = forecast.get("reference_value")
    asset = forecast.get("asset") or C.FORECAST_ASSET
    if reference_value is None:
        return None

    if event_type in {
        ForecastEventType.TAO_PRICE_MOVEMENT.value,
        ForecastEventType.SUBNET_TOKEN_PRICE.value,
    }:
        price = await fetch_price(asset)
        if price is None:
            return None
        return resolve_outcome(float(reference_value), price)

    if event_type == ForecastEventType.NEW_SUBNET_REGISTRATION.value:
        count = await fetch_subnet_count(subtensor=subtensor)
        if count is None:
            return None
        return count > int(reference_value)

    bt.logging.info(f"oracle: no automatic resolver for event_type={event_type}")
    return None
