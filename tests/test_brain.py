"""Brain (DeepSeek) + validator + persistent-memory tests (mocked LLM)."""
import json
import os

from parallax.contracts import DecisionClass, InstrumentId, InstrumentType
from parallax.core.brain import Brain, BrainAssessment, DeepSeekClient, ValidatorEnsemble
from parallax.core.hypotheses import HypothesisEngine
from parallax.core.memory import BrainMemory
from parallax.core.decision import DecisionConfig, DecisionEngine

from conftest import make_bars, make_state


class FakeLLM:
    def __init__(self, response=None, enabled=True):
        self._response = response
        self._enabled = enabled

    @property
    def enabled(self):
        return self._enabled

    def complete_json(self, messages, temperature=0.2):
        return self._response


def _state():
    return make_state(inst=InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE),
                      bars=make_bars(seed=3, drift=2.5))


def _hyps(state):
    return HypothesisEngine().generate(state)


def test_llm_parse_valid_json():
    llm = FakeLLM({"direction": "long", "confidence": 0.72, "verdict": "approve",
                   "thesis": "sweep + displacement", "counter_argument": "chop",
                   "uncertainty": "low", "reasons": ["aligned"]})
    a = Brain(llm=llm).think(_state(), _hyps(_state()))
    assert a.direction == "long"
    assert a.verdict == "approve"
    assert a.llm_used is True


def test_llm_invalid_json_falls_back():
    llm = FakeLLM({"bogus": 1})
    a = Brain(llm=llm).think(_state(), _hyps(_state()))
    assert a.llm_used is False
    assert a.verdict in ("approve", "reduce", "reject")


def test_llm_unavailable_falls_back():
    llm = FakeLLM(enabled=False)
    a = Brain(llm=llm).think(_state(), _hyps(_state()))
    assert a.llm_used is False
    assert a.direction in ("long", "short", "flat")


def test_confidence_clipped():
    llm = FakeLLM({"direction": "short", "confidence": 99.0, "verdict": "reject"})
    a = Brain(llm=llm).think(_state(), _hyps(_state()))
    assert a.confidence == 1.0


def test_mask_hides_key():
    c = DeepSeekClient(api_key="sk-abc123secret")
    m = c.masked()
    assert "sk-abc123secret" not in str(m)
    assert m["api_key"].startswith("set(") and "chars)" in m["api_key"]


def test_validator_rejects_on_brain_reject():
    llm = FakeLLM({"direction": "flat", "confidence": 0.3, "verdict": "reject"})
    v = ValidatorEnsemble(brain=Brain(llm=llm))
    state = _state()
    dec = DecisionEngine(DecisionConfig(atr_stop_mult=4.0)).decide(
        state, _hyps(state), kill_switch=False)
    if dec.decision_class != DecisionClass.TRADE:
        return  # nothing to validate
    result = v.validate(dec, state, _hyps(state))
    assert result is not None and result.allowed is False


def test_validator_none_for_non_trade():
    llm = FakeLLM({"direction": "flat", "confidence": 0.5, "verdict": "reject"})
    v = ValidatorEnsemble(brain=Brain(llm=llm))
    state = _state()
    dec = DecisionEngine().decide(state, _hyps(state))
    if dec.decision_class == DecisionClass.TRADE:
        return
    assert v.validate(dec, state, _hyps(state)) is None


def test_typesafe_judge_maps_typed_answers():
    from parallax.core.brain.typesafe import TypeSafeSystemOne

    class FakeTS(TypeSafeSystemOne):
        def __init__(self):
            super().__init__(api_key="k")

        def _ask(self, state, questions):
            return {"answers": {
                "direction_correct": {"noul": 0.72},
                "edge_sufficient": {"noul": 0.35},
                "action": {"choice": "reduce"},
                "setup_quality": {"score": 2.0},
            }}

    a = FakeTS().judge({}, proposed_direction="long")
    assert a.direction == "long"
    assert a.confidence == 0.72
    assert a.verdict == "reduce"
    assert a.provider == "TypeSafeSystemOne"
    assert a.llm_used is True


def test_typesafe_disabled_returns_none():
    from parallax.core.brain.typesafe import TypeSafeSystemOne
    ts = TypeSafeSystemOne(api_key="")  # no key -> disabled
    assert ts.judge({}, "long") is None


def test_ensemble_is_conservative():
    # a DeepSeek "approve" + a TypeSafe "reject" => combined verdict is reject
    from parallax.core.brain.brain import combine_assessments
    from parallax.core.brain import BrainAssessment
    a = BrainAssessment("long", 0.8, "approve", provider="DeepSeek")
    b = BrainAssessment("long", 0.65, "reject", provider="TypeSafeSystemOne")
    combined = combine_assessments([a, b])
    assert combined.verdict == "reject"
    assert combined.confidence == 0.65  # minimum, not average


def test_brain_memory_persists(tmp_path):
    path = os.path.join(str(tmp_path), "brain.json")
    bm = BrainMemory(path)
    bm.remember_assessment({"direction": "long", "verdict": "approve"})
    bm.record_outcome("WIN", "BUY")
    bm.add_lesson("opening sweep worked")
    bm2 = BrainMemory(path)
    assert len(bm2.recent()) >= 1
    assert bm2.outcome_counts().get("WIN") == 1
    assert "opening sweep worked" in bm2.lessons()
