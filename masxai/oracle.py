"""
masxai/oracle.py — free, objective price oracle for v1 resolution.

v1 resolves "will TAO be higher in 60 minutes?" by reading the spot price from
CoinGecko's free public endpoint. No API key required. The oracle is the only
source of ground truth in v1 — there is no LLM and no human in the resolve path.

Design rule: the oracle must fail SOFT. If the price feed is unreachable, return
None and let the caller skip resolution this epoch and retry next epoch. A forecast
is never resolved on missing data.
"""

from typing import Optional

import bittensor as bt
import httpx

from masxai import constants as C


async def fetch_price(asset: str = C.FORECAST_ASSET) -> Optional[float]:
    """
    Return current USD price for `asset`, or None on any failure.
    Never raises — resolution must continue across feed outages.
    """
    params = {"ids": asset, "vs_currencies": "usd"}
    try:
        async with httpx.AsyncClient(timeout=C.ORACLE_TIMEOUT) as client:
            resp = await client.get(C.COINGECKO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            price = data.get(asset, {}).get("usd")
            if price is None:
                bt.logging.warning(f"oracle: no price for asset '{asset}' in response")
                return None
            return float(price)
    except Exception as e:  # noqa: BLE001 — soft-fail by design
        bt.logging.warning(f"oracle: price fetch failed: {e}")
        return None


def resolve_outcome(reference_price: float, resolved_price: float) -> bool:
    """
    Binary outcome for the question "will the price be HIGHER at resolve time?"
    True  = price went up (or flat-up)  -> YES
    False = price went down              -> NO
    """
    return resolved_price >= reference_price
