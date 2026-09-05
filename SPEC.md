# SPEC.md — Teknisk specifikation för Trending News Bot

> Detta dokument är skrivet för att ge en LLM eller AI-agent full teknisk
> kontext om projektet. Det beskriver arkitektur, kontrakt, dataflöden och
> kända inkonsistenser så att en agent kan resonera om och modifiera koden
> korrekt utan att behöva läsa varje fil från grunden.

## 1. Syfte och omfång

Trending News Bot är ett OOP-strukturerat Python-verktyg som:

- Skrapar nyhetsrubriker från RSS-flöden (konfigurerade i `config/sources.json`).
- Extraherar fulltext från artiklar med `trafilatura`.
- Sparar unika artiklar i en SQLite-databas med dubblettkontroll per URL.
- Identifierar trender genom att skicka de senaste artiklarnas rubriker till en
  OpenAI-kompatibel LLM och låta den producera en svensk trendanalys.
- Loggar allting till `logs/pipeline.log` och skriver rapporten i terminalen.

Projektet riktar sig mot tech-/data science-/AI-nyheter men är
käll-agnostiskt: allt som har ett RSS-flöd kan läggas till i `sources.json`.

## 2. Teknisk stack

| Komponent | Teknik |
|---|---|
| Språk | Python ≥ 3.10 (utvecklas mot 3.13.7) |
| Paketering | `setuptools` via `pyproject.toml`, `src/`-layout |
| RSS-parsning | `feedparser` |
| Fulltext-extraktion | `trafilatura` |
| Databas | `sqlite3` (standardbibliotek), fil: `data/articles.db` |
| LLM-klient | `openai` Python-SDK, OpenAI-kompatibel endpoint |
| Miljövariabler | `python-dotenv` (`.env`) |
| Testning | `pytest` + `pytest-mock` |

## 3. Arkitektur

Systemet är lager-indelat med beroenden som pekar nedåt:

```
main.py
   │
   ▼
ContentManager (core/)         ← orkestrerar hela pipelinen
   ├── scrapers/                ← datainhämtning (RSS + fulltext)
   ├── DatabaseManager (core/)  ← persistens + dubblettkontroll
   └── LLMClient (llm/)         ← trendanalys
```

### 3.1 `main.py`
CLI-entry. Anropar `setup_logging()`, parsar `--hours` (valfri, annars från
`settings.json`), instansierar `ContentManager` och anropar
`run_pipeline(hours=args.hours)`. Fångar topp-nivåfel och skriver ut dem.

### 3.2 `scrapers/base_scraper.py` — `BaseScraper`
Abstrakt basklass (ärver `ABC`). Tvingar subklasser att implementera:
- `fetch_headlines() -> List[Dict[str, str]]` — lista med `{"title", "url"}`.
- `fetch_article_content(article_url) -> str` — brödtexten.

Tillhandahåller `log_status(msg)` som anropar `logger.debug`. Varje subklass
får en egen logger via `logging.getLogger(self.__class__.__name__)`.

### 3.3 `scrapers/rss_scraper.py` — `RSSScraper`
Konkret subklass av `BaseScraper`. Två ansvarsområden:

1. `fetch_headlines(limit=10)`: parsar RSS via `feedparser.parse(self.base_url)`.
   Kontrollerar `feed.bozo` — om `True` returneras `[]` med varning. Annars
   returneras de `limit` första entries som dicts med `title`, `url`, `summary`.

2. `fetch_article_content(article_url)`: hämtar HTML via
   `trafilatura.fetch_url`, extraherar text via `trafilatura.extract`.
   Returnerar `""` vid misslyckande (nerladdning eller extraktion).

Modulen exporterar även `extract_source_site(url)` som parsar domännamnet och
strippar `www.`.

### 3.4 `core/database.py` — `DatabaseManager`
Hanterar SQLite-databasen `data/articles.db`. Tabellens schema:

