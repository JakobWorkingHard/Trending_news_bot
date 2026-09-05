# Trending News Bot

Ett OOP-strukturerat verktyg för att skrapa nyheter via RSS-flöden, spara dem i en
SQLite-databas och identifiera trender med hjälp av en LLM. Pipelinen hämtar
rubriker från konfigurerade källor, deduplicerar, extraherar fulltext med
`trafilatura`, och skickar de senaste artiklarna till en OpenAI-kompatibel modell
för trendanalys.

## Innehåll

- [Översikt](#översikt)
- [Hur pipelinen fungerar](#hur-pipelinen-fungerar)
- [Arkitektur](#arkitektur)
- [Krav](#krav)
- [Installation](#installation)
- [Användning](#användning)
- [Konfiguration](#konfiguration)
- [Testning](#testning)
- [Loggning](#loggning)
- [Projektstruktur](#projektstruktur)

## Översikt

Trending News Bot kör en enda pipeline som:

1. Läser RSS-källor från `config/sources.json` (grupperade per kategori).
2. Skrapar varje feed med `feedparser` och begränsar antal artiklar per feed
   (`scraping.limit_per_feed` i `config/settings.json`).
3. Släpper dubbletter via URL-kontroll mot SQLite-databasen.
4. Hämtar fulltext med `trafilatura`; faller tillbaka på RSS-summary om det
   misslyckas.
5. Sparar nya artiklar i `data/articles.db` tillsammans med `source_site`
   (extraherat från URL:en).
6. Raderar artiklar äldre än `database.retention_days`.
7. Hämtar alla artiklar skrapade de senaste X timmarna och skickar rubrikerna
   till en LLM med en system-prompt och user-prompt från `settings.json`.
8. Skriver ut trendrapporten i terminalen och loggar den i `logs/pipeline.log`.
9. Sparar rapporten till `data/trend_reports/` om `output.save_trend_report`
   är `true`.

## Hur pipelinen fungerar

```
RSS-feeds (sources.json)
        │
        ▼
   RSSScraper ── feedparser ──▶ rubriker + URL
        │
        ▼
   trafilatura ──▶ fulltext (eller RSS-summary som fallback)
        │
        ▼
   DatabaseManager ──▶ dubblettkontroll ──▶ spara i SQLite
        │
        ▼
   get_articles_since(hours) ──▶ urval de senaste X timmarna
        │
        ▼
   LLMClient (OpenAI-kompatibel) ──▶ trendanalys
        │
        ▼
   Terminal + logs/pipeline.log (+ valbar fil i data/trend_reports/)
```

## Arkitektur

Projektet är byggt i lager med tydligt separerat ansvar:

- **`scrapers/`** — datainhämtning. `BaseScraper` är en abstrakt basklass som
  tvingar subklasser att implementera `fetch_headlines` och
  `fetch_article_content`. `RSSScraper` är den konkreta implementationen.
- **`core/`** — affärslogik. `ContentManager` är dirigenten som orkestrerar
  hela pipelinen. `DatabaseManager` hanterar SQLite och dubblettkontroll.
  `setup_logging` konfigurerar global loggning.
- **`llm/`** — `LLMClient` kapslar in all kommunikation med LLM-API:et.

Alla klasser instansieras via `ContentManager`, vilket gör systemet lätt att
utöka med nya skrapor eller LLM-klienter utan att ändra pipelinen.

## Krav

- Python ≥ 3.10 (utvecklas mot 3.13.7, se `.python-version`)
- Ett API-konto hos [evroc](https://models.think.evroc.com) för att få en
  `EVROC_API_KEY`

## Installation

```bash
# 1. Skapa och aktivera en virtuell miljö
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Installera projektet + utvecklingsberoenden
pip install -e ".[dev]"

# 3. Skapa din .env-fil från mallen och fyll i API-nyckeln
cp .env.example .env
# Redigera .env och sätt EVROC_API_KEY=din-nyckel-här
```

## Användning

```bash
# Kör pipelinen med standardvärden (24 timmar, se settings.json)
python main.py

# Kör trendanalys för de senaste 6 timmarna
python main.py --hours 6
```

När pipelinen är klar skrivs trendanalysen ut i terminalen och loggas till
`logs/pipeline.log`.

## Konfiguration

### `config/settings.json`

| Nyckel | Beskrivning | Standard |
|---|---|---|
| `scraping.limit_per_feed` | Max antal artiklar som hämtas per RSS-feed | `10` |
| `scraping.download_timeout_seconds` | Timeout (sekunder) per artikelnedladdning via trafilatura | `10` |
| `scraping.max_retries` | Max antal retries/redirects vid misslyckad artikelnedladdning | `1` |
| `scraping.feed_timeout_seconds` | Timeout (sekunder) vid hämtning av själva RSS-feeden | `15` |
| `llm.model` | Modell-ID som skickas till LLM-API:et | `zai-org/GLM-5.2` |
| `llm.temperature` | Samplingstemperatur för LLM | `0.7` |
| `llm.base_url` | Slutpunkt för OpenAI-kompatibel LLM-API. Kan pekas om till valfri provider (OpenAI, lokal Ollama, Anthropic, etc.) | `https://models.think.evroc.com/v1` |
| `trend_analysis.system_prompt` | System-prompt som styr LLM:ens roll | se fil |
| `trend_analysis.user_prompt_template` | Mall för user-prompten (`{articles}` ersätts) | se fil |
| `trend_analysis.articles_format_template` | Format per artikelrad (`{title}`, `{source_site}`) | se fil |
| `trend_analysis.max_articles_for_analysis` | Tak för antal artiklar som skickas till LLM | `50` |
| `trend_analysis.min_articles_for_analysis` | Golv — färre artiklar hoppar över LLM-anropet | `3` |
| `trend_analysis.default_hours` | Standard tidspann om `--hours` inte anges | `24` |
| `database.retention_days` | Artiklar äldre än så många dagar raderas varje körning | `30` |
| `output.save_trend_report` | Om `true` sparas rapporten till `data/trend_reports/` som `.md` | `true` |

### `config/sources.json`

JSON-objekt där varje nyckel är en kategori och värdet är en lista med
RSS-URL:er. Exempel:

```json
{
  "data_science": [
    "https://towardsdatascience.com/feed",
    "https://www.kdnuggets.com/feed"
  ],
  "ai_news": [
    "https://techcrunch.com/category/artificial-intelligence/feed/"
  ]
}
```

## Testning

Testerna använder `pytest` med mockade beroenden (ingen databas, inget
nätverk, inga riktiga API-nycklar). Fixtures i `tests/conftest.py` isolerar
varje test via `tmp_path` och `monkeypatch`.

```bash
pytest
```

Testerna täcker:
- `RSSScraper` — parsning, bozo-fel, fulltext-extraktion, fallback till summary
- `DatabaseManager` — dubblettkontroll, tidsfiltrering, retention
- `LLMClient` — krav på API-nyckel, rätt anropsparametrar, felhantering
- `ContentManager` — orkestrering, avbrott vid bristande data, prompt-bygge
- `BaseScraper` — abstraktionskontraktet upprätthålls
- `setup_logging` — skapar loggfil, undviker dubbla handlers

## Loggning

`setup_logging` (i `src/trending_news_bot/core/logger.py`) konfigurerar två
handlers på rot-loggern:

- **Fil** (`logs/pipeline.log`): loggar på `DEBUG`-nivå och uppåt — allt.
- **Terminal**: loggar på `INFO`-nivå och uppåt — det viktigaste.

Format: `2026-08-28 09:45:12 - RSSScraper - INFO - Hittade 10 rubriker`.

> Logs-mappen skapas automatiskt vid körning. `logs/*.log` ignoreras av git
> (se `.gitignore`); själva mappen behöver inte sparas i versionkontroll.

## Projektstruktur

```
trending_news_bot/
├── main.py                      # CLI-entry: --hours, startar ContentManager
├── pyproject.toml               # Paketdefinition, deps, pytest-konfig
├── .env.example                 # Mall för miljövariabler
├── .gitignore
├── config/
│   ├── settings.json            # Modell, prompts, retention, etc.
│   └── sources.json             # RSS-källor per kategori
├── data/
│   ├── articles.db              # SQLite-databas (skapas vid körning)
│   └── trend_reports/           # Sparade rapporter (om enablelat)
├── logs/
│   └── pipeline.log             # Debug+INFO-logg (skapas vid körning)
├── notebooks/                   # För experiment och analys
├── src/
│   └── trending_news_bot/
│       ├── __init__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── content_manager.py   # Dirigenten — orkestrerar pipelinen
│       │   ├── database.py          # SQLite + dubblettkontroll
│       │   └── logger.py            # Global loggkonfiguration
│       ├── llm/
│       │   └── llm_client.py        # OpenAI-kompatibel LLM-klient
│       └── scrapers/
│           ├── __init__.py
│           ├── base_scraper.py      # Abstrakt basklass
│           └── rss_scraper.py       # RSS + trafilatura-fulltext
└── tests/
    ├── conftest.py              # Gemensamma fixtures (mockar allt externt)
    ├── test_base_scraper.py
    ├── test_content_manager.py
    ├── test_database.py
    ├── test_llm_client.py
    ├── test_logger.py
    └── test_rss_scraper.py
```
