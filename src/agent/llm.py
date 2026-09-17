import os
import logging
from typing import Any

try:
    from langchain.chat_models import ChatOpenAI
    from langchain.embeddings import OpenAIEmbeddings   
    
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

logger = logging.getLogger(__name__)

_llm_instance = None
_embeddings_instance = None


class MockLLM:
    def invoke(self, prompt: str) -> str:
        return "Mocked LLM Response"


class MockEmbeddings:
    def embed_query(self, query: str) -> list:
        return [0.0] * 768


def get_llm() -> Any:
    global _llm_instance

    if _llm_instance is None:

        if (
            LANGCHAIN_AVAILABLE
            and os.environ.get("LITELLM_API_KEY")
            and os.environ.get("LITELLM_BASE_URL")
        ):
            logger.info("Initializing Gemini through LiteLLM")

            _llm_instance = ChatOpenAI(
                model=os.environ.get(
                    "LLM_MODEL",
                    "gemini-2.5-flash"
                ),
                temperature=0,
                api_key=os.environ["LITELLM_API_KEY"],
                base_url=os.environ["LITELLM_BASE_URL"],
            )

        else:
            logger.warning(
                "LiteLLM configuration missing. Using MockLLM."
            )
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
                model=os.environ.get(
                    "EMBEDDING_MODEL",
                    "text-embedding-004"
                ),
                api_key=os.environ["LITELLM_API_KEY"],
                base_url=os.environ["LITELLM_BASE_URL"],
            )

        else:
            logger.warning(
                "LiteLLM configuration not found. "
                "Using MockEmbeddings."
            )
            _embeddings_instance = MockEmbeddings()

    return _embeddings_instance