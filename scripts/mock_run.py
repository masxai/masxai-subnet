"""
scripts/mock_run.py — local mock run of the MASXAI v1 loop (no testnet, no chain).

Watch the full deferred-resolution loop close in ~30 seconds:

    ISSUE   → query mock miners, snapshot price, store in PENDING
    RESOLVE → (a few seconds later) fetch new price, score with Brier, EMA
    WEIGHT  → normalize scores into on-chain-style weights

This runs the REAL validator logic from `neurons/validator.py` — `resolve_due()`,
`issue_round()`, `build_question()`, `get_miner_uids()`, Brier scoring, EMA, and
state persistence are all the actual shipped code. Only the network primitives a
validator can't have off-chain are stubbed:

  1. metagraph + wallet  → lightweight fakes (N serving miner axons + 1 validator)
  2. dendrite            → a MASXAI-aware mock that fills `synapse.probability`
                           per miner, each running a distinct strategy
  3. oracle.fetch_price  → a simulated random walk so resolution happens in
                           seconds and outcomes vary
  4. forecast horizon    → compressed from 3600s to a few seconds

We stub the network here (rather than the SDK's chain mock) on purpose: the
bittensor mock-subtensor has drifted across SDK versions, and stubbing the
primitives keeps the demo deterministic while exercising 100% of the subnet's
own logic.

Run:
    python scripts/mock_run.py
    python scripts/mock_run.py --miners 12 --steps 24 --tick 1.0 --horizon 3
"""

import argparse
import asyncio
import os
import random
import sys
from typing import List, Optional

# Make the repo root importable when run as `python scripts/mock_run.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from masxai import constants as C
from masxai import oracle
from masxai.protocol import ForecastSynapse
from neurons.miner import predict as baseline_predict


# --------------------------------------------------------------------------- #
# 1. Simulated price feed: random walk with a configurable upward drift.        #
#    Replaces masxai.oracle.fetch_price so the loop resolves in seconds.         #
# --------------------------------------------------------------------------- #
class MockPriceFeed:
    def __init__(self, start: float = 233.0, drift: float = 0.8, sigma: float = 0.8, seed: int = 7):
        self.price = start
        self.drift = drift
        self.sigma = sigma
        self._rng = random.Random(seed)
        self.reads = 0

    async def fetch_price(self, asset: str = C.FORECAST_ASSET) -> Optional[float]:
        # Each read nudges the market. Positive drift => "up" outcomes are more
        # common, so bullish miners should climb the leaderboard over time.
        self.price = max(0.01, self.price + self._rng.gauss(self.drift, self.sigma))
        self.reads += 1
        return round(self.price, 4)


# --------------------------------------------------------------------------- #
# 2. Miner strategies. Each mock uid runs a fixed strategy so the leaderboard   #
#    spreads out. The baseline group uses the REAL neurons/miner.predict().     #
# --------------------------------------------------------------------------- #
def strategy_for(uid: int, synapse: ForecastSynapse, rng: random.Random) -> Optional[float]:
    bucket = uid % 4
    if bucket == 1:
        return 0.72                       # bullish — bets price goes up
    if bucket == 2:
        return 0.28                       # bearish — bets price goes down
    if bucket == 3:
        return baseline_predict(synapse)  # real baseline miner (neutral 0.5)
    # bucket == 0: flaky miner — answers ~60% of the time, else no answer (None)
    return 0.65 if rng.random() < 0.6 else None


def strategy_label(uid: int) -> str:
    return {1: "bullish(0.72)", 2: "bearish(0.28)", 3: "baseline(0.50)", 0: "flaky/no-answer"}[uid % 4]


# --------------------------------------------------------------------------- #
# 3. MASXAI-aware mock dendrite. Drives our ForecastSynapse (the SDK's mock     #
#    dendrite only knows the template's dummy protocol).                         #
# --------------------------------------------------------------------------- #
class MockForecastDendrite:
    def __init__(self, metagraph):
        self.metagraph = metagraph
        self._rng = random.Random(42)

    async def __call__(
        self,
        axons,
        synapse: ForecastSynapse,
        deserialize: bool = True,
        timeout: float = 12,
        **_,
    ) -> List[ForecastSynapse]:
        responses = []
        for axon in axons:
            uid = self.metagraph.hotkeys.index(axon.hotkey)
            resp = synapse.model_copy(deep=True)
            resp.probability = strategy_for(uid, resp, self._rng)
            responses.append(resp)
        await asyncio.sleep(0)            # behave like async I/O
        return responses


# --------------------------------------------------------------------------- #
# 4. Stubbed network primitives (metagraph / wallet) — just enough surface for  #
#    the real Validator methods. uid 0 = validator, uids 1..N = serving miners. #
# --------------------------------------------------------------------------- #
class _FakeAxon:
    def __init__(self, hotkey: str, serving: bool = True):
        self.hotkey = hotkey
        self.is_serving = serving
        self.ip = "127.0.0.1"
        self.port = 8091


