"""
neurons/validator.py — MASXAI v1 validator.

This is the heart of v1. It implements the deferred-resolution queue that makes a
forecasting subnet actually work: a forecast issued now is scored later, when the
horizon passes and the oracle can tell us what actually happened.

Each forward() call (one per epoch-ish step) does three things:
  1. RESOLVE pending forecasts whose resolve_at has passed -> score -> EMA into scores
  2. ISSUE a fresh batch of forecasts to all miners -> store in PENDING
  3. weights are set by the template's machinery from self.scores

Persistence: PENDING + RESOLVED counts + scores are saved to STATE_FILE so a
restart never loses in-flight forecasts.

Built on the opentensor/bittensor-subnet-template BaseValidatorNeuron (SDK v10).
"""

import asyncio
import json
import os
import time
import uuid

import bittensor as bt
import numpy as np

from masxai.protocol import ForecastSynapse, ForecastCategory
from masxai import constants as C
from masxai import oracle
from masxai.scoring import score_response, ema_update

from template.base.validator import BaseValidatorNeuron


class Validator(BaseValidatorNeuron):
    def __init__(self, config=None):
        super().__init__(config=config)
        # pending[forecast_id] = {uid, probability, reference_price, asset, resolve_at}
        self.pending: dict[str, dict] = {}
        self.resolved_count = 0
        self.load_masxai_state()
        bt.logging.info("MASXAI v1 validator initialized.")

    # ---------------------------------------------------------------- state
    def load_masxai_state(self):
        if not os.path.exists(C.STATE_FILE):
            return
        try:
            with open(C.STATE_FILE, "r") as f:
                s = json.load(f)
            self.pending = s.get("pending", {})
            self.resolved_count = s.get("resolved_count", 0)
            scores = s.get("scores")
            if scores is not None:
                arr = np.array(scores, dtype=np.float32)
                if arr.shape == self.scores.shape:
                    self.scores = arr
            bt.logging.info(
                f"loaded state: {len(self.pending)} pending, "
                f"{self.resolved_count} resolved"
            )
        except Exception as e:  # noqa: BLE001
            bt.logging.warning(f"could not load state, starting fresh: {e}")

    def save_masxai_state(self):
        try:
            with open(C.STATE_FILE, "w") as f:
                json.dump(
                    {
                        "pending": self.pending,
                        "resolved_count": self.resolved_count,
                        "scores": self.scores.tolist(),
                    },
                    f,
                )
        except Exception as e:  # noqa: BLE001
            bt.logging.warning(f"could not save state: {e}")

    # ------------------------------------------------------------- resolve
    async def resolve_due(self):
        """Resolve every pending forecast whose horizon has passed."""
        now = time.time()
        due = [fid for fid, f in self.pending.items() if f["resolve_at"] <= now]
        if not due:
            return

        # one price read serves all forecasts on the same asset this pass
        price = await oracle.fetch_price(C.FORECAST_ASSET)
        if price is None:
            bt.logging.info("oracle unavailable; deferring resolution to next epoch")
            return

        for fid in due:
            f = self.pending.pop(fid)
            outcome = oracle.resolve_outcome(f["reference_price"], price)
            reward = score_response(f["probability"], outcome)
            uid = f["uid"]
            self.scores[uid] = ema_update(float(self.scores[uid]), reward)
            self.resolved_count += 1
            bt.logging.debug(
                f"resolved uid={uid} p={f['probability']} outcome={outcome} "
                f"reward={reward:.3f} -> score={self.scores[uid]:.3f}"
            )
        bt.logging.info(
            f"resolved {len(due)} forecasts at price={price} | "
            f"total resolved={self.resolved_count}"
        )

    # --------------------------------------------------------------- issue
    def build_question(self, reference_price: float) -> ForecastSynapse:
        now = time.time()
        resolve_at = now + C.FORECAST_HORIZON_SECONDS
        mins = C.FORECAST_HORIZON_SECONDS // 60
        return ForecastSynapse(
            question=(
                f"Will TAO/USD be higher than ${reference_price:.4f} "
                f"in {mins} minutes?"
            ),
            category=ForecastCategory.TAO_PRICE.value,
            asset=C.FORECAST_ASSET,
            reference_price=reference_price,
            issued_at=now,
            resolve_at=resolve_at,
        )

    async def issue_round(self):
        """Snapshot price, query all miners, store responses in PENDING."""
        reference_price = await oracle.fetch_price(C.FORECAST_ASSET)
        if reference_price is None:
            bt.logging.info("oracle unavailable; skipping issue this epoch")
            return

        miner_uids = self.get_miner_uids()
        if len(miner_uids) == 0:
            bt.logging.info("no miners to query this epoch")
            return

        synapse = self.build_question(reference_price)
        axons = [self.metagraph.axons[uid] for uid in miner_uids]

        responses = await self.dendrite(
            axons=axons,
            synapse=synapse,
            deserialize=False,
            timeout=C.QUERY_TIMEOUT,
        )

        issued = 0
        for uid, resp in zip(miner_uids, responses):
            fid = uuid.uuid4().hex
            self.pending[fid] = {
                "uid": int(uid),
                "probability": resp.probability,  # may be None if no answer
                "reference_price": reference_price,
                "asset": C.FORECAST_ASSET,
                "resolve_at": synapse.resolve_at,
            }
            issued += 1
        bt.logging.info(
            f"issued {issued} forecasts @ ref={reference_price} "
            f"(resolve in {C.FORECAST_HORIZON_SECONDS//60}m) | "
            f"pending now={len(self.pending)}"
        )

    def get_miner_uids(self) -> list[int]:
        """All registered neurons that are serving an axon (i.e., miners)."""
        uids = []
        for uid in range(self.metagraph.n.item()):
            if self.metagraph.axons[uid].is_serving:
                # skip our own hotkey
                if self.metagraph.hotkeys[uid] != self.wallet.hotkey.ss58_address:
                    uids.append(uid)
        return uids

    # ------------------------------------------------------------- forward
    async def forward(self):
        """One validator step: resolve due → issue new → persist."""
        await self.resolve_due()
        await self.issue_round()
        self.save_masxai_state()
        # brief pause so we don't hot-loop; the base class also paces by epoch
        await asyncio.sleep(5)


if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info(
                f"MASXAI validator alive | pending={len(validator.pending)} "
                f"resolved={validator.resolved_count} | "
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            time.sleep(30)
