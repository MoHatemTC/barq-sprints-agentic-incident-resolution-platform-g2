import os
import json
from src.agent.graph import compile_graph
from src.agent.state import AgentState
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver

# Stub out the LLM calls so the script runs reliably without real keys
from unittest.mock import patch, MagicMock

def _build_mock_llm(content):
    mock = MagicMock()
    resp = MagicMock()
    resp.content = content
    mock.invoke.return_value = resp
    return mock

# The mock state will track the calls to classify to show re-classification
classify_calls = []

def mock_classify_invoke(*args, **kwargs):
    if len(classify_calls) == 0:
        classify_calls.append("software")
        return MagicMock(content="software")
    classify_calls.append("database")
    return MagicMock(content="database")

def main():
    print("=========================================================")
    print(" LIVE RUN LOG: Re-classification via Human Feedback      ")
    print("=========================================================\n")

    # Mocks for all nodes in the path
    mock_validate = _build_mock_llm("valid")
    
    mock_classify_llm = MagicMock()
    mock_classify_llm.invoke.side_effect = mock_classify_invoke
    
    mock_risk = _build_mock_llm("high")
    mock_query = _build_mock_llm('{"query": "database connection pool error"}')
    mock_diagnose = _build_mock_llm(json.dumps({
        "root_cause": "PostgreSQL connection pool exhausted",
        "reasoning": "Matching KB article found.",
        "supporting_evidence": ["KB0001"],
        "confidence": 0.95
    }))
    mock_generate = _build_mock_llm("1. Restart PostgreSQL [Source: KB0001]")
    mock_verify = _build_mock_llm(json.dumps({
        "passed": True, "feedback": "", "invalid_steps": [], "citation_findings": []
    }))
    
    with patch("src.agent.nodes.validate.get_llm", return_value=mock_validate), \
         patch("src.agent.nodes.classify.get_llm", return_value=mock_classify_llm), \
         patch("src.agent.nodes.determine_risk.get_llm", return_value=mock_risk), \
         patch("src.agent.nodes.formulate_query.get_llm", return_value=mock_query), \
         patch("src.agent.nodes.diagnose.get_llm", return_value=mock_diagnose), \
         patch("src.agent.nodes.generate.get_llm", return_value=mock_generate), \
         patch("src.agent.nodes.verify_evidence.get_llm", return_value=mock_verify), \
         patch("src.agent.nodes.retrieve.search", return_value=[MagicMock(number="KB0001", point_id="KB0001", score=0.9, text="Restart Postgres")]), \
         patch("src.db.knowledge_capture_service.record_knowledge_capture"):
        
        graph = compile_graph(checkpointer=MemorySaver())
        thread_config = {"configurable": {"thread_id": "live-trace-01"}}
        
        # 1. Start the run
        print("[System] -> Starting incident resolution for vague issue: 'System slow and unresponsive'")
        
        graph.invoke({
            "execution_id": "live-trace-01",
            "incident_number": "INC0000001",
            "incident_payload": {"description": "System slow and unresponsive", "short_description": "Slow system"}
        }, config=thread_config)
        
        state = graph.get_state(thread_config)
        print(f"[Agent] -> Initial classification determined as: '{state.values.get('classification')}'")
        print(f"[Agent] -> Risk level determined as: '{state.values.get('risk')}'")
        print(f"[Agent] -> High risk incident detected. Interrupting for human review.\n")
        
        # 2. Resume with human feedback
        human_decision = {
            "decision": "approve",
            "reviewer": "Alice",
            "comment": "This is clearly a database issue.",
            "human_solution": "The PostgreSQL connection pool was exhausted. Restart the database service."
        }
        
        print(f"[Reviewer] -> Approves execution with feedback:")
        print(f"            Comment: {human_decision['comment']}")
        print(f"            Solution: {human_decision['human_solution']}\n")
        
        print("[System] -> Resuming graph execution and routing back to classify node...\n")
        
        final_state = graph.invoke(Command(resume=human_decision), config=thread_config)
        
        # 3. Show the updated state
        print(f"[Agent] -> Re-classification triggered with human feedback.")
        print(f"[Agent] -> Updated classification determined as: '{final_state.get('classification')}'")
        print(f"[Agent] -> Downstream Retrieval/Diagnosis completed successfully.")
        print(f"[Agent] -> Final Resolution Drafted:\n")
        print(f"           {final_state.get('outputs', {}).get('resolution')}")
        print("\n=========================================================")

if __name__ == "__main__":
    main()
