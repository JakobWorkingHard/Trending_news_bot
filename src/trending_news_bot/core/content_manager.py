import json
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

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
                        summary=item.get("summary", ""),
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

        # 3. Bygg Call 1-prompt (summaries+titles om under char-tak, else titles-only)
        system_prompt = trend_cfg.get("system_prompt", "")
        user_prompt, mode = self._build_trend_prompt(recent_articles, system_prompt, trend_cfg)

        self.logger.info(f"Call 1-prompt byggs i läge: {mode}")

        call1_chars = len(system_prompt) + len(user_prompt)
        self.logger.info(f"Call 1 — tecken skickade till LLM: {call1_chars}")
        print(f"Call 1 — tecken skickade till LLM: {call1_chars}")

        # 4. Call 1 — trendanalys
        trend_report = self.llm.generate_response(system_prompt, user_prompt)

        # 5. Skriv ut trendrapporten i terminalen och logga den
        print("\n" + "=" * 60)
        print(f"📰 TRENDANALYS (senaste {hours} timmarna)")
        print("=" * 60)
        print(trend_report)
        print("=" * 60 + "\n")

        self.logger.info("Trendanalys (Call 1) slutförd och utskriven.")
        for line in trend_report.splitlines():
            self.logger.info(f"TREND: {line}")

        # 6. Call 2 — sammanfatta fulltext för artiklarna i varje trend
        summary_report = self._summarize_trends(
            trend_report, recent_articles, trend_cfg
        )

        final_report = trend_report
        if summary_report:
            print("\n" + "=" * 60)
            print("📝 SUMMERINGAR PER TREND")
            print("=" * 60)
            print(summary_report)
            print("=" * 60 + "\n")
            self.logger.info("Summeringar (Call 2) slutförda och utskrivna.")
            for line in summary_report.splitlines():
                self.logger.info(f"SUMMARY: {line}")
            final_report = (
                trend_report + "\n\n---\n\n## Summeringar per trend\n\n" + summary_report
            )
        else:
            self.logger.info("Inga summeringar genererades (hoppade över Call 2).")

        # 7. Spara den sammanslagna rapporten till fil om enableat
        if output_cfg.get("save_trend_report", False):
            self._save_trend_report(final_report, hours)

        self.logger.info("Pipeline slutförd!")

    # --- Hjälpmetoder för tvåstegs-trendanalysen ---

    def _build_trend_prompt(
        self, recent_articles: List[Dict], system_prompt: str, trend_cfg: Dict
    ) -> Tuple[str, str]:
        """
        Bygger Call 1 user-prompt. Använder summaries+titles-läget om den
        totala prompt-längden (system + user) är under 'max_prompt_characters';
        annars faller den tillbaka till titles-only och loggar det.

        Returnerar (user_prompt, mode) där mode är 'summaries' eller 'titles'.
        """
        max_prompt_chars = trend_cfg.get("max_prompt_characters", 12000)
        max_summary_chars = trend_cfg.get("max_summary_characters", 500)

        titles_template = trend_cfg.get(
            "articles_format_template", "{index}. {title} (Sajt: {source_site})"
        )
        summary_template = trend_cfg.get(
            "articles_format_with_summary_template",
            "{index}. {title} (Sajt: {source_site}) — {summary}",
        )
        titles_user_template = trend_cfg.get(
            "user_prompt_template", "Här är dagens nyhetsrubriker:\n{articles}"
        )
        summary_user_template = trend_cfg.get(
            "user_prompt_with_summary_template",
            "Här är dagens nyhetsrubriker med sammanfattningar:\n{articles}",
        )

        def _truncate(text: str, limit: int) -> str:
            if len(text) <= limit:
                return text
            return text[:limit].rstrip() + "…"

        # --- Summaries-läge: bygg först och kolla char-budget ---
        summary_lines = []
        for i, a in enumerate(recent_articles, start=1):
            summary = _truncate(a.get("summary", "") or "", max_summary_chars)
            line = (
                summary_template
                .replace("{index}", str(i))
                .replace("{title}", a["title"])
                .replace("{source_site}", a["source_site"])
                .replace("{summary}", summary)
            )
            summary_lines.append(line)
        summary_articles_text = "\n".join(summary_lines)
        summary_user_prompt = summary_user_template.replace(
            "{articles}", summary_articles_text
        )

        if len(system_prompt) + len(summary_user_prompt) <= max_prompt_chars:
            return summary_user_prompt, "summaries"

        # --- Titles-only fall-back ---
        self.logger.warning(
            f"Summaries-prompten överstiger max {max_prompt_chars} tecken "
            f"(blev {len(system_prompt) + len(summary_user_prompt)}). "
            f"Använder titles-only-läget istället."
        )
        title_lines = []
        for i, a in enumerate(recent_articles, start=1):
            line = (
                titles_template
                .replace("{index}", str(i))
                .replace("{title}", a["title"])
                .replace("{source_site}", a["source_site"])
            )
            title_lines.append(line)
        titles_articles_text = "\n".join(title_lines)
        titles_user_prompt = titles_user_template.replace(
            "{articles}", titles_articles_text
        )

        # Hård kap: om även titles-only överstiger char-tak, kapa artiklar
        # från slutet (bevarar de nyaste först eftersom recent_articles är
        # DESC) tills prompten får plats. Minst 1 artikel behålls alltid.
        if len(system_prompt) + len(titles_user_prompt) > max_prompt_chars:
            titles_user_prompt = self._truncate_titles_prompt(
                recent_articles,
                titles_template,
                titles_user_template,
                system_prompt,
                max_prompt_chars,
            )
        return titles_user_prompt, "titles"

    def _truncate_titles_prompt(
        self,
        recent_articles: List[Dict],
        titles_template: str,
        titles_user_template: str,
        system_prompt: str,
        max_prompt_chars: int,
    ) -> str:
        """
        Bygger en titles-only-prompt och kapar artiklar från slutet tills
        len(system)+len(user) <= max_prompt_chars. Minst 1 artikel behålls.
        """
        n = len(recent_articles)
        while n > 1:
            title_lines = []
            for i, a in enumerate(recent_articles[:n], start=1):
                line = (
                    titles_template
                    .replace("{index}", str(i))
                    .replace("{title}", a["title"])
                    .replace("{source_site}", a["source_site"])
                )
                title_lines.append(line)
            articles_text = "\n".join(title_lines)
            user_prompt = titles_user_template.replace("{articles}", articles_text)
            if len(system_prompt) + len(user_prompt) <= max_prompt_chars:
                self.logger.warning(
                    f"Titles-only-prompten kapades till {n} artiklar för att "
                    f"passera max {max_prompt_chars} tecken."
                )
                return user_prompt
            n -= 1
        # Sista utväg: skicka en endaste artikel
        a = recent_articles[0]
        single_line = (
            titles_template
            .replace("{index}", "1")
            .replace("{title}", a["title"])
            .replace("{source_site}", a["source_site"])
        )
        user_prompt = titles_user_template.replace("{articles}", single_line)
        self.logger.warning(
            f"Titles-only-prompten reducerades till 1 artikel pga char-tak "
            f"({max_prompt_chars})."
        )
        return user_prompt

    def _parse_trends(self, report: str) -> List[Dict]:
        """
        Parsar trendrapporten från Call 1 och returnerar en lista med
        {'num', 'name', 'indices': [int, ...]}.

        Kontraktet vi ber LLM:en följa:
            ### <nummer>. <trendnamn>
            Artiklar: [nummer] Titel, [nummer] Titel, ...

        Vi matchar rubriker med ^#{1,6}\\s*\\d+[.:)] och samlar [n] från
        efterföljande rader tills nästa rubrik. Index utanför 1..N ignoreras
        sen av anroparen.
        """
        trends: List[Dict] = []
        current = None
        heading_re = re.compile(r"^#{1,6}\s*(\d+)[.:)]\s*(.+?)\s*$")
        index_re = re.compile(r"\[(\d+)\]")

        for raw_line in report.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m = heading_re.match(line)
            if m:
                if current is not None:
                    trends.append(current)
                current = {
                    "num": int(m.group(1)),
                    "name": m.group(2).strip(),
                    "indices": [],
                }
                continue
            if current is not None:
                for idx in index_re.findall(line):
                    try:
                        n = int(idx)
                    except ValueError:
                        continue
                    if n not in current["indices"]:
                        current["indices"].append(n)
        if current is not None:
            trends.append(current)
        # Släng trender utan artiklar
        return [t for t in trends if t["indices"]]

    def _select_trend(self, trends: List[Dict]) -> Optional[Dict]:
        """
        Låter användaren välja vilken trend som ska sammanfattas i Call 2.
        Returnerar vald trend (dict) eller None vid ogiltigt val/avbrott.
        """
        if not trends:
            return None
        if len(trends) == 1:
            return trends[0]

        print("\n" + "=" * 60)
        print("📋 VÄLJ TREND ATT SAMMANFATTA (Call 2)")
        print("=" * 60)
        for i, t in enumerate(trends, start=1):
            print(f"  {i}. Trend {t['num']}: {t['name']} ({len(t['indices'])} artiklar)")
        print("=" * 60)

        while True:
            choice = input(
                f"Ange nummer (1-{len(trends)}) och tryck Enter, "
                f"eller 'q' för att hoppa över Call 2: "
            ).strip()
            if choice.lower() == "q":
                return None
            try:
                idx = int(choice)
            except ValueError:
                print(f"Ogiltig inmatning: '{choice}'. Försök igen.")
                continue
            if 1 <= idx <= len(trends):
                return trends[idx - 1]
            print(
                f"Nummer utanför intervall 1-{len(trends)}. Försök igen."
            )

    def _summarize_trends(
        self, trend_report: str, recent_articles: List[Dict], trend_cfg: Dict
    ) -> str:
        """
        Kör Call 2: hämta fulltext för artiklarna i varje trend och låt LLM:en
        sammanfatta. Returnerar en sträng med summeringar (eller "" om inget
        anrop gjordes / det misslyckades).
        """
        # Hoppa om Call 1 redan misslyckades (felsträng från LLMClient)
        if not trend_report or trend_report.startswith("Fel vid generering:"):
            self.logger.warning("Call 1 misslyckades — hoppar över Call 2.")
            return ""

        trends = self._parse_trends(trend_report)
        if not trends:
            self.logger.warning(
                "Kunde inte identifiera några trender med artiklar i Call 1-svaret "
                "— hoppar över Call 2."
            )
            return ""

        # Låt användaren välja vilken trend som ska sammanfattas i Call 2
        selected_trend = self._select_trend(trends)
        if selected_trend is None:
            self.logger.info("Ingen trend vald — hoppar över Call 2.")
            return ""
        if len(trends) > 1:
            skipped_trends = [t["num"] for t in trends if t is not selected_trend]
            self.logger.info(
                f"Skickar endast trend {selected_trend['num']} till Call 2. "
                f"Skippar trender {skipped_trends}."
            )
        trends = [selected_trend]

        # Mappa 1-baserade index → url (index utanför intervall varnas och skippar)
        candidate_urls: List[str] = []
        url_by_index: Dict[int, str] = {}
        for t in trends:
            for idx in t["indices"]:
                if 1 <= idx <= len(recent_articles):
                    url = recent_articles[idx - 1]["url"]
                    url_by_index[idx] = url
                    if url not in candidate_urls:
                        candidate_urls.append(url)
                else:
                    self.logger.warning(
                        f"Index [{idx}] i trend {t['num']} saknar matchande artikel "
                        f"(1..{len(recent_articles)}) — ignoreras."
                    )

        if not candidate_urls:
            self.logger.warning("Inga giltiga artikel-URL:er att sammanfatta — hoppar över Call 2.")
            return ""

        fulltext_rows = self.db.get_articles_by_urls(candidate_urls)
        fulltext_map = {row["url"]: row for row in fulltext_rows}

        summary_user_prompt, included, skipped = self._build_summary_prompt(
            trends, url_by_index, fulltext_map, trend_cfg
        )

        if included == 0:
            self.logger.warning(
                "Inga artiklar kunde inkluderas i Call 2 (saknad fulltext eller "
                "char-budget sprängd) — hoppar över Call 2."
            )
            return ""

        self.logger.info(
            f"Call 2-prompt: {included} artiklar inkluderade, {skipped} skipparade."
        )
        summary_system_prompt = trend_cfg.get("summary_system_prompt", "")
        call2_chars = len(summary_system_prompt) + len(summary_user_prompt)
        self.logger.info(f"Call 2 — tecken skickade till LLM: {call2_chars}")
        print(f"Call 2 — tecken skickade till LLM: {call2_chars}")
        summary_report = self.llm.generate_response(
            summary_system_prompt, summary_user_prompt
        )

        if summary_report.startswith("Fel vid generering:"):
            self.logger.warning(f"Call 2 misslyckades: {summary_report}")
            return ""
        return summary_report

    def _build_summary_prompt(
        self,
        trends: List[Dict],
        url_by_index: Dict[int, str],
        fulltext_map: Dict[str, Dict],
        trend_cfg: Dict,
    ) -> Tuple[str, int, int]:
        """
        Bygger Call 2 user-prompt enligt:
            ### Trend <num>: <name>
            Artikel [<idx>]: <title>
            <trunkerad fulltext>
            ...

        Greedy fill i trend-ordning; avbryter när 'max_summary_prompt_characters'
        överskrids. Returnerar (user_prompt, included_count, skipped_count).
        """
        max_total = trend_cfg.get("max_summary_prompt_characters", 40000)
        article_template = trend_cfg.get(
            "summary_article_format_template", "Artikel [{index}]: {title}\n{content}"
        )

        blocks: List[str] = []
        total_len = 0
        included = 0
        skipped = 0
        budget_full = False

        for trend in trends:
            if budget_full:
                break
            header = f"### Trend {trend['num']}: {trend['name']}"
            # Buffra artikelblock för trenden; lägg bara till headern om minst
            # en artikel får plats (annars slipper vi en tom trend-rubrik i prompten).
            trend_blocks: List[str] = []
            trend_len = len(header) + 2  # +2 för "\n\n"-separatorn

            for idx in trend["indices"]:
                url = url_by_index.get(idx)
                if url is None:
                    continue
                row = fulltext_map.get(url)
                if not row or not row.get("content"):
                    self.logger.warning(
                        f"Saknar fulltext för artikel [{idx}] ({url}) — skippar i Call 2."
                    )
                    skipped += 1
                    continue
                content = row["content"]
                block = (
                    article_template
                    .replace("{index}", str(idx))
                    .replace("{title}", row["title"])
                    .replace("{content}", content)
                )
                block_len = len(block) + 1  # +1 för separatorn
                if total_len + trend_len + block_len > max_total:
                    budget_full = True
                    skipped += 1
                    self.logger.warning(
                        f"Call 2 char-budget ({max_total}) nådd vid artikel [{idx}] — "
                        f"resten av trenden/trenderna skipparade."
                    )
                    break
                trend_blocks.append(block)
                trend_len += block_len
                included += 1

            if trend_blocks:
                blocks.append(header)
                blocks.extend(trend_blocks)
                total_len += trend_len

        return "\n\n".join(blocks), included, skipped

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