class _FakeMetagraph:
    def __init__(self, hotkeys: List[str]):
        self.hotkeys = hotkeys
        self.axons = [_FakeAxon(hk) for hk in hotkeys]
        self.n = np.int64(len(hotkeys))   # validator code calls metagraph.n.item()


class _FakeKeypair:
    def __init__(self, ss58: str):
        self.ss58_address = ss58


class _FakeWallet:
    def __init__(self, ss58: str):
        self.hotkey = _FakeKeypair(ss58)


def build_validator(n_miners: int):
    """Construct the real Validator with chain setup bypassed."""
    from neurons.validator import Validator

    v = Validator.__new__(Validator)      # skip BaseValidatorNeuron.__init__ (chain setup)
    hotkeys = ["validator-hotkey"] + [f"miner-hotkey-{i}" for i in range(1, n_miners + 1)]
    v.metagraph = _FakeMetagraph(hotkeys)
    v.wallet = _FakeWallet("validator-hotkey")
    v.scores = np.zeros(len(hotkeys), dtype=np.float32)
    v.pending = {}
    v.resolved_count = 0
    v.dendrite = MockForecastDendrite(v.metagraph)
    v.load_masxai_state()                 # real persistence load (no-op on clean start)
    return v


# --------------------------------------------------------------------------- #
# Pretty printing                                                              #
# --------------------------------------------------------------------------- #
def print_leaderboard(validator, title: str):
    scores = validator.scores
    uids = validator.get_miner_uids()
    norm = np.linalg.norm(scores, ord=1)
    weights = scores / norm if norm > 0 else np.zeros_like(scores)

    rows = sorted(uids, key=lambda u: scores[u], reverse=True)
    print(f"\n  {title}")
    print(f"  {'uid':>3}  {'strategy':<16}  {'score(EMA)':>10}  {'weight':>8}")
    print(f"  {'-'*3}  {'-'*16}  {'-'*10}  {'-'*8}")
    for u in rows:
        print(f"  {u:>3}  {strategy_label(u):<16}  {scores[u]:>10.4f}  {weights[u]:>8.4f}")


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
async def run(args):
    # patch the slow/real-world bits before constructing the validator
    feed = MockPriceFeed(drift=args.drift)
    oracle.fetch_price = feed.fetch_price            # validator calls oracle.fetch_price(...)
    C.FORECAST_HORIZON_SECONDS = args.horizon        # compress 3600s -> a few seconds
    C.STATE_FILE = os.path.join(os.path.dirname(__file__), "mock_validator_state.json")
    if os.path.exists(C.STATE_FILE):
        os.remove(C.STATE_FILE)                      # clean, reproducible demo

    print("=" * 72)
    print("  MASXAI v1 — LOCAL MOCK RUN (no testnet, no chain)")
    print(f"  miners={args.miners}  horizon={args.horizon}s  tick={args.tick}s  "
          f"steps={args.steps}  drift=+{args.drift}/read")
    print("=" * 72)

    validator = build_validator(args.miners)
    n_miners = len(validator.get_miner_uids())
    print(f"\n  Mock network: {n_miners} serving miners (uid 0 = validator).")
    print(f"  Strategies assigned by uid % 4: bullish / bearish / baseline / flaky.\n")

    for step in range(1, args.steps + 1):
        await validator.resolve_due()                # REAL resolve path (Brier + EMA)
        await validator.issue_round()                # REAL issue path (snapshot + query)
        validator.save_masxai_state()                # REAL persistence
        print(
            f"  step {step:>2}/{args.steps} | "
            f"price≈${feed.price:>8.4f} | "
            f"pending={len(validator.pending):>2} | "
            f"resolved={validator.resolved_count:>3}"
        )
        await asyncio.sleep(args.tick)

    # drain: let the horizon pass and resolve everything still pending
    await asyncio.sleep(args.horizon)
    await validator.resolve_due()
    validator.save_masxai_state()

    print_leaderboard(validator, f"FINAL LEADERBOARD after {validator.resolved_count} resolved forecasts")

    norm = np.linalg.norm(validator.scores, ord=1)
    if norm > 0:
        print(f"\n  Weights sum to {(validator.scores / norm).sum():.4f} (L1-normalized scores).")
    print(f"  State persisted to: {C.STATE_FILE}")
    print("\n  Done — the loop closed end-to-end using the real validator code.\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Local mock run of the MASXAI v1 loop.")
    p.add_argument("--miners", type=int, default=12, help="number of mock miners")
    p.add_argument("--steps", type=int, default=18, help="number of issue/resolve ticks")
    p.add_argument("--tick", type=float, default=1.5, help="seconds between ticks")
    p.add_argument("--horizon", type=int, default=3, help="compressed forecast horizon (seconds)")
    p.add_argument("--drift", type=float, default=0.8, help="upward price drift per oracle read")
    asyncio.run(run(p.parse_args()))
