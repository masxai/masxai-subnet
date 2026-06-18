"""
masxai/scoring.py — v1 scoring.

v1 uses raw Brier score per resolved forecast, converted to a reward in [0, 1],
then EMA-smoothed into the per-miner score. Brier decomposition (reliability −
resolution + uncertainty) is intentionally deferred to v2 — it needs many resolved
forecasts per probability bin to be meaningful, which v1 doesn't have yet.

Brier score:  BS = (probability − outcome)^2,  range [0, 1], lower is better.
Reward:       r  = 1 − BS,                      range [0, 1], higher is better.

Why this is the right reward:
  - A miner predicting the true probability minimizes expected Brier (proper scoring rule).
  - Predicting 0.5 on a coin-flip event yields BS = 0.25 → reward 0.75 (the honest floor).
  - Confident-and-right (p=0.99, outcome=1) → BS≈0 → reward≈1.
  - Confident-and-wrong (p=0.99, outcome=0) → BS≈0.98 → reward≈0.02 (severe, correct).
"""

from masxai import constants as C


def clamp_prob(p: float) -> float:
    """Clamp a probability into [PROB_CLAMP_LO, PROB_CLAMP_HI]."""
    return max(C.PROB_CLAMP_LO, min(C.PROB_CLAMP_HI, p))


def brier_score(probability: float, outcome: bool) -> float:
    """(probability − outcome)^2, with probability clamped first."""
    p = clamp_prob(probability)
    return (p - float(outcome)) ** 2


def reward_from_brier(brier: float) -> float:
    """Convert a Brier score [0,1] into a reward [0,1] (higher = better)."""
    return max(0.0, 1.0 - brier)


def score_response(probability, outcome: bool) -> float:
    """
    Full reward for one resolved forecast.
    `probability` may be None if the miner didn't answer → assigned NO_ANSWER_BRIER.
    """
    if probability is None:
        return reward_from_brier(C.NO_ANSWER_BRIER)
    return reward_from_brier(brier_score(probability, outcome))


def ema_update(prev_score: float, new_reward: float, alpha: float = C.EMA_ALPHA) -> float:
    """Exponential moving average update of a miner's running score."""
    return (1.0 - alpha) * prev_score + alpha * new_reward
