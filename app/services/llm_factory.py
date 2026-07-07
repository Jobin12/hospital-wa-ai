import logging
from flask import current_app
from langchain_openai import ChatOpenAI


def get_llm():
    """
    Build a LangChain chat model based on the configured provider.

    The provider is selected via the LLM_PROVIDER env var (loaded into app config):
      - "openai" (default) -> ChatOpenAI
      - "gemini"           -> ChatGoogleGenerativeAI

    Both models expose the same LangChain interface, so the agent and tool-calling
    loop work identically regardless of provider. Switching providers only requires
    updating the .env file - no code changes.
    """
    provider = (current_app.config.get("LLM_PROVIDER") or "openai").lower()

    if provider == "gemini":
        api_key = current_app.config.get("GOOGLE_API_KEY")
        model = current_app.config.get("GEMINI_MODEL", "gemini-2.0-flash")
        if not api_key:
            logging.error("GOOGLE_API_KEY is not set but LLM_PROVIDER=gemini.")
            raise ValueError("GOOGLE_API_KEY is required when LLM_PROVIDER=gemini")
        # Imported lazily so the openai-only deployments don't need this package loaded.
        from langchain_google_genai import ChatGoogleGenerativeAI

        logging.info(f"Using Gemini LLM provider (model={model})")
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, max_retries=1)

    if provider != "openai":
        logging.warning(f"Unknown LLM_PROVIDER '{provider}'. Falling back to OpenAI.")

    api_key = current_app.config.get("OPENAI_API_KEY")
    model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-nano")
    if not api_key:
        logging.error("OPENAI_API_KEY is not set.")
        raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")

    logging.info(f"Using OpenAI LLM provider (model={model})")
    return ChatOpenAI(model=model, api_key=api_key, max_retries=1)
