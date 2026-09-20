import os
import logging
from typing import Any
from dotenv import load_dotenv

load_dotenv()

try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    LANGCHAIN_AVAILABLE = True
except ImportError:
    try:
        from langchain.chat_models import ChatOpenAI
        from langchain.embeddings import OpenAIEmbeddings
        LANGCHAIN_AVAILABLE = True
    except ImportError:
        LANGCHAIN_AVAILABLE = False

logger = logging.getLogger(__name__)

_llm_instance = None
_embeddings_instance = None


class MockEmbeddings:
    """Fallback embeddings when langchain-openai is not installed."""

    def embed_query(self, query: str) -> list:
        return [0.0] * 768

    def embed_documents(self, texts: list) -> list:
        return [[0.0] * 768 for _ in texts]


class MockLLM:
    """Fallback LLM when langchain-openai is not installed."""

    def invoke(self, prompt: str, **kwargs) -> str:
        if 'determine if it is "high" risk or "low" risk' in prompt:
            if "high-risk" in prompt or "major data breach" in prompt or "data center wipe" in prompt:
                return "high"
            return "low"
        return "Mocked LLM Response"

def get_llm() -> Any:
    global _llm_instance
    if _llm_instance is None:
        if (
            LANGCHAIN_AVAILABLE
            and os.environ.get("LITELLM_API_KEY")
            and os.environ.get("LITELLM_BASE_URL")
        ):
            logger.info("Initializing Gemini through LiteLLM")
            model_name = os.environ.get("LLM_MODEL", "gemini-3.6-flash")
            if "2.5" in model_name or "2.0" in model_name or "1.5" in model_name:
                model_name = "gemini/gemini-3.6-flash"
            elif not model_name.startswith("gemini/") and "gemini" in model_name:
                model_name = f"gemini/{model_name}"

            _llm_instance = ChatOpenAI(
                model=model_name,
                temperature=0,
                api_key=os.environ["LITELLM_API_KEY"],
                base_url=os.environ["LITELLM_BASE_URL"],
            )
        else:
            logger.warning("LiteLLM configuration missing. Using MockLLM.")
            _llm_instance = MockLLM()
    return _llm_instance


def get_embeddings() -> Any:
    global _embeddings_instance
    if _embeddings_instance is None:
        if (
            LANGCHAIN_AVAILABLE
            and os.environ.get("LITELLM_API_KEY")
            and os.environ.get("LITELLM_BASE_URL")
        ):
            logger.info("Initializing embeddings through LiteLLM")
            _embeddings_instance = OpenAIEmbeddings(
                model=os.environ.get("EMBEDDING_MODEL", "text-embedding-004"),
                api_key=os.environ["LITELLM_API_KEY"],
                base_url=os.environ["LITELLM_BASE_URL"],
            )
        else:
            logger.warning("LiteLLM configuration not found. Using MockEmbeddings.")
            _embeddings_instance = MockEmbeddings()
    return _embeddings_instance