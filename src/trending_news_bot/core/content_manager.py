import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional

from trending_news_bot.scrapers.rss_scraper import RSSScraper, extract_source_site
from trending_news_bot.core.database import DatabaseManager
from trending_news_bot.llm.llm_client import LLMClient

class ContentManager:
    """
    Dirigenten! Orkestrerar skrapning, sparande till databas och trendanalys via LLM.
    """
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.db = DatabaseManager()
        self.sources_path = Path("config/sources.json")
        self.settings_path = Path("config/settings.json")

        # Ladda settings en gång och konfigurera LLM med model/temperature
        self._settings = self.load_settings()
        llm_cfg = self._settings.get("llm", {})
        self.llm = LLMClient(
            model=llm_cfg.get("model", "zai-org/GLM-5.2"),
            temperature=llm_cfg.get("temperature", 0.7),
            base_url=llm_cfg.get("base_url", "https://models.think.evroc.com/v1"),
        )

    def load_sources(self) -> Dict[str, List[str]]:
        if not self.sources_path.exists():
            self.logger.error(f"Hittade inte {self.sources_path}!")
            return {}
        with open(self.sources_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_settings(self) -> Dict:
        if not self.settings_path.exists():
            self.logger.warning(
                f"Hittade inte {self.settings_path}. Använder standardvärden."
            )
            return {}
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Kunde inte läsa settings.json: {e}. Använder standardvärden.")
            return {}

    def run_pipeline(self, hours: Optional[int] = None):
        self.logger.info("Startar nyhetspipelinen...")
        sources = self.load_sources()
        if not sources:
            return

        settings = self._settings
        scraping_cfg = settings.get("scraping", {})
        trend_cfg = settings.get("trend_analysis", {})
        db_cfg = settings.get("database", {})
        output_cfg = settings.get("output", {})

        limit_per_feed = scraping_cfg.get("limit_per_feed", 10)
        download_timeout = scraping_cfg.get("download_timeout_seconds", 10)
        max_retries = scraping_cfg.get("max_retries", 1)
        feed_timeout = scraping_cfg.get("feed_timeout_seconds", 15)
        default_hours = trend_cfg.get("default_hours", 24)
        if hours is None:
            hours = default_hours

        # Rensa gamla artiklar enligt retention-policy
        retention_days = db_cfg.get("retention_days", 30)
        self.db.delete_older_than(retention_days)

        new_articles_count = 0
        fulltext_attempted = 0
        fulltext_success = 0

        # 1. Skrapa alla feeds och spara nya artiklar i databasen
        for category, urls in sources.items():
            self.logger.info(f"Skrapar kategori: {category}")
            for url in urls:
                scraper = RSSScraper(
                    url,
                    download_timeout=download_timeout,
                    max_retries=max_retries,
                    feed_timeout=feed_timeout,
                )
                headlines = scraper.fetch_headlines(limit=limit_per_feed)

                for item in headlines:
                    article_url = item["url"]
                    if self.db.is_url_seen(article_url):
                        # Hämta titeln separat så loggmeddelandet blir tydligt
                        duplicate_title = item["title"]
                        self.logger.debug(f"Dubblett ignorerad: {duplicate_title}")
                        continue

                    # Hämta fulltext. Artiklar där fulltext saknas sparas ej —
                    # summary används inte längre som fallback.
                    # Felisolering: en enskild artikel som time:ar ut eller kastar
                    # får inte krascha hela pipelinen.
                    fulltext_attempted += 1
                    try:
                        full_text = scraper.fetch_article_content(article_url)
                    except Exception as e:
                        self.logger.warning(
                            f"Misslyckades hämta fulltext från "
                            f"{article_url}: {e}"
                        )
                        continue

                    if not full_text:
                        self.logger.warning(
                            f"Ingen fulltext kunde extraheras från {article_url}; "
                            f"artikeln sparas ej."
                        )
                        continue

                    fulltext_success += 1
                    source_site = extract_source_site(article_url)

                    self.db.save_article(
                        url=article_url,
                        title=item["title"],
                        content=full_text,
                        source_site=source_site,
                    )
                    new_articles_count += 1

        fulltext_failed = fulltext_attempted - fulltext_success
        self.logger.info(
            f"Fulltext-hämtning: {fulltext_success}/{fulltext_attempted} "
            f"artiklar lyckades ({fulltext_failed} misslyckades)."
        )
        self.logger.info(f"Sparade {new_articles_count} nya artiklar i databasen.")

        # 2. Hämta alla artiklar från senaste X timmarna för trendanalys
        recent_articles = self.db.get_articles_since(hours)
        if not recent_articles:
            self.logger.info(f"Inga artiklar hittades inom senaste {hours} timmarna. Avslutar.")
            return

        min_articles = trend_cfg.get("min_articles_for_analysis", 3)
        max_articles = trend_cfg.get("max_articles_for_analysis", 50)

        # Räkna antalet artiklar som hämtats från databasen
        num_recent_articles = len(recent_articles)

        # Hoppa över analysen om vi har för få artiklar
        if num_recent_articles < min_articles:
            self.logger.info(
                f"Endast {num_recent_articles} artiklar (krav: {min_articles}). "
                f"Hoppar över trendanalys för att spara LLM-kostnader."
            )
            return

        # Kapa listan om vi har för många artiklar
        if num_recent_articles > max_articles:
            self.logger.warning(
                f"{num_recent_articles} artiklar överstiger max {max_articles}. "
                f"Kapar till de {max_articles} nyaste."
            )
            recent_articles = recent_articles[:max_articles]
            # Uppdatera räknaren eftersom listan nu är kortare
            num_recent_articles = len(recent_articles)

        self.logger.info(
            f"Hittade {num_recent_articles} artiklar från senaste {hours}h. "
            f"Skickar till LLM för trendanalys..."
        )

        # 3. Bygg artikeltext, user-prompt och hämta system-prompt från settings
        articles_format = trend_cfg.get(
            "articles_format_template", "- {title} (Sajt: {source_site})"
        )

        # Bygg en lista med formaterade artikelrader
        formatted_lines = []
        for a in recent_articles:
            # Byt ut platsmarkörerna mot aktuell artikels titel och källsajt
            line = articles_format.replace("{title}", a["title"])
            line = line.replace("{source_site}", a["source_site"])
            formatted_lines.append(line)

        # Slå ihop alla rader med radbrytning emellan
        articles_text = "\n".join(formatted_lines)

        user_template = trend_cfg.get(
            "user_prompt_template", "Här är dagens nyhetsrubriker:\n{articles}"
        )
        user_prompt = user_template.replace("{articles}", articles_text)

        system_prompt = trend_cfg.get("system_prompt", "")

        trend_report = self.llm.generate_response(system_prompt, user_prompt)

        # 4. Skriv ut rapporten i terminalen och logga den
        print("\n" + "=" * 60)
        print(f"📰 TRENDANALYS (senaste {hours} timmarna)")
        print("=" * 60)
        print(trend_report)
        print("=" * 60 + "\n")

        self.logger.info("Trendanalys slutförd och utskriven.")

        # Logga hela rapporten på INFO-nivå så den hamnar i pipeline.log
        for line in trend_report.splitlines():
            self.logger.info(f"TREND: {line}")

        # 5. Spara rapporten till fil om enabledat
        if output_cfg.get("save_trend_report", False):
            self._save_trend_report(trend_report, hours)

        self.logger.info("Pipeline slutförd!")

    def _save_trend_report(self, report: str, hours: int):
        """Sparar en trendrapport till data/trend_reports/ med tidsstämpel."""
        report_dir = Path("data/trend_reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        filename = report_dir / f"trend_{timestamp}.md"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# Trendanalys (senaste {hours} timmarna)\n\n")
                f.write(f"_Genererad {now.isoformat()}_\n\n")
                f.write("---\n\n")
                f.write(report)
            self.logger.info(f"Sparade trendrapport till {filename}")
        except Exception as e:
            self.logger.error(f"Kunde inte spara trendrapport: {e}")
