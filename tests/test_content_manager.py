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
    import re
    manager = ContentManager()

    # Skapa en tom lista med testartiklar
    articles = []

    # Bygg en artikel per index (vi vill överstiga maxgränsen)
    for i in range(60):
        article = {
            "title": f"Artikel {i}",
            "source_site": "site.com",
            "summary": "s",
            "url": f"http://site.com/{i}",
        }
        articles.append(article)

    manager.db.get_articles_since.return_value = articles
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline()

    call_args = manager.llm.generate_response.call_args
    user_prompt = call_args.args[1]
    # Artikelraderna har nu formatet "N. Titel (sajt) — summary" → räkna rader
    # som börjar med siffra+punkt.
    article_lines = [l for l in user_prompt.split("\n") if re.match(r"^\d+\. ", l)]

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
    # Bekräfta att system_prompt och user_prompt byggs med rätt templates.
    # Med korta summaries och budgeten under max_prompt_characters ska
    # summaries-läget användas (första anropet = Call 1).
    manager = ContentManager()
    articles = [
        {"title": "AI-breakthrough", "source_site": "techcrunch.com", "summary": "Kort om AI", "url": "http://t.com/1"},
        {"title": "Ny modell", "source_site": "kdnuggets.com", "summary": "Ny modell släppt", "url": "http://k.com/1"},
        {"title": "Pythons nya version", "source_site": "python.org", "summary": "Python 3.13", "url": "http://p.com/1"},
    ]
    manager.db.get_articles_since.return_value = articles
    # "Rapport" har inga [nr] → Call 2 hoppas över → bara ett anrop
    manager.llm.generate_response.return_value = "Rapport"

    manager.run_pipeline()

    call_args = manager.llm.generate_response.call_args_list[0]
    system_prompt = call_args.args[0]
    user_prompt = call_args.args[1]

    # system_prompt från tmp_config
    assert system_prompt == "Test system prompt"
    # Summaries-läge: börjar med summaries-templaten
    assert user_prompt.startswith("Artiklar med sammanfattning:\n")
    # Varje artikel rad-formaterad med index, titel, sajt och summary
    assert "1. AI-breakthrough (techcrunch.com) — Kort om AI" in user_prompt
    assert "2. Ny modell (kdnuggets.com) — Ny modell släppt" in user_prompt
    assert "3. Pythons nya version (python.org) — Python 3.13" in user_prompt


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


# --- run_pipeline: tvåstegs-trendanalys (Call 1 + Call 2) ---


def _articles(n=3, prefix="A"):
    """Bygger n testartiklar med title/source_site/summary/url."""
    return [
        {
            "title": f"{prefix}{i}",
            "source_site": "site.com",
            "summary": f"summary {i}",
            "url": f"http://site.com/{prefix}{i}",
        }
        for i in range(n)
    ]


def test_run_pipeline_falls_back_to_titles_when_over_limit(tmp_config, mocked_deps):
    # När summaries-prompten överstiger max_prompt_characters ska titles-only
    # användas istället.
    settings = tmp_config["settings"]
    settings["trend_analysis"]["max_prompt_characters"] = 50  # extremt lås för att trigga
    (tmp_config["dir"] / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )

    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)
    manager.llm.generate_response.return_value = "Inga trender"

    manager.run_pipeline()

    call1 = manager.llm.generate_response.call_args_list[0]
    user_prompt = call1.args[1]
    # Titles-läge: börjar med titles-templaten och innehåller inte "—"
    assert user_prompt.startswith("Artiklar:\n")
    assert "—" not in user_prompt
    assert "1. A0 (site.com)" in user_prompt


def test_run_pipeline_makes_two_llm_calls(tmp_config, mocked_deps):
    # Med en trendrapport som listar [nr] ska både Call 1 och Call 2 göras
    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)

    trend_report = (
        "### 1. Trend A\n"
        "Artiklar: [1] A0, [2] A1\n"
        "Sajter: site.com\n"
        "Förklaring: ngt"
    )
    manager.llm.generate_response.side_effect = [trend_report, "Summering"]
    manager.db.get_articles_by_urls.return_value = [
        {"url": "http://site.com/A0", "title": "A0", "content": "text0"},
        {"url": "http://site.com/A1", "title": "A1", "content": "text1"},
    ]

    manager.run_pipeline()

    assert manager.llm.generate_response.call_count == 2
    # Call 2 system_prompt ska vara summary_system_prompt från tmp_config
    call2 = manager.llm.generate_response.call_args_list[1]
    assert call2.args[0] == "Test summary system prompt"


def test_run_pipeline_parses_trends_and_fetches_fulltext(tmp_config, mocked_deps):
    # Call 1 listar [1] och [3] → Call 2 ska hämta just dessa URL:er och
    # inkludera deras fulltext i sin prompt.
    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)

    trend_report = (
        "### 1. Trend A\n"
        "Artiklar: [1] A0, [3] A2\n"
        "Sajter: site.com\n"
        "Förklaring: ngt"
    )
    manager.llm.generate_response.side_effect = [trend_report, "Summering"]
    manager.db.get_articles_by_urls.return_value = [
        {"url": "http://site.com/A0", "title": "A0", "content": "FULLTEXT-A0"},
        {"url": "http://site.com/A2", "title": "A2", "content": "FULLTEXT-A2"},
    ]

    manager.run_pipeline()

    # Hämtade URL:er ska vara just A0 och A2 (från index 1 och 3)
    called_urls = manager.db.get_articles_by_urls.call_args.args[0]
    assert set(called_urls) == {"http://site.com/A0", "http://site.com/A2"}

    call2 = manager.llm.generate_response.call_args_list[1]
    summary_user_prompt = call2.args[1]
    assert "FULLTEXT-A0" in summary_user_prompt
    assert "FULLTEXT-A2" in summary_user_prompt
    # A1 (index 2) ingick inte i trenden → ska inte finnas med
    assert "Artikel [2]" not in summary_user_prompt


