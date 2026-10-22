import json
import pytest
from services.operations_brief import build_operations_brief, build_agent_question

SUMMARY = {'campaign_id': 101, 'attempts': 10, 'answered_attempts': 4,
           'answer_rate_pct': 40, 'customer_phone': '+9000000'}
VARIANCE = {'campaign_id': 101, 'allocated_ports': 5,
            'ideal_attempts_per_hour': 900, 'hourly': [
                {'hour': '2026-10-26T09:00:00+03:00', 'attempts': 10,
                 'ratio_to_ideal_pct': 1.11, 'below_ideal_pct': 98.89,
                 'port_id': 'secret'}]}


def test_summary_no_personal_data():
    brief = build_operations_brief(SUMMARY, VARIANCE)
    data = json.dumps(brief)
    assert '+9000000' not in data and 'secret' not in data
    assert brief['campaign']['attempts'] == 10


def test_prompt_contains_caveats():
    question = build_agent_question(build_operations_brief(SUMMARY, VARIANCE))
    assert 'kesin teşhis' in question and '900' in question


def test_campaign_mismatch():
    with pytest.raises(ValueError):
        build_operations_brief(SUMMARY, {**VARIANCE, 'campaign_id': 102})


def test_hour_limit():
    for bad in (0, 49, True, 1.5):
        with pytest.raises(ValueError):
            build_operations_brief(SUMMARY, VARIANCE, max_hours=bad)


def test_missing_prompt_data():
    with pytest.raises(ValueError):
        build_agent_question({})
