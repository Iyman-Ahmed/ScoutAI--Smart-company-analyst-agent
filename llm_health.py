"""Non-fatal startup check for model access with the deployment's own key."""
import logging
import os
import re

logger = logging.getLogger(__name__)


def warn_unavailable_models(api_key, models, client_factory=None):
    if not api_key:
        logger.warning("Groq startup check skipped: GROQ_API_KEY is not configured.")
        return
    try:
        if client_factory is None:
            from groq import Groq
            client_factory = Groq
        with client_factory(api_key=api_key, timeout=10, max_retries=0) as client:
            available = {model.id for model in client.models.list().data}
        for variable, model in models.items():
            if model not in available:
                logger.warning(
                    "Groq startup check: %s=%s is absent from GET /openai/v1/models "
                    "for this deployment key. Verify its account access or configure %s; "
                    "model absence does not establish global retirement.", variable, model, variable)
    except Exception as error:
        message = str(error).replace(api_key, "[REDACTED]")
        for name in ("GROQ_API_KEY", "CEREBRAS_API_KEY"):
            secret = os.getenv(name)
            if secret:
                message = message.replace(secret, "[REDACTED]")
        message = re.sub(r"gsk_[A-Za-z0-9_-]+", "[REDACTED]", message)
        message = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", message)
        logger.warning("Groq startup model check unavailable: %s: %s", type(error).__name__, message)
