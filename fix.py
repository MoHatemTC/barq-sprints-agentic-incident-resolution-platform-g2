import os

# Fix 1: output_validation.py
path_out_val = 'src/agent/guardrails/output_validation.py'
with open(path_out_val, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace('resolution_text', 'resolution')
with open(path_out_val, 'w', encoding='utf-8') as f:
    f.write(content)

# Fix 1.b: test_guardrails_output.py
path_test_out = 'tests/test_guardrails_output.py'
with open(path_test_out, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace('resolution_text', 'resolution')
with open(path_test_out, 'w', encoding='utf-8') as f:
    f.write(content)

# Fix 1.c: test_nodes.py
path_test_nodes = 'tests/test_nodes.py'
if os.path.exists(path_test_nodes):
    with open(path_test_nodes, 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace('resolution_text', 'resolution')
    with open(path_test_nodes, 'w', encoding='utf-8') as f:
        f.write(content)

# Fix 2: generate_node
path_gen = 'src/agent/nodes/generate.py'
with open(path_gen, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace('outputs[\"resolution\"] =', 'outputs[\"proposed_action\"] = \"update_incident\"\n    outputs[\"resolution\"] =')
with open(path_gen, 'w', encoding='utf-8') as f:
    f.write(content)

# Fix 3: interrupt_node
path_int = 'src/agent/nodes/interrupt.py'
with open(path_int, 'r', encoding='utf-8') as f:
    content = f.read()

new_int_logic = '''
    if state.get("action_taken") == "blocked_by_guardrail":
        return {
            "action_taken": state.get("action_taken"),
            "human_review_required": True,
            "failure_reason": state.get("failure_reason"),
        }

    if risk == "high":'''
content = content.replace('    if risk == "high":', new_int_logic.strip('\n'))
with open(path_int, 'w', encoding='utf-8') as f:
    f.write(content)

# Fix 4: requirements.txt
path_req = 'requirements.txt'
with open(path_req, 'a', encoding='utf-8') as f:
    f.write('\npytesseract==0.3.10\npdf2image==1.17.0\n')

# Fix 5: create ocr stressors dir
os.makedirs('data/corpus/stressors/ocr', exist_ok=True)
with open('data/corpus/stressors/ocr/.keep', 'w') as f:
    f.write('')
