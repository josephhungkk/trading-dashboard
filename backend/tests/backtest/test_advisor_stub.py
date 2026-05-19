from __future__ import annotations

from app.backtest.advisor_stub import AdvisorStub, VetoInjection


class TestAdvisorStubFromConfig:
    def test_from_config_none_returns_empty_stub(self):
        stub = AdvisorStub.from_config(None)
        assert stub.enabled is False

    def test_from_config_empty_dict_returns_empty_stub(self):
        stub = AdvisorStub.from_config({})
        assert stub.enabled is False

    def test_from_config_parses_injections(self):
        config = {
            "veto_injections": [
                {"bar_index": 5, "canonical_id": "AAPL", "reasoning": "test veto"},
                {"bar_index": 10, "canonical_id": "*"},
            ]
        }
        stub = AdvisorStub.from_config(config)
        assert stub.enabled is True

    def test_from_config_defaults_canonical_id_to_wildcard(self):
        config = {"veto_injections": [{"bar_index": 3}]}
        stub = AdvisorStub.from_config(config)
        verdict, _reasoning, _latency = stub.review(3, "TSLA", None)
        assert verdict.action == "veto"


class TestAdvisorStubReview:
    def test_approve_when_no_injection(self):
        stub = AdvisorStub([VetoInjection(bar_index=5, canonical_id="AAPL")])
        verdict, _reasoning, latency = stub.review(99, "AAPL", None)
        assert verdict.action == "approve"
        assert latency == 0

    def test_veto_at_matching_bar_and_canonical_id(self):
        stub = AdvisorStub(
            [VetoInjection(bar_index=5, canonical_id="AAPL", reasoning="bad signal")]
        )
        verdict, reasoning, latency = stub.review(5, "AAPL", None)
        assert verdict.action == "veto"
        assert reasoning == "bad signal"
        assert latency == 0

    def test_approve_when_canonical_id_mismatch(self):
        stub = AdvisorStub([VetoInjection(bar_index=5, canonical_id="AAPL")])
        verdict, _, _ = stub.review(5, "MSFT", None)
        assert verdict.action == "approve"

    def test_veto_wildcard_canonical_id_matches_any(self):
        stub = AdvisorStub([VetoInjection(bar_index=7, canonical_id="*")])
        for symbol in ["AAPL", "TSLA", "SPY"]:
            verdict, _, _ = stub.review(7, symbol, None)
            assert verdict.action == "veto"

    def test_multiple_injections_at_different_bars(self):
        stub = AdvisorStub(
            [
                VetoInjection(bar_index=1, canonical_id="A"),
                VetoInjection(bar_index=2, canonical_id="B"),
            ]
        )
        v1, _, _ = stub.review(1, "A", None)
        v2, _, _ = stub.review(2, "B", None)
        v3, _, _ = stub.review(1, "B", None)
        assert v1.action == "veto"
        assert v2.action == "veto"
        assert v3.action == "approve"

    def test_enabled_false_when_no_injections(self):
        stub = AdvisorStub([])
        assert stub.enabled is False

    def test_enabled_true_when_injections_exist(self):
        stub = AdvisorStub([VetoInjection(bar_index=0, canonical_id="X")])
        assert stub.enabled is True
