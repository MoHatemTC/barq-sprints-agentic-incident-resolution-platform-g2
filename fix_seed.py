import json

with open('data/adversarial/sprint3_seed_set.json', 'r') as f:
    cases = json.load(f)

for case in cases:
    if case['sys_id'] == 'ADV_PII_001':
        case['description'] = case['description'].replace('password is: ', 'password: ')

with open('data/adversarial/sprint3_seed_set.json', 'w') as f:
    json.dump(cases, f, indent=2)