```sql
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    source_site TEXT NOT NULL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Metoder:
- `_create_tables()` — idempotent, körs i `__init__`.
- `is_url_seen(url) -> bool` — dubblettkontroll.
- `save_article(url, title, content, source_site)` — INSERT; `UNIQUE` på URL
  fångar dubbletter via `sqlite3.IntegrityError` (loggar varning, kastar ej).
- `get_articles_since(hours) -> List[Dict]` — hämtar artiklar nyare än
  `now - hours` (UTC), sorterade fallande, returnerar `title` + `source_site`.
- `delete_older_than(days) -> int` — raderar artiklar äldre än `days` dagar
  (UTC). Säkerhetsventil: om `days` är `None` eller `<= 0` görs inget.

Alla metoder loggar fel och returnerar säkra fallbacks (`[]`, `False`, `0`)
istället för att propagera undantag, förutom `save_article` som tystar
`IntegrityError`.

### 3.5 `llm/llm_client.py` — `LLMClient`
Kapslar in LLM-API:et. Använder `openai`-SDK men pekar mot en
OpenAI-kompatibel endpoint:

- `base_url = "https://models.think.evroc.com/v1"`
- API-nyckel läses från miljövariabeln `LLM_API_KEY` (via `load_dotenv()`).
- Saknas nyckeln kastas `ValueError("LLM_API_KEY saknas.")` — pipeline
  avbryts hårt vid initiering.

`generate_response(system_prompt, user_prompt) -> str` anropar
`client.chat.completions.create(model, messages=[system, user], temperature)`.
Vid API-fel fångas undantaget och returnerar strängen
`"Fel vid generering: {e}"` istället för att krascha pipelinen.

### 3.6 `core/content_manager.py` — `ContentManager`
**Dirigenten.** Orkestrerar hela pipelinen. Instansierar `DatabaseManager`
och (med config från `settings.json`) `LLMClient` i `__init__`.

`run_pipeline(hours=None)` gör i tur och ordning:
1. Läser `config/sources.json` (tom → avbryt tyst).
2. Läser `config/settings.json` (saknas/ogiltig → standardvärden).
3. Bestämmer `hours`: explicit argument > `default_hours` från config (24).
4. Raderar gamla artiklar: `db.delete_older_than(retention_days)`.
5. För varje kategori och URL: skapa `RSSScraper`, hämta headlines, och för
   varje artikel:
   - Hoppa om `is_url_seen(url)`.
   - Hämta fulltext; fallback till RSS-summary.
   - Spara med `extract_source_site(url)` som `source_site`.
6. Hämta `recent_articles = db.get_articles_since(hours)`.
7. Om färre än `min_articles_for_analysis` → avbryt (sparar LLM-kostnader).
8. Om fler än `max_articles_for_analysis` → kapa till de nyaste.
9. Bygg `user_prompt` från `user_prompt_template` och
   `articles_format_template` (ersättningar: `{title}`, `{source_site}`,
   `{articles}`).
10. Hämta `system_prompt` från config.
11. `trend_report = llm.generate_response(system_prompt, user_prompt)`.
12. Skriv ut i terminalen med ramverk; logga varje rad på `INFO`.
13. Om `output.save_trend_report` är `true` → spara till
    `data/trend_reports/trend_<UTC-timestamp>.txt` via `_save_trend_report`.

### 3.7 `core/logger.py` — `setup_logging()`
Konfigurerar rot-loggern med två handlers (filtillämpas en gång via guard):

- `FileHandler("logs/pipeline.log")` på `DEBUG`.
- `StreamHandler` på `INFO`.

Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`.

## 4. Konfigurationsmodell

### 4.1 `config/settings.json`
Hela pipelinebeteendet styrs här. Se även README.md för tabell med standard.

Nycklar:
- `scraping.limit_per_feed` (int)
- `llm.model` (str) — default `zai-org/GLM-5.2`
- `llm.temperature` (float) — default `0.7`
- `trend_analysis.system_prompt` (str) — instruerar LLM:en som nyhetsanalyst.
- `trend_analysis.user_prompt_template` (str) — innehåller `{articles}`.
- `trend_analysis.articles_format_template` (str) — `{title}`, `{source_site}`.
- `trend_analysis.max_articles_for_analysis` (int) — tak för token-kostnad.
- `trend_analysis.min_articles_for_analysis` (int) — golv för att köra LLM.
- `trend_analysis.default_hours` (int) — om `--hours` ej anges.
- `database.retention_days` (int) — raderingsålder.
- `output.save_trend_report` (bool) — spara rapport till fil?

### 4.2 `config/sources.json`
Dict `{kategori: [RSS-URL:er]}`. Läsas av `load_sources()` i `ContentManager`.

## 5. Externa kontrakt

### 5.1 LLM-API (OpenAI-kompatibel)
- Endpoint: `POST https://models.think.evroc.com/v1/chat/completions`
- Auth: `Authorization: Bearer $LLM_API_KEY`
- Body: `{model, messages: [{role, content}], temperature}`
- Förväntar svar: `{choices: [{message: {content: str}}]}`
- Standardmodell: `zai-org/GLM-5.2`

### 5.2 RSS-källor
Måste vara giltiga RSS/Atom-flöden parsbara av `feedparser`. Bozo-flaggan
respekteras ( Trasig feed → tom lista, ej krasch).

## 6. Pipeline-flöde (pseudokod)

```
function run_pipeline(hours):
    sources = load_sources()                  # → Dict[kategori, [url]]
    settings = load_settings()                # → Dict
    hours = hours or settings.trend.default_hours  # default 24
    db.delete_older_than(settings.database.retention_days)

    for kategori, urls in sources:
        for url in urls:
            scraper = RSSScraper(url)
            for item in scraper.fetch_headlines(limit=settings.scraping.limit_per_feed):
                if db.is_url_seen(item.url): continue
                content = scraper.fetch_article_content(item.url) or item.summary
                db.save_article(item.url, item.title, content, extract_source_site(item.url))

    recent = db.get_articles_since(hours)
    if len(recent) < settings.trend.min_articles_for_analysis:
        return                                 # spara LLM-kostnader
    if len(recent) > settings.trend.max_articles_for_analysis:
        recent = recent[:max_articles_for_analysis]

    user_prompt = build_from_template(recent)
    system_prompt = settings.trend.system_prompt
    report = llm.generate_response(system_prompt, user_prompt)

    print(report)
    log_every_line(report)
    if settings.output.save_trend_report:
        _save_trend_report(report, hours)
```

