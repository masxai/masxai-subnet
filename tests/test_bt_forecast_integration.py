from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from masxai.oracle_bt import BtForecastQuestion, BtForecastResolution, BtForecastRunStatus
from neurons.validator import Validator


class _FakeAxon:
    def __init__(self, hotkey: str):
        self.hotkey = hotkey
        self.is_serving = True


class _FakeMetagraph:
    hotkeys = ["validator-hotkey", "miner-hotkey-1", "miner-hotkey-2"]
    n = np.int64(3)

    def __init__(self):
        self.axons = [_FakeAxon(hotkey) for hotkey in self.hotkeys]


class _FakeWallet:
    hotkey = SimpleNamespace(ss58_address="validator-hotkey")


class _FakeDendrite:
    def __init__(self):
        self.synapses = []

    async def __call__(self, axons, synapse, deserialize=False, timeout=0):
        self.synapses.append(synapse)
        responses = []
        for axon in axons:
            resp = synapse.model_copy(deep=True)
            resp.probability = 0.93 if axon.hotkey.endswith("1") else 0.40
            resp.prediction = resp.probability >= 0.5
            resp.confidence = max(resp.probability, 1.0 - resp.probability)
            resp.reasoning = "central BT-Forecast test response"
            resp.timestamp = "2026-07-16T00:00:00+00:00"
            resp.model = "fake"
            responses.append(resp)
        return responses


class _FakeBtForecastClient:
    def __init__(self):
        self.posts = []
        self.include_lineage_calls = []

    async def get_run(self, run_id: str):
        return BtForecastRunStatus(run_id=run_id, status="ready", question_count=1)

    async def get_questions(self, run_id: str, include_lineage: bool = False):
        self.include_lineage_calls.append(include_lineage)
        return [
            BtForecastQuestion(
                question_id="pred-1",
                question_key="Will SN12 active miner count fall below by daily snapshot|SN12|2099-01-01",
                question="Will SN12 active miner count fall below 128 by 2099-01-01?",
                family="active_miners",
                scope="subnet",
                netuid=12,
                horizon_days=14,
                cutoff_date="2099-01-01T06:00:00Z",
                resolution_criteria="Resolved from the daily on-chain snapshot.",
                evidence_summary="SN12 miners 141 -> 133 over 7d.",
                measurement={"threshold": 128, "operator": "below"},
                engine_probability=0.31 if include_lineage else None,
            )
        ]

    async def get_resolutions(self, run_id: str):
        return [
            BtForecastResolution(
                question_key="Will SN12 active miner count fall below by daily snapshot|SN12|2099-01-01",
                status="resolved_true",
                outcome=True,
                resolved_at="2099-01-01T06:04:10Z",
                measurement_value=126,
            )
        ]

    async def post_miner_results(self, payload):
        self.posts.append(payload)
        return {"ok": True}


def _validator(fake_client: _FakeBtForecastClient) -> Validator:
    validator = Validator.__new__(Validator)
    validator.pending = {}
    validator.issued_questions = {}
    validator.feedback_queue = []
    validator.bt_forecast_client = fake_client
    validator.bt_forecast_required = True
    validator.resolved_count = 0
    validator.last_issue_at = 0.0
    validator.metagraph = _FakeMetagraph()
    validator.wallet = _FakeWallet()
    validator.dendrite = _FakeDendrite()
    validator.scores = np.zeros(3, dtype=np.float32)
    return validator


def test_validator_issues_bt_forecast_question_without_engine_answer(monkeypatch):
    monkeypatch.setenv("MASXAI_FORECAST_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("BT_FORECAST_RUN_ID", "bt-test")
    monkeypatch.setenv("BT_FORECAST_INCLUDE_LINEAGE", "true")
    fake_client = _FakeBtForecastClient()
    validator = _validator(fake_client)

    asyncio.run(validator.issue_round())

    assert len(validator.pending) == 2
    synapse = validator.dendrite.synapses[0]
    assert synapse.question_key
    assert synapse.family == "active_miners"
    assert synapse.netuid == 12
    assert synapse.horizon_days == 14
    assert "0.31" not in synapse.context
    assert validator._bt_pending_key("bt-test", synapse.question_key, 1) in validator.pending
    assert validator._bt_pending_key("bt-test", synapse.question_key, 2) in validator.pending


def test_validator_lineage_is_opt_in_by_default(monkeypatch):
    monkeypatch.setenv("MASXAI_FORECAST_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("BT_FORECAST_RUN_ID", "bt-test")
    monkeypatch.delenv("BT_FORECAST_INCLUDE_LINEAGE", raising=False)
    fake_client = _FakeBtForecastClient()
    validator = _validator(fake_client)

    asyncio.run(validator.issue_round())

    assert fake_client.include_lineage_calls == [False]
    assert all(item["engine_probability"] is None for item in validator.pending.values())


def test_validator_resolves_bt_forecast_and_posts_accurate_miners(monkeypatch):
    monkeypatch.setenv("MASXAI_FORECAST_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("BT_FORECAST_RUN_ID", "bt-test")
    monkeypatch.setenv("BT_FORECAST_INCLUDE_LINEAGE", "true")
    fake_client = _FakeBtForecastClient()
    validator = _validator(fake_client)

    asyncio.run(validator.issue_round())
    for forecast in validator.pending.values():
        forecast["resolve_at"] = 1.0

    asyncio.run(validator.resolve_due())

    assert validator.pending == {}
    assert validator.scores[1] > validator.scores[2]
    assert len(fake_client.posts) == 1
    assert [row["uid"] for row in fake_client.posts[0]["results"]] == [1]
    assert fake_client.posts[0]["engine_probability"] == 0.31


def test_validator_skips_validator_permit_uids_by_default(monkeypatch):
    monkeypatch.delenv("MASXAI_QUERY_VALIDATOR_UIDS", raising=False)
    fake_client = _FakeBtForecastClient()
    validator = _validator(fake_client)
    validator.metagraph.validator_permit = np.array([True, False, True])

    assert validator.get_miner_uids() == [1]
