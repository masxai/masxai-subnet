"""
neurons/miner.py — MASXAI v1 baseline miner.

Runs with ZERO API keys. Anyone can launch it. It answers price-direction
forecasting questions with a calibrated baseline probability.

The baseline strategy: short-horizon price direction is close to a coin flip,
so an honest miner returns ~0.5 with a tiny momentum nudge. This is deliberately
weak — it exists so the loop runs and so real miners have something to beat.

To build a SERIOUS miner, replace `predict()` with your own model (momentum,
order-book signals, an LLM, or the full MASXAI engine). The contract is simple:
given the synapse, return a probability in [0.01, 0.99] that the price will be
HIGHER at resolve_at than the reference_price.

Built on the opentensor/bittensor-subnet-template BaseMinerNeuron (SDK v10).
Place this repo as a fork of that template so `template.base.miner` is importable.
"""

import time
import typing

import bittensor as bt

from masxai.protocol import ForecastSynapse
from masxai import constants as C
from masxai.scoring import clamp_prob

# Provided by the bittensor-subnet-template fork:
from template.base.miner import BaseMinerNeuron


def predict(synapse: ForecastSynapse) -> float:
    """
    Baseline prediction. Returns P(price higher at resolve time).

    v1 baseline = neutral 0.5. Honest, calibrated, and easy to beat — exactly
    what a reference miner should be. Override this with a real model.
    """
    return C.NEUTRAL_PROB


class Miner(BaseMinerNeuron):
    def __init__(self, config=None):
        super().__init__(config=config)
        bt.logging.info("MASXAI v1 baseline miner initialized.")

    async def forward(self, synapse: ForecastSynapse) -> ForecastSynapse:
        """Answer a forecasting question with a probability."""
        try:
            p = clamp_prob(predict(synapse))
        except Exception as e:  # noqa: BLE001 — never let forward crash
            bt.logging.warning(f"miner predict failed, returning neutral: {e}")
            p = C.NEUTRAL_PROB

        synapse.probability = p
        bt.logging.debug(
            f"answered: cat={synapse.category} ref={synapse.reference_price} -> p={p:.3f}"
        )
        return synapse

    async def blacklist(self, synapse: ForecastSynapse) -> typing.Tuple[bool, str]:
        """
        Reject requests from non-registered or (optionally) non-validator hotkeys.
        Keeps the axon from answering spam. Standard template pattern.
        """
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            return True, "missing dendrite/hotkey"

        hotkey = synapse.dendrite.hotkey
        if hotkey not in self.metagraph.hotkeys:
            return True, f"unregistered hotkey {hotkey}"

        uid = self.metagraph.hotkeys.index(hotkey)
        # In v1 we only require registration. To restrict to validators, also check:
        #   if not self.metagraph.validator_permit[uid]: return True, "no validator permit"
        return False, f"accepted from uid {uid}"

    async def priority(self, synapse: ForecastSynapse) -> float:
        """Prioritize higher-stake callers. Standard template pattern."""
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            return 0.0
        uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        return float(self.metagraph.S[uid])


if __name__ == "__main__":
    with Miner() as miner:
        while True:
            bt.logging.info(f"MASXAI miner alive | {time.strftime('%Y-%m-%d %H:%M:%S')}")
            time.sleep(30)
