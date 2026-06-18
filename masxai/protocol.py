"""
masxai/protocol.py — v1 wire protocol for the MASXAI subnet (netuid 501).

The validator fills the *request* fields and queries miners. Each miner fills
the *response* field (a probability) and returns the synapse unchanged otherwise.

Keep this schema small in v1. Any change here breaks already-registered miners,
so version it (bump SYNAPSE_VERSION) and coordinate a redeploy.
"""

from enum import Enum
from typing import Optional

import bittensor as bt
import pydantic

SYNAPSE_VERSION = 1


class ForecastCategory(str, Enum):
    # v1 only ever issues TAO_PRICE. The rest are reserved for later versions.
    TAO_PRICE = "tao_price"
    CRYPTO_MARKETS = "crypto_markets"
    GEOPOLITICS = "geopolitics"
    MACROECONOMICS = "macroeconomics"
    EVENT = "event_forecasting"


class ForecastSynapse(bt.Synapse):
    """
    A single binary forecasting task.

    Request (set by validator):
        question        human-readable binary question
        category        forecast category (v1: always tao_price)
        asset           oracle id of the asset (v1: 'bittensor' for TAO)
        reference_price snapshot price at issue time (for transparency/debug)
        issued_at       unix seconds when issued
        resolve_at      unix seconds when the question resolves
        version         protocol version

    Response (set by miner):
        probability     P(outcome is YES), i.e. P(price_at_resolve > reference_price)
                        Must be in [0.01, 0.99]. None means the miner did not answer.
    """

    # ---- request ----
    question: str = ""
    category: str = ForecastCategory.TAO_PRICE.value
    asset: str = "bittensor"
    reference_price: float = 0.0
    issued_at: float = 0.0
    resolve_at: float = 0.0
    version: int = SYNAPSE_VERSION

    # ---- response ----
    probability: Optional[float] = pydantic.Field(default=None)

    def deserialize(self) -> Optional[float]:
        """Validators call this to read the miner's answer."""
        return self.probability
