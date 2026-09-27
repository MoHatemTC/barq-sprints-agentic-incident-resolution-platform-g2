import json
import pytest
from src.agent.guardrails.input_screening import screen_incident_payload
from src.agent.guardrails.output_validation import validate_agent_output

def test_adversarial_seed_set():
    with open('data/adversarial/sprint3_seed_set.json', 'r') as f:
        cases = json.load(f)
    
    for case in cases:
        adv_type = case['adversarial_type']
        desc = case['description']
        
        if adv_type == 'injection' or adv_type == 'pii':
            screened_payload, meta = screen_incident_payload({'description': desc})
            
            if adv_type == 'injection':
                assert meta.injection_flagged is True, f"Failed to flag {adv_type} case: {case['sys_id']}"
                assert '[SCREENED_CONTENT]' in screened_payload['description']
            else:
                assert meta.redaction_count > 0, f"Failed to flag {adv_type} case: {case['sys_id']}"
                assert '[REDACTED_' in screened_payload['description']
            
        elif adv_type == 'disallowed_action':
            state = {
                'outputs': {
                    'proposed_action': desc, 
                    'resolution': 'resolution'
                }
            }
            res = validate_agent_output(state)
            assert res.is_valid is False
            assert any('ACTION_BLOCKED' in r for r in res.block_reasons), f"Failed to block disallowed action case: {case['sys_id']}"

