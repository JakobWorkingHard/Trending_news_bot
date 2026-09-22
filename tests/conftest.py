"""
Gemensamma fixtures för test-suiten.

Dessa fixtures isolerar varje test från:
 - Riktig databas (tmp_path)
 - Riktiga API-nycklar (.env)
 - Riktiga nätverksanrop (mockad OpenAI)
 - Riktig konfiguration (fejkade config/-filer)
"""
import json
import logging
from unittest.mock import patch, MagicMock

import pytest

from trending_news_bot.core.database import DatabaseManager


# --- Databas ---


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Skapar en DatabaseManager vars data/-mapp hamnar i tmp_path.
    Varje test får därmed en helt isolerad SQLite-fil.
    """
    monkeypatch.chdir(tmp_path)
    return DatabaseManager()


# --- LLM / API ---


@pytest.fixture
def isolated_env(monkeypatch):
    """
    Rensar LLM_API_KEY om den råkar finnas i miljön,
    så att tester kan kontrollera nyckeln explicit.
    """
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    return monkeypatch


@pytest.fixture
def mock_openai_client():
    """
    Patchar OpenAI-klienten i llm_client-modulen så inga riktiga
    nätverksanrop eller anrop mot models.think.evroc.com görs.
    Returnerar mock-instansen som används som self.client.
    """
    with patch("trending_news_bot.llm.llm_client.OpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def llm_with_key(monkeypatch, mock_openai_client):
    """
    Sätter en fejkad LLM_API_KEY och patchar load_dotenv så .env
    inte läses. Returnerar den mockade OpenAI-klienten.
    """
    monkeypatch.setenv("LLM_API_KEY", "fake-test-key")
    with patch("trending_news_bot.llm.llm_client.load_dotenv"):
        yield mock_openai_client


# --- Konfiguration ---


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """
    Skapar fejkade config/settings.json och config/sources.json i tmp_path
    och chdir dit, så ContentManager hittar dem. Returnerar en dict med
    innehållet för bekväm assertion i testerna.
    """
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    settings = {
        "scraping": {"limit_per_feed": 5},
        "llm": {"model": "test-model", "temperature": 0.5},
        "trend_analysis": {
            "system_prompt": "Test system prompt",
            "user_prompt_template": "Artiklar:\n{articles}",
            "user_prompt_with_summary_template": "Artiklar med sammanfattning:\n{articles}",
            "articles_format_template": "{index}. {title} ({source_site})",
            "articles_format_with_summary_template": "{index}. {title} ({source_site}) — {summary}",
            "max_articles_for_analysis": 50,
            "min_articles_for_analysis": 3,
            "default_hours": 24,
            "max_prompt_characters": 12000,
            "max_summary_characters": 500,
            "summary_system_prompt": "Test summary system prompt",
            "summary_article_format_template": "Artikel [{index}]: {title}\n{content}",
            "max_summary_prompt_characters": 40000,
        },
        "database": {"retention_days": 30},
        "output": {"save_trend_report": False},
    }
    sources = {
        "ai_news": ["http://example.com/feed"],
    }

    (config_dir / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )
    (config_dir / "sources.json").write_text(
        json.dumps(sources), encoding="utf-8"
    )

    return {"settings": settings, "sources": sources, "dir": config_dir}


@pytest.fixture(autouse=True)
def reset_logging():
    """
    Nollstiller root-loggerns handlers efter varje test så att
    logger-tester inte läcker handlers in i andra tester.
    """
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
