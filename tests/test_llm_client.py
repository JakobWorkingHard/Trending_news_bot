"""
Tester for LLMClient (llm/llm_client.py).

LLMClient gör nätverksanrop och kräver LLM_API_KEY. Dessa tester
säkerställer att:
 - Initiering misslyckas tydligt (ValueError) om nyckel saknas
 - Riktiga API-anrop ALDRIG görs i tester (OpenAI är mockad)
 - generate_response skickar rätt model/temperature/messages
 - Tillfälliga API-fel fångas och returnerar ett felmeddelande
   istället för att krascha hela pipelinen
"""
from unittest.mock import MagicMock, patch

import pytest

from trending_news_bot.llm.llm_client import LLMClient


# --- Initiering ---


def test_init_requires_api_key(isolated_env):
    # Ingen LLM_API_KEY i miljön och .env patchad bort → ValueError
    with patch("trending_news_bot.llm.llm_client.load_dotenv"):
        with pytest.raises(ValueError, match="LLM_API_KEY"):
            LLMClient()


def test_init_with_api_key_does_not_raise(llm_with_key):
    # Med fejkad nyckel och mockad OpenAI ska initiering gå igenom
    client = LLMClient()
    assert client.model == "zai-org/GLM-5.2"
    assert client.temperature == 0.7
    assert client.client is llm_with_key


def test_init_passes_model_and_temperature(llm_with_key):
    # Custom parametrar ska sättas korrekt
    client = LLMClient(model="custom-model", temperature=0.1)
    assert client.model == "custom-model"
    assert client.temperature == 0.1


def test_init_default_base_url_points_to_evroc(llm_with_key):
    # Utan explicit base_url ska LLMClient använda evroc-slutpunkten
    client = LLMClient()
    assert client.base_url == "https://models.think.evroc.com/v1"


def test_init_accepts_custom_base_url(llm_with_key):
    # En custom base_url (t.ex. OpenAI, lokal Ollama) ska sättas korrekt
    # så en ny användare kan peka om till valfri OpenAI-kompatibel provider
    # utan att ändra koden.
    custom_url = "https://api.openai.com/v1"
    client = LLMClient(base_url=custom_url)
    assert client.base_url == custom_url


# --- generate_response ---


def _make_mock_response(text: str) -> MagicMock:
    """Bygger en mock som ser ut som ett OpenAI-svar."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


def test_generate_response_success(llm_with_key):
    # Mocka API:et att returnera en specifik text och verifiera anropet
    mock_response = _make_mock_response("Här är din trendanalys.")
    llm_with_key.chat.completions.create.return_value = mock_response

    client = LLMClient()
    result = client.generate_response("system-prompt", "user-prompt")

    assert result == "Här är din trendanalys."

    # Verifiera att API:et anropats med rätt parametrar
    llm_with_key.chat.completions.create.assert_called_once()
    call_kwargs = llm_with_key.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == "zai-org/GLM-5.2"
    assert call_kwargs.kwargs["temperature"] == 0.7
    messages = call_kwargs.kwargs["messages"]
    assert messages == [
        {"role": "system", "content": "system-prompt"},
        {"role": "user", "content": "user-prompt"},
    ]


def test_generate_response_returns_error_string_on_exception(llm_with_key):
    # Om API:et kastar ska metoden fångar det och returnera felmeddelande,
    # inte låta undantaget propagera vidare och krascha pipelinen
    llm_with_key.chat.completions.create.side_effect = RuntimeError("API nere")

    client = LLMClient()
    result = client.generate_response("sys", "usr")

    assert result.startswith("Fel vid generering:")
    assert "API nere" in result
