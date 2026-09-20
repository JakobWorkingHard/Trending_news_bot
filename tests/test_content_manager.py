"""
Tester for ContentManager (core/content_manager.py).

ContentManager är dirigenten som knyter ihop skrapning, databas och LLM.
Dessa tester verifierar orchestreringen — inte externa beroenden.
Allt nätverk och all disk/API är mockat.

Vad testas:
 - load_sources / load_settings: fil-läsning + robusthet vid saknad/ogiltig fil
 - run_pipeline: avbryter tyst när data saknas
 - run_pipeline: hoppar över LLM om färre än min_articles (sparar API-kostnader)
 - run_pipeline: kapar till max_articles (förhindrar token-sprängning)
 - run_pipeline: använder default_hours från settings när hours=None
 - run_pipeline: bygger prompts med rätt templates och skickar till LLM
 - _save_trend_report: skapar fil med rätt innehåll om enabledat
"""
import json
from unittest.mock import MagicMock

import pytest

from trending_news_bot.core.content_manager import ContentManager


# --- Fixtur för mockade beroenden ---


@pytest.fixture
def mocked_deps(monkeypatch):
    """
    Patchar DatabaseManager, LLMClient och RSSScraper i content_manager-modulen
    så att ContentManager kan instansieras utan nätverk, databas eller API-nyckel.
    Returnerar en dict med mock-klasserna för bekväm konfiguration i testerna.
    """
    mock_db_cls = MagicMock()
    mock_llm_cls = MagicMock()
    mock_scraper_cls = MagicMock()
    monkeypatch.setattr(
        "trending_news_bot.core.content_manager.DatabaseManager", mock_db_cls
    )
    monkeypatch.setattr(
        "trending_news_bot.core.content_manager.LLMClient", mock_llm_cls
    )
    monkeypatch.setattr(
        "trending_news_bot.core.content_manager.RSSScraper", mock_scraper_cls
    )
    return {"db": mock_db_cls, "llm": mock_llm_cls, "scraper": mock_scraper_cls}


# --- load_sources ---


def test_load_sources_valid(tmp_config, mocked_deps):
    # Giltig sources.json → rätt dict returneras
    manager = ContentManager()
    sources = manager.load_sources()
    assert sources == tmp_config["sources"]


def test_load_sources_missing(tmp_path, mocked_deps, monkeypatch):
    # Ingen config/-mapp → tom dict (inte krasch)
    monkeypatch.chdir(tmp_path)
    manager = ContentManager()
    assert manager.load_sources() == {}


