from __future__ import annotations

"""Normalize reliability profile scores and optionally submit chain weights."""

import logging
from typing import Sequence

from config import Settings
from models import MinerRegistration, ReliabilityProfile, utcnow


logger = logging.getLogger(__name__)


def normalize_weights(session_factory) -> list[tuple[int, float, float]]:
    with session_factory() as session:
        rows = (
            session.query(MinerRegistration, ReliabilityProfile)
            .join(ReliabilityProfile, ReliabilityProfile.miner_uid == MinerRegistration.uid)
            .filter(MinerRegistration.is_active.is_(True))
            .all()
        )
        total = sum(max(0.0, float(profile.base_score)) for _miner, profile in rows)
        results: list[tuple[int, float, float]] = []
        for miner, profile in rows:
            raw = max(0.0, float(profile.base_score))
            normalized = raw / total if total > 0 else 0.0
            profile.raw_weight = raw
            profile.normalized_weight = normalized
            profile.updated_at = utcnow()
            results.append((miner.uid, raw, normalized))
        session.commit()
        return sorted(results, key=lambda item: item[0])


def set_weights(settings: Settings, session_factory) -> list[tuple[int, float, float]]:
    weights = normalize_weights(session_factory)
    if settings.dry_run_weights:
        logger.info("DRY_RUN_WEIGHTS=true; normalized weights=%s", weights)
        return weights
    _submit_weights(settings, [uid for uid, _raw, _norm in weights], [norm for _uid, _raw, norm in weights])
    return weights


def _submit_weights(settings: Settings, uids: Sequence[int], weights: Sequence[float]) -> None:
    try:
        import bittensor as bt
    except Exception as exc:
        raise RuntimeError("bittensor is required when DRY_RUN_WEIGHTS=false") from exc
    wallet = bt.wallet(name=settings.wallet_name, hotkey=settings.wallet_hotkey)
    subtensor = bt.subtensor(network=settings.subtensor_network)
    subtensor.set_weights(wallet, settings.netuid, list(uids), list(weights))