def test_run_pipeline_skips_call2_when_no_articles_parsed(tmp_config, mocked_deps):
    # Trendrapport utan [nr] → inga trender parsade → inget Call 2
    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)
    manager.llm.generate_response.return_value = "Inga tydliga trender idag."

    manager.run_pipeline()

    assert manager.llm.generate_response.call_count == 1


def test_run_pipeline_skips_call2_when_call1_failed(tmp_config, mocked_deps):
    # Om Call 1 returnerar felsträng ska Call 2 hoppas över
    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)
    manager.llm.generate_response.return_value = "Fel vid generering: API nere"

    manager.run_pipeline()

    assert manager.llm.generate_response.call_count == 1


def test_run_pipeline_call2_respects_char_budget(tmp_config, mocked_deps):
    # Med en liten total char-budget ska artiklar som inte får plats skippas.
    # Ingen per-artikel-trunkering längre — budgeten är den enda storlekskontrollen.
    settings = tmp_config["settings"]
    settings["trend_analysis"]["max_summary_prompt_characters"] = 100
    (tmp_config["dir"] / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )

    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)

    trend_report = (
        "### 1. Trend A\n"
        "Artiklar: [1] A0, [2] A1\n"
        "Sajter: site.com\n"
        "Förklaring: ngt"
    )
    manager.llm.generate_response.side_effect = [trend_report, "Summering"]
    # Två artiklar med ~40 tecken content var; budget 100 → bara en får plats
    # (trend-rubriken + en artikel överstiger inte 100, men två artiklar gör det).
    manager.db.get_articles_by_urls.return_value = [
        {"url": "http://site.com/A0", "title": "A0", "content": "C" * 40},
        {"url": "http://site.com/A1", "title": "A1", "content": "C" * 40},
    ]

    manager.run_pipeline()

    # Call 2 ska ha körts (minst en artikel fick plats)
    assert manager.llm.generate_response.call_count == 2
    call2 = manager.llm.generate_response.call_args_list[1]
    summary_user_prompt = call2.args[1]
    # Artikel [1] ska vara med; artikel [2] ska ha skipparats pga budget
    assert "Artikel [1]" in summary_user_prompt
    assert "Artikel [2]" not in summary_user_prompt


def test_run_pipeline_call1_truncates_titles_to_hard_cap(tmp_config, mocked_deps):
    # När även titles-only-prompten överstiger max_prompt_characters ska
    # artiklar kapas från slutet tills den får plats (minst 1 behålls).
    settings = tmp_config["settings"]
    # Lågt tak + lång system_prompt tvingar fram truncering redan i titles-läget
    settings["trend_analysis"]["max_prompt_characters"] = 100
    settings["trend_analysis"]["system_prompt"] = "S" * 40
    (tmp_config["dir"] / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )

    manager = ContentManager()
    # 20 artiklar med långa summaries så summaries-läget garanterat överstiger
    # taket → fall-back till titles → som också överstiger → trunkeras.
    long_articles = [
        {
            "title": f"Artikel-{i}-med-lång-titel",
            "source_site": "site.com",
            "summary": "X" * 200,
            "url": f"http://site.com/{i}",
        }
        for i in range(20)
    ]
    manager.db.get_articles_since.return_value = long_articles
    manager.llm.generate_response.return_value = "Inga trender"

    manager.run_pipeline()

    call1 = manager.llm.generate_response.call_args_list[0]
    system_prompt = call1.args[0]
    user_prompt = call1.args[1]
    total_len = len(system_prompt) + len(user_prompt)

    # Hård kap: system+user får inte överstiga max_prompt_characters
    assert total_len <= 100
    # Minst 1 artikel ska ha kommit med
    import re
    article_lines = re.findall(r"^\d+\. ", user_prompt, flags=re.MULTILINE)
    assert len(article_lines) >= 1
    # Och färre än 20 (något måste ha kapats)
    assert len(article_lines) < 20


def test_run_pipeline_skips_call2_when_fulltext_missing(tmp_config, mocked_deps):
    # Om get_articles_by_urls returnerar tomt (fulltext saknas) ska Call 2 skipparas
    manager = ContentManager()
    manager.db.get_articles_since.return_value = _articles(3)

    trend_report = (
        "### 1. Trend A\n"
        "Artiklar: [1] A0\n"
        "Sajter: site.com\n"
        "Förklaring: ngt"
    )
    manager.llm.generate_response.side_effect = [trend_report, "Summering"]
    manager.db.get_articles_by_urls.return_value = []  # ingen fulltext hittad

    manager.run_pipeline()

    # Call 1 gjordes; Call 2 skipparas (inget inkluderat)
    assert manager.llm.generate_response.call_count == 1


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