def test_load_sources_malformed_raises(tmp_path, mocked_deps, monkeypatch):
    # OGILTIG JSON i sources.json. NOTERA: load_sources har ingen try/except
    # (till skillnad från load_settings) så detta dokumenterar nuvarande
    # beteende — funktionen kraschar. Detta är en känd inkonsekvens som
    # testet synliggör; vill du ha graceful fallback får du lägga try/except.
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources.json").write_text("{ inte giltig json", encoding="utf-8")

    manager = ContentManager()
    with pytest.raises(json.JSONDecodeError):
        manager.load_sources()


# --- load_settings ---


def test_load_settings_valid(tmp_config, mocked_deps):
    manager = ContentManager()
    settings = manager.load_settings()
    assert settings == tmp_config["settings"]


def test_load_settings_missing(tmp_path, mocked_deps, monkeypatch):
    # Ingen settings.json → tom dict med varning (inte krasch)
    monkeypatch.chdir(tmp_path)
    manager = ContentManager()
    assert manager.load_settings() == {}


def test_load_settings_malformed_returns_empty(tmp_path, mocked_deps, monkeypatch):
    # Ogiltig JSON i settings.json → tom dict (load_settings HAR try/except)
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{ ogiltig", encoding="utf-8")

    manager = ContentManager()
    assert manager.load_settings() == {}


# --- run_pipeline: avbrytande fall ---


def test_run_pipeline_no_sources_returns(tmp_path, mocked_deps, monkeypatch):
    # Inga sources → pipeline avbryter tyst utan att anropa LLM
    monkeypatch.chdir(tmp_path)
    manager = ContentManager()
    manager.run_pipeline()
    manager.llm.generate_response.assert_not_called()


def test_run_pipeline_no_recent_articles_returns(tmp_config, mocked_deps):
    # sources finns men db har inga artiklar → avbryt innan LLM
    manager = ContentManager()
    manager.db.get_articles_since.return_value = []
    manager.run_pipeline()
    manager.llm.generate_response.assert_not_called()


def test_run_pipeline_below_min_articles_skips_llm(tmp_config, mocked_deps):
    # Färre än min_articles_for_analysis (3) → avbryt, inget LLM-anrop
    manager = ContentManager()
    manager.db.get_articles_since.return_value = [
        {"title": "A", "source_site": "a.com"},
        {"title": "B", "source_site": "b.com"},
    ]
    manager.run_pipeline()
    manager.llm.generate_response.assert_not_called()


# --- run_pipeline: kapning och parametrar ---


def test_run_pipeline_caps_to_max_articles(tmp_config, mocked_deps):
    # Fler än max_articles_for_analysis (50) → kapar till exakt 50 i prompten
    manager = ContentManager()

    # Skapa en tom lista med testartiklar
    articles = []

    # Bygg en artikel per index (vi vill överstiga maxgränsen)
    for i in range(60):
        article = {"title": f"Artikel {i}", "source_site": "site.com"}
        articles.append(article)

    manager.db.get_articles_since.return_value = articles
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline()

    call_args = manager.llm.generate_response.call_args
    user_prompt = call_args.args[1]
    # Skapa en tom lista för artikelraderna i prompten
    article_lines = []

    # Dela upp prompten i rader och behåll bara artikelraderna
    for l in user_prompt.split("\n"):
        if l.startswith("- "):
            article_lines.append(l)

    assert len(article_lines) == 50


def test_run_pipeline_uses_default_hours_when_none(tmp_config, mocked_deps):
    # hours=None → ska använda default_hours=24 från settings
    manager = ContentManager()

    # Skapa en tom lista med testartiklar
    articles = []

    # Bygg en artikel per index
    for i in range(3):
        article = {"title": f"A{i}", "source_site": "site.com"}
        articles.append(article)

    manager.db.get_articles_since.return_value = articles
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline(hours=None)

    # Verifiera att get_articles_since anropats med 24 (default_hours)
    manager.db.get_articles_since.assert_called_with(24)


def test_run_pipeline_passes_correct_prompts_to_llm(tmp_config, mocked_deps):
    # Bekräfta att system_prompt och user_prompt byggs med rätt templates
    manager = ContentManager()
    articles = [
        {"title": "AI-breakthrough", "source_site": "techcrunch.com"},
        {"title": "Ny modell", "source_site": "kdnuggets.com"},
        {"title": "Pythons nya version", "source_site": "python.org"},
    ]
    manager.db.get_articles_since.return_value = articles
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline()

    call_args = manager.llm.generate_response.call_args
    system_prompt = call_args.args[0]
    user_prompt = call_args.args[1]

    # system_prompt från tmp_config
    assert system_prompt == "Test system prompt"
    # user_prompt ska använda template "Artiklar:\n{articles}"
    assert user_prompt.startswith("Artiklar:\n")
    # Varje artikel ska ha formaterats med template "- {title} ({source_site})"
    assert "- AI-breakthrough (techcrunch.com)" in user_prompt
    assert "- Ny modell (kdnuggets.com)" in user_prompt
    assert "- Pythons nya version (python.org)" in user_prompt


# --- run_pipeline: fulltext-skip ---


def test_run_pipeline_skips_articles_with_empty_fulltext(tmp_config, mocked_deps):
    # Artiklar där fulltext saknas ska inte sparas i databasen
    manager = ContentManager()

    # Konfigurera mock-scraper att returnera två headlines
    mock_scraper_instance = mocked_deps["scraper"].return_value
    mock_scraper_instance.fetch_headlines.return_value = [
        {"title": "Med text", "url": "http://example.com/1"},
        {"title": "Utan text", "url": "http://example.com/2"},
    ]
    # Första artikeln får fulltext, andra får tom sträng
    mock_scraper_instance.fetch_article_content.side_effect = [
        "Bra brödtext", ""
    ]
    # Inga dubbletter
    manager.db.is_url_seen.return_value = False

    # Se till att LLM-steget inte avbryter pipelinen för tidigt
    manager.db.get_articles_since.return_value = [
        {"title": "A", "source_site": "a.com"},
        {"title": "B", "source_site": "b.com"},
        {"title": "C", "source_site": "c.com"},
    ]
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline()

    # Bara artikeln med fulltext ska ha sparats
    assert manager.db.save_article.call_count == 1
    saved = manager.db.save_article.call_args
    assert saved.kwargs["title"] == "Med text"
    assert saved.kwargs["content"] == "Bra brödtext"


def test_run_pipeline_skips_articles_when_fetch_raises(tmp_config, mocked_deps):
    # Om fetch_article_content kastar undantag ska artikeln hoppas över
    manager = ContentManager()

    mock_scraper_instance = mocked_deps["scraper"].return_value
    mock_scraper_instance.fetch_headlines.return_value = [
        {"title": "Kraschar", "url": "http://example.com/x"},
    ]
    mock_scraper_instance.fetch_article_content.side_effect = Exception("timeout")
    manager.db.is_url_seen.return_value = False

    manager.db.get_articles_since.return_value = [
        {"title": "A", "source_site": "a.com"},
        {"title": "B", "source_site": "b.com"},
        {"title": "C", "source_site": "c.com"},
    ]
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline()

    # Inget ska ha sparats
    assert manager.db.save_article.call_count == 0


# --- _save_trend_report ---


def test_save_trend_report_writes_file(tmp_config, mocked_deps):
    # Med save_trend_report=True ska en fil skapas i data/trend_reports/
    settings = tmp_config["settings"]
    settings["output"]["save_trend_report"] = True
    (tmp_config["dir"] / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )

    manager = ContentManager()

    # Skapa en tom lista med testartiklar
    articles = []

    # Bygg en artikel per index
    for i in range(3):
        article = {"title": f"A{i}", "source_site": "site.com"}
        articles.append(article)

    manager.db.get_articles_since.return_value = articles
    manager.llm.generate_response.return_value = "Min trendrapport"

    manager.run_pipeline()

    # Hitta den skapade rapportfilen i tmp_path/data/trend_reports/
    import pathlib
    report_dir = pathlib.Path("data/trend_reports")
    files = list(report_dir.glob("trend_*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "Min trendrapport" in content
    assert "Trendanalys" in content