## 7. Felhantering och robusthet

| Scenario | Beteende |
|---|---|
| `sources.json` saknas | `load_sources()` returnerar `{}`, pipeline avbryter tyst |
| `settings.json` saknas | `load_settings()` returnerar `{}`, standardvärden används |
| `settings.json` ogiltig JSON | `load_settings()` fångar, returnerar `{}` |
| `sources.json` ogiltig JSON | **`load_sources()` kraschar** (saknar try/except — känd inkonsistens) |
| Dubblett-URL vid sparning | `IntegrityError` tystas, loggas som varning |
| RSS-feed bozo | Returnerar `[]`, loggas som varning |
| `trafilatura.fetch_url` → None | Fulltext = `""`, fallback till RSS-summary |
| API-nyckel saknas | `ValueError` vid `LLMClient`-initiering — pipeline avbryts |
| API-anrop kastar | `generate_response` returnerar `"Fel vid generering: ..."` |
| För få artiklar | Pipeline avbryter innan LLM-anrop (sparar pengar) |
| För många artiklar | Kapa till `max_articles_for_analysis` (förhindrar token-sprängning) |
| `retention_days` ≤ 0 eller None | Inget raderas (säkerhetsventil) |

## 8. Kända inkonsistenser och skulder

> Viktigt för en LLM som ska modifiera koden: följande är dokumenterade i
> testerna och bör respekteras eller uttryckligen åtgärdas.

1. **`load_sources()` saknar try/except.** Till skillnad från `load_settings()`
   kastar `load_sources()` `json.JSONDecodeError` vid ogiltig JSON.
   Testet `test_load_sources_malformed_raises` dokumenterar detta.

2. **`setup_logging()`-guard kan tysta loggning.** Om något annat (pytest,
   ett bibliotek) redan lagt en handler på rot-loggern blir `setup_logging()`
   en no-op på grund av `if not root_logger.handlers:`. I produktion kan det
   göra att loggning tystnar. Testet
   `test_setup_logging_skipped_when_handlers_exist` dokumenterar beteendet.

3. **DB-sökvägar är relativa.** `DatabaseManager`, `ContentManager` och
   `setup_logging` använder relativa sökvägar (`data/`, `logs/`, `config/`).
   Arbetskatalogen vid körning påverkar var filer hamnar. Testerna kringår
   detta via `monkeypatch.chdir(tmp_path)`.

4. **`scraped_at` används utan tidszon i SQLite.** Lagras via
   `CURRENT_TIMESTAMP` (UTC) men filtreringen använder UTC-strängar. Fungerar
   så länge alla tider är UTC, men ingen explicit tidszonmarkör finns i DB.

## 9. Teststrategi

Testerna är designade för att ALDRIG röra nätverk, riktig databas eller
riktiga API-nycklar. Allt externt mockas.

### 9.1 Fixtures (`tests/conftest.py`)
- `tmp_db` — `DatabaseManager` i `tmp_path` (isolerad SQLite).
- `isolated_env` — rensar `LLM_API_KEY` ur miljön.
- `mock_openai_client` — patchar `OpenAI` i `llm_client`-modulen.
- `llm_with_key` — sätter fejkad nyckel och patchar `load_dotenv`.
- `tmp_config` — skapar fejkade `config/`-filer i `tmp_path`.
- `reset_logging` (autouse) — rensar handlers efter varje test.

### 9.2 Täckning
- `test_rss_scraper.py` — parsning, bozo, fulltext, fallback, summary-saknas.
- `test_database.py` — dedup, tidsfiltrering, retention, no-op-fall.
- `test_llm_client.py` — nyckelkrav, anropsparametrar, felhantering.
- `test_content_manager.py` — avbrott, kapning, prompt-bygge, sparning.
- `test_base_scraper.py` — abstraktionskontrakt.
- `test_logger.py` — skapar fil, inga dubbla handlers, guard-beteende.

Kör med: `pytest` (konfigurerat i `pyproject.toml`:
`pythonpath = ["src"]`, `testpaths = ["tests"]`, `addopts = "-v --tb=short"`).

## 10. Beroenden

### Runtime (från `pyproject.toml`)
- `requests`
- `feedparser`
- `openai`
- `python-dotenv`
- `trafilatura`

### Dev (`[project.optional-dependencies].dev`)
- `pytest`
- `pytest-mock`

## 11. Miljövariabler

| Variabel | Syfte | Var den används |
|---|---|---|
| `LLM_API_KEY` | API-nyckel för LLM-endpoint | `llm/llm_client.py` (via `load_dotenv`) |

Se `.env.example` för mall.

## 12. Köra projektet

```bash
# Installation
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
cp .env.example .env           # Fyll i LLM_API_KEY

# Kör
python main.py                 # default 24h
python main.py --hours 6       # senaste 6h

# Testa
pytest
```
