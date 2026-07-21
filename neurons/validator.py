"""
neurons/validator.py - MASXAI MVP validator.

The validator issues structured forecasting tasks, stores miner forecasts, waits
for the forecast window to complete, resolves ground truth through objective
oracles, scores miners, and lets the template weight machinery use self.scores.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from masxai.protocol import ForecastSynapse, ForecastEventType
from masxai import constants as C
from masxai import oracle
from masxai.bt_compat import bt
from masxai.env import load_env
from masxai.oracle_bt import (
    BtForecastQuestion,
    BtForecastResolution,
    bt_forecast_required_from_env,
    bt_forecast_run_id_from_env,
    open_bt_forecast_client_from_env,
    parse_api_timestamp,
)
from masxai.scoring import brier_score, ema_update, score_structured_forecast

try:
    from template.base.validator import BaseValidatorNeuron
except Exception:
    class BaseValidatorNeuron:  # type: ignore[no-redef]
        def __init__(self, *_, **__):
            raise RuntimeError("BaseValidatorNeuron requires a working bittensor install")


def _parse_timestamp(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return None


def _env_float(name: str, default: float) -> float:
    load_env()
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    load_env()
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    load_env()
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Validator(BaseValidatorNeuron):
    def __init__(self, config=None):
        load_env()
        super().__init__(config=config)
        # pending[key] = structured forecast response plus resolver state.
        # Central BT-Forecast keys are deterministic by run/question/miner.
        self.pending: dict[str, dict] = {}
        self.issued_questions: dict[str, float] = {}
        self.feedback_queue: list[dict[str, Any]] = []
        self.bt_forecast_client = open_bt_forecast_client_from_env()
        self.bt_forecast_required = bt_forecast_required_from_env()
        self.resolved_count = 0
        self.last_issue_at = 0.0
        self.load_masxai_state()
        bt.logging.info(
            "MASXAI validator initialized | "
            f"bt_forecast_enabled={self.bt_forecast_client is not None} "
            f"bt_forecast_required={self.bt_forecast_required}"
        )

    # ---------------------------------------------------------------- state
    def load_masxai_state(self):
        if not hasattr(self, "issued_questions"):
            self.issued_questions = {}
        if not hasattr(self, "feedback_queue"):
            self.feedback_queue = []
        if not os.path.exists(C.STATE_FILE):
            return
        try:
            with open(C.STATE_FILE, "r") as f:
                s = json.load(f)
            self.pending = s.get("pending", {})
            self.issued_questions = {
                str(k): float(v) for k, v in s.get("issued_questions", {}).items()
            }
            self.feedback_queue = list(s.get("feedback_queue", []))
            self.resolved_count = s.get("resolved_count", 0)
            self.last_issue_at = float(s.get("last_issue_at", 0.0))
            scores = s.get("scores")
            if scores is not None:
                arr = np.array(scores, dtype=np.float32)
                if arr.shape == self.scores.shape:
                    self.scores = arr
            bt.logging.info(
                f"loaded state: {len(self.pending)} pending, "
                f"{self.resolved_count} resolved, "
                f"{len(self.feedback_queue)} feedback payload(s) queued"
            )
        except Exception as e:  # noqa: BLE001
            bt.logging.warning(f"could not load state, starting fresh: {e}")

    def save_masxai_state(self):
        try:
            state_path = os.path.abspath(C.STATE_FILE)
            tmp_path = f"{state_path}.tmp"
            with open(tmp_path, "w") as f:
                json.dump(
                    {
                        "pending": self.pending,
                        "issued_questions": self.issued_questions,
                        "feedback_queue": self.feedback_queue,
                        "resolved_count": self.resolved_count,
                        "last_issue_at": self.last_issue_at,
                        "scores": self.scores.tolist(),
                    },
                    f,
                )
            os.replace(tmp_path, state_path)
        except Exception as e:  # noqa: BLE001
            bt.logging.warning(f"could not save state: {e}")

    # ------------------------------------------------------------- resolve
    async def resolve_due(self):
        """Resolve every pending forecast whose horizon has passed."""
        now = time.time()
        due = [fid for fid, f in self.pending.items() if f["resolve_at"] <= now]
        if not due:
            await self.flush_bt_feedback()
            return

        bt_due = [fid for fid in due if self.pending[fid].get("source") == "bt_forecast"]
        bt_due_set = set(bt_due)
        local_due = [fid for fid in due if fid not in bt_due_set]

        if bt_due:
            await self.resolve_bt_forecast_due(bt_due, now=now)
        if local_due:
            await self.resolve_local_due(local_due)
        await self.flush_bt_feedback()

    async def resolve_local_due(self, due: list[str]):
        """Resolve legacy local-oracle forecasts."""
        for fid in due:
            if fid not in self.pending:
                continue
            f = self.pending[fid]
            outcome = await oracle.resolve_forecast_outcome(
                f,
                subtensor=getattr(self, "subtensor", None),
            )
            if outcome is None:
                bt.logging.info(f"oracle unavailable for {fid}; deferring resolution")
                continue

            f = self.pending.pop(fid)
            uid = f["uid"]
            prev_score = float(self.scores[uid])
            reward = score_structured_forecast(
                prediction=f.get("prediction"),
                confidence=f.get("confidence"),
                outcome=outcome,
                probability=f.get("probability"),
                previous_score=prev_score,
                submitted_at=f.get("submitted_at", f.get("issued_at", 0.0)),
                issued_at=f.get("issued_at", 0.0),
                resolve_at=f.get("resolve_at", 1.0),
            )
            self.scores[uid] = ema_update(float(self.scores[uid]), reward)
            self.resolved_count += 1
            bt.logging.debug(
                f"resolved uid={uid} event={f.get('event_type')} "
                f"prediction={f.get('prediction')} confidence={f.get('confidence')} "
                f"outcome={outcome} "
                f"reward={reward:.3f} -> score={self.scores[uid]:.3f}"
            )
        bt.logging.info(
            f"resolved due forecasts | "
            f"total resolved={self.resolved_count}"
        )

    async def resolve_bt_forecast_due(self, due: list[str], *, now: float):
        """Resolve centralized BT-Forecast questions through the FastAPI API."""
        client = self._bt_forecast_client()
        if client is None:
            bt.logging.warning("BT-Forecast API unavailable; deferring centralized resolutions")
            return

        by_run: dict[str, list[str]] = defaultdict(list)
        for fid in due:
            f = self.pending.get(fid)
            if not f:
                continue
            by_run[str(f.get("run_id") or bt_forecast_run_id_from_env())].append(fid)

        resolution_wait = _env_float(
            C.BT_FORECAST_RESOLUTION_WAIT_SECONDS_ENV,
            C.BT_FORECAST_RESOLUTION_WAIT_SECONDS,
        )
        resolved_questions = 0
        dropped_questions = 0

        for run_id, fids in by_run.items():
            try:
                resolutions = await client.get_resolutions(run_id=run_id)
            except Exception as e:  # noqa: BLE001
                bt.logging.warning(f"BT-Forecast resolutions fetch failed run_id={run_id}: {e}")
                continue

            resolution_by_key = {r.question_key: r for r in resolutions}
            by_question: dict[str, list[str]] = defaultdict(list)
            for fid in fids:
                f = self.pending.get(fid)
                if f:
                    by_question[str(f.get("question_key") or fid)].append(fid)

            for question_key, question_fids in by_question.items():
                sample = self.pending.get(question_fids[0])
                if not sample:
                    continue
                resolution = resolution_by_key.get(question_key)
                if resolution is None or resolution.status in C.BT_FORECAST_OPEN_STATUSES:
                    if now - float(sample.get("resolve_at", now)) > resolution_wait:
                        self._drop_pending(question_fids)
                        dropped_questions += 1
                        bt.logging.info(
                            f"dropped unscored BT-Forecast question after wait window: {question_key}"
                        )
                    continue

                if resolution.status in C.BT_FORECAST_UNSCORED_TERMINAL_STATUSES:
                    self._drop_pending(question_fids)
                    dropped_questions += 1
                    bt.logging.info(
                        f"dropped unscored BT-Forecast question status={resolution.status}: "
                        f"{question_key}"
                    )
                    continue

                outcome = resolution.bool_outcome()
                if outcome is None:
                    continue

                forecasts = [self.pending[fid] for fid in question_fids if fid in self.pending]
                feedback_payload = self._build_miner_results_payload(
                    run_id=run_id,
                    question_key=question_key,
                    forecasts=forecasts,
                    resolution=resolution,
                    outcome=outcome,
                )
                for fid in question_fids:
                    if fid not in self.pending:
                        continue
                    f = self.pending.pop(fid)
                    uid = int(f["uid"])
                    prev_score = float(self.scores[uid])
                    reward = self._score_resolved_forecast(f, outcome)
                    self.scores[uid] = ema_update(float(self.scores[uid]), reward)
                    self.resolved_count += 1
                    bt.logging.debug(
                        f"resolved bt uid={uid} family={f.get('family')} "
                        f"probability={f.get('probability')} outcome={outcome} "
                        f"reward={reward:.3f} prev={prev_score:.3f} "
                        f"score={self.scores[uid]:.3f}"
                    )

                if feedback_payload["results"]:
                    self.feedback_queue.append(feedback_payload)
                resolved_questions += 1

        bt.logging.info(
            f"BT-Forecast resolution pass | questions_resolved={resolved_questions} "
            f"questions_dropped={dropped_questions} total_resolved={self.resolved_count}"
        )

    def _score_resolved_forecast(self, forecast: dict, outcome: bool) -> float:
        uid = int(forecast["uid"])
        return score_structured_forecast(
            prediction=forecast.get("prediction"),
            confidence=forecast.get("confidence"),
            probability=forecast.get("probability"),
            outcome=outcome,
            previous_score=float(self.scores[uid]),
            submitted_at=forecast.get("submitted_at", forecast.get("issued_at", 0.0)),
            issued_at=forecast.get("issued_at", 0.0),
            resolve_at=forecast.get("resolve_at", 1.0),
        )

    def _drop_pending(self, fids: list[str]) -> None:
        for fid in fids:
            self.pending.pop(fid, None)

    async def flush_bt_feedback(self):
        """Best-effort retrying sender for validator -> BT-Forecast calibration feedback."""
        if not getattr(self, "feedback_queue", None):
            return
        client = self._bt_forecast_client()
        if client is None:
            return
        remaining = []
        for payload in self.feedback_queue:
            try:
                await client.post_miner_results(payload)
                bt.logging.info(
                    f"posted BT-Forecast miner feedback question_key={payload.get('question_key')} "
                    f"results={len(payload.get('results', []))}"
                )
            except Exception as e:  # noqa: BLE001
                bt.logging.warning(
                    f"BT-Forecast miner feedback post failed "
                    f"question_key={payload.get('question_key')}: {e}"
                )
                remaining.append(payload)
        self.feedback_queue = remaining

    # --------------------------------------------------------------- issue
    def build_question(self, event_type: str, reference: dict) -> ForecastSynapse:
        now = time.time()
        resolve_at = now + C.FORECAST_HORIZON_SECONDS
        mins = C.FORECAST_HORIZON_SECONDS // 60
        reference_value = reference.get("reference_value")
        metadata = reference.get("reference_metadata", {})

        if event_type == ForecastEventType.TAO_PRICE_MOVEMENT.value:
            question = (
                f"Will TAO/USD be higher than ${float(reference_value):.4f} "
                f"in {mins} minutes?"
            )
            context = (
                f"Event type: {event_type}\n"
                f"Current TAO/USD reference price: {reference_value}\n"
                f"Forecast window: {C.FORECAST_WINDOW}\n"
                "Use recent Bittensor market, subnet, governance, and ecosystem "
                "signals available to your miner before answering."
            )
        elif event_type == ForecastEventType.NEW_SUBNET_REGISTRATION.value:
            count = int(reference_value)
            question = f"Will at least one new Bittensor subnet register in the next {C.FORECAST_WINDOW}?"
            context = (
                f"Event type: {event_type}\n"
                f"Current subnet count: {count}\n"
                f"Forecast window: {C.FORECAST_WINDOW}"
            )
        else:
            question = f"Will the MASXAI event '{event_type}' occur within {C.FORECAST_WINDOW}?"
            context = f"Event type: {event_type}\nForecast window: {C.FORECAST_WINDOW}"

        return ForecastSynapse(
            forecast_id=uuid.uuid4().hex,
            question=question,
            event_type=event_type,
            asset=C.FORECAST_ASSET,
            reference_value=reference_value,
            reference_metadata=metadata,
            forecast_window=C.FORECAST_WINDOW,
            issued_at=now,
            resolve_at=resolve_at,
            context=context,
        )

    async def issue_round(self):
        """Fetch/issue forecast questions, query miners, and store responses."""
        now = time.time()
        if not self._issue_interval_reached(now):
            return

        client = self._bt_forecast_client()
        if client is not None:
            await self.issue_bt_forecast_round(client=client, now=now)
            self.last_issue_at = time.time()
            return

        if self._bt_forecast_required():
            bt.logging.warning(
                "MASXAI_BT_FORECAST_REQUIRED=true but BT_FORECAST_BASE_URL is unset; "
                "skipping legacy local issue"
            )
            self.last_issue_at = time.time()
            return

        await self.issue_local_round(now=now)
        self.last_issue_at = time.time()

    def _issue_interval_reached(self, now: float) -> bool:
        interval = max(
            0.0,
            _env_float("MASXAI_FORECAST_INTERVAL_SECONDS", C.FORECAST_INTERVAL_SECONDS),
        )
        if self.last_issue_at and now - self.last_issue_at < interval:
            remaining = int(interval - (now - self.last_issue_at))
            bt.logging.debug(f"forecast interval not reached; next issue in {remaining}s")
            return False
        return True

    async def issue_local_round(self, *, now: float):
        """Legacy local-oracle issue path for development and fallback."""
        event_type = C.ENABLED_EVENT_TYPES[self.resolved_count % len(C.ENABLED_EVENT_TYPES)]
        reference = await oracle.snapshot_reference(
            event_type,
            asset=C.FORECAST_ASSET,
            subtensor=getattr(self, "subtensor", None),
        )
        if reference is None:
            bt.logging.info("oracle unavailable; skipping issue this epoch")
            return

        miner_uids = self.get_miner_uids()
        if len(miner_uids) == 0:
            bt.logging.info("no miners to query this epoch")
            return

        synapse = self.build_question(event_type, reference)
        axons = [self.metagraph.axons[uid] for uid in miner_uids]

        responses = await self.dendrite(
            axons=axons,
            synapse=synapse,
            deserialize=False,
            timeout=C.QUERY_TIMEOUT,
        )

        issued = 0
        answered = 0
        submitted_at = time.time()
        for uid, resp in zip(miner_uids, responses):
            fid = uuid.uuid4().hex
            prediction = resp.prediction
            confidence = resp.confidence
            if prediction is None and resp.probability is not None:
                prediction = resp.probability >= C.NEUTRAL_PROB
                confidence = max(resp.probability, 1.0 - resp.probability)
            if prediction is not None and confidence is not None:
                answered += 1
            self.pending[fid] = {
                "uid": int(uid),
                "forecast_id": resp.forecast_id or fid,
                "event_type": event_type,
                "prediction": prediction,
                "confidence": confidence,
                "probability": resp.probability,  # may be None if no answer
                "reasoning": resp.reasoning,
                "model": resp.model,
                "timestamp": resp.timestamp,
                "submitted_at": _parse_timestamp(resp.timestamp) or submitted_at,
                "reference_value": reference.get("reference_value"),
                "reference_metadata": reference.get("reference_metadata", {}),
                "issued_at": synapse.issued_at,
                "asset": C.FORECAST_ASSET,
                "resolve_at": synapse.resolve_at,
            }
            issued += 1
        bt.logging.info(
            f"issued {issued} {event_type} forecasts @ ref={reference.get('reference_value')} "
            f"| answered={answered}/{len(miner_uids)} "
            f"(resolve in {C.FORECAST_HORIZON_SECONDS//60}m) | "
            f"pending now={len(self.pending)}"
        )

    async def issue_bt_forecast_round(self, *, client, now: float):
        """Fetch ready BT-Forecast questions and relay miner-safe tasks to miners."""
        run_id = bt_forecast_run_id_from_env()
        try:
            run = await client.get_run(run_id)
        except Exception as e:  # noqa: BLE001
            bt.logging.warning(f"BT-Forecast run poll failed run_id={run_id}: {e}")
            return

        if run.status not in C.BT_FORECAST_READY_STATUSES:
            bt.logging.info(f"BT-Forecast run {run_id} not ready yet: status={run.status}")
            return

        include_lineage = _env_flag(C.BT_FORECAST_INCLUDE_LINEAGE_ENV, False)
        try:
            questions = await client.get_questions(run_id, include_lineage=include_lineage)
        except Exception as e:  # noqa: BLE001
            bt.logging.warning(f"BT-Forecast question fetch failed run_id={run_id}: {e}")
            return

        questions = [q for q in questions if not q.predetermined_at_creation]
        questions = [q for q in questions if self._should_issue_bt_question(q, now=now)]
        max_questions = _env_int(
            C.BT_FORECAST_MAX_QUESTIONS_ENV,
            C.BT_FORECAST_MAX_QUESTIONS_PER_ROUND,
        )
        if max_questions > 0:
            questions = questions[:max_questions]
        if not questions:
            bt.logging.info(f"BT-Forecast run {run_id}: no new miner-safe questions to issue")
            return

        miner_uids = self.get_miner_uids()
        if len(miner_uids) == 0:
            bt.logging.info("no miners to query this epoch")
            return

        issued = 0
        answered = 0
        for question in questions:
            synapse = self.build_bt_forecast_synapse(question, run_id=run_id)
            axons = [self.metagraph.axons[uid] for uid in miner_uids]
            responses = await self.dendrite(
                axons=axons,
                synapse=synapse,
                deserialize=False,
                timeout=C.QUERY_TIMEOUT,
            )
            count, answered_count = self._store_bt_forecast_responses(
                run_id=run_id,
                question=question,
                synapse=synapse,
                miner_uids=miner_uids,
                responses=responses,
            )
            issued += count
            answered += answered_count
            self.issued_questions[question.question_key] = now

        bt.logging.info(
            f"issued {issued} BT-Forecast miner calls from run={run_id} "
            f"questions={len(questions)} answered={answered}/{issued} "
            f"pending now={len(self.pending)}"
        )

    def build_bt_forecast_synapse(self, question: BtForecastQuestion, *, run_id: str) -> ForecastSynapse:
        issued_at = time.time()
        resolve_at = parse_api_timestamp(question.cutoff_date) or issued_at
        context = "\n".join(
            part
            for part in (
                question.evidence_summary,
                question.resolution_criteria,
                f"Measurement: {json.dumps(question.measurement, sort_keys=True)}"
                if question.measurement
                else "",
                f"BT-Forecast run: {run_id}",
            )
            if part
        )
        return ForecastSynapse(
            forecast_id=uuid.uuid4().hex,
            question_id=question.question_id,
            question_key=question.question_key,
            question=question.question,
            event_type=ForecastEventType.SIGNIFICANT_BITTENSOR_EVENT.value,
            family=question.family,
            scope=question.scope,
            netuid=question.netuid,
            horizon_days=question.horizon_days,
            forecast_window=f"{question.horizon_days}d" if question.horizon_days else "",
            issued_at=issued_at,
            resolve_at=resolve_at,
            context=context,
        )

    def _should_issue_bt_question(self, question: BtForecastQuestion, *, now: float) -> bool:
        cutoff_ts = parse_api_timestamp(question.cutoff_date)
        if cutoff_ts is not None and cutoff_ts <= now:
            return False
        reissue_seconds = _env_float(
            C.BT_FORECAST_REISSUE_SECONDS_ENV,
            C.BT_FORECAST_REISSUE_SECONDS,
        )
        last_issued = self.issued_questions.get(question.question_key)
        if last_issued is None:
            return True
        if reissue_seconds <= 0:
            return False
        return now - last_issued >= reissue_seconds

    def _store_bt_forecast_responses(
        self,
        *,
        run_id: str,
        question: BtForecastQuestion,
        synapse: ForecastSynapse,
        miner_uids: list[int],
        responses,
    ) -> tuple[int, int]:
        submitted_at = time.time()
        issued = 0
        answered = 0
        for uid, resp in zip(miner_uids, responses):
            fid = self._bt_pending_key(run_id, question.question_key, int(uid))
            probability, prediction, confidence = self._normalize_miner_response(resp)
            if probability is not None:
                answered += 1
            self.pending[fid] = {
                "source": "bt_forecast",
                "pending_key": fid,
                "uid": int(uid),
                "hotkey": self.metagraph.hotkeys[int(uid)],
                "forecast_id": getattr(resp, "forecast_id", "") or synapse.forecast_id,
                "question_id": question.question_id,
                "question_key": question.question_key,
                "question": question.question,
                "event_type": ForecastEventType.SIGNIFICANT_BITTENSOR_EVENT.value,
                "family": question.family,
                "scope": question.scope,
                "netuid": question.netuid,
                "horizon_days": question.horizon_days,
                "prediction": prediction,
                "confidence": confidence,
                "probability": probability,
                "reasoning": getattr(resp, "reasoning", ""),
                "model": getattr(resp, "model", ""),
                "features": dict(getattr(resp, "features", {}) or {}),
                "timestamp": getattr(resp, "timestamp", ""),
                "submitted_at": _parse_timestamp(getattr(resp, "timestamp", "")) or submitted_at,
                "issued_at": synapse.issued_at,
                "resolve_at": synapse.resolve_at,
                "cutoff_date": question.cutoff_date,
                "run_id": run_id,
                "engine_probability": question.engine_probability,
                "measurement": question.measurement,
            }
            issued += 1
        return issued, answered

    @staticmethod
    def _bt_pending_key(run_id: str, question_key: str, uid: int) -> str:
        digest = hashlib.sha256(f"{run_id}\n{question_key}\n{int(uid)}".encode("utf-8")).hexdigest()
        return f"bt_forecast:{digest}"

    def _normalize_miner_response(self, resp) -> tuple[Optional[float], Optional[bool], Optional[float]]:
        probability = getattr(resp, "probability", None)
        prediction = getattr(resp, "prediction", None)
        confidence = getattr(resp, "confidence", None)
        try:
            probability = float(probability) if probability is not None else None
        except (TypeError, ValueError):
            probability = None

        if probability is not None:
            probability = max(0.0, min(1.0, probability))
            if prediction is None:
                prediction = probability >= C.NEUTRAL_PROB
            if confidence is None:
                confidence = max(probability, 1.0 - probability)
        elif prediction is not None and confidence is not None:
            try:
                c = float(confidence)
                probability = c if bool(prediction) else 1.0 - c
            except (TypeError, ValueError):
                probability = None

        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))
        return probability, prediction if prediction is None else bool(prediction), confidence

    def _build_miner_results_payload(
        self,
        *,
        run_id: str,
        question_key: str,
        forecasts: list[dict],
        resolution: BtForecastResolution,
        outcome: bool,
    ) -> dict[str, Any]:
        sample = forecasts[0] if forecasts else {}
        threshold = _env_float(
            C.BT_FORECAST_FEEDBACK_THRESHOLD_ENV,
            C.BT_FORECAST_FEEDBACK_THRESHOLD,
        )
        outcome_value = float(outcome)
        engine_probability = sample.get("engine_probability")
        results = []
        for forecast in forecasts:
            probability = forecast.get("probability")
            if probability is None:
                continue
            probability = float(probability)
            dist_to_outcome = abs(probability - outcome_value)
            if dist_to_outcome > threshold:
                continue
            dist_to_engine = (
                abs(probability - float(engine_probability))
                if engine_probability is not None
                else None
            )
            results.append(
                {
                    "uid": int(forecast["uid"]),
                    "hotkey": forecast.get("hotkey", ""),
                    "probability": probability,
                    "prediction": forecast.get("prediction"),
                    "confidence": forecast.get("confidence"),
                    "reasoning": forecast.get("reasoning", ""),
                    "model": forecast.get("model", ""),
                    "features": dict(forecast.get("features") or {}),
                    "issued_at": forecast.get("issued_at"),
                    "submitted_at": forecast.get("submitted_at"),
                    "brier": brier_score(probability, outcome),
                    "dist_to_outcome": dist_to_outcome,
                    "dist_to_engine": dist_to_engine,
                }
            )
        return {
            "run_id": run_id,
            "question_key": question_key,
            "family": sample.get("family", ""),
            "scope": sample.get("scope", ""),
            "netuid": sample.get("netuid"),
            "horizon_days": sample.get("horizon_days"),
            "outcome": outcome,
            "measurement_value": resolution.measurement_value,
            "resolved_at": resolution.resolved_at or datetime.now(timezone.utc).isoformat(),
            "engine_probability": engine_probability,
            "results": results,
        }

    def _bt_forecast_client(self):
        client = getattr(self, "bt_forecast_client", None)
        if client is None:
            client = open_bt_forecast_client_from_env()
            self.bt_forecast_client = client
        return client

    def _bt_forecast_required(self) -> bool:
        if not hasattr(self, "bt_forecast_required"):
            self.bt_forecast_required = bt_forecast_required_from_env()
        return bool(self.bt_forecast_required)

    def get_miner_uids(self) -> list[int]:
        """All registered neurons that are serving an axon (i.e., miners)."""
        uids = []
        query_validators = _env_flag(C.QUERY_VALIDATOR_UIDS_ENV, False)
        validator_permit = getattr(self.metagraph, "validator_permit", None)
        for uid in range(self._metagraph_size()):
            if not self.metagraph.axons[uid].is_serving:
                continue
            if self.metagraph.hotkeys[uid] == self.wallet.hotkey.ss58_address:
                continue
            if (
                not query_validators
                and validator_permit is not None
                and bool(validator_permit[uid])
            ):
                continue
            uids.append(uid)
        return uids

    def _metagraph_size(self) -> int:
        n = getattr(self.metagraph, "n", 0)
        return int(n.item()) if hasattr(n, "item") else int(n)

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
