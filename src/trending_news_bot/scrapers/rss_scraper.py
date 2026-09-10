import feedparser
import requests
import trafilatura
from configparser import ConfigParser
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Dict
from .base_scraper import BaseScraper

# Vissa sajter (t.ex. VentureBeat) blockerar requests utan en "riktig"
# User-Agent. feedparser och trafilatura använder annars standard-agenter
# som ofta får 429 Too Many Requests.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Särskild User-Agent för RSS-feeds. Många sajter (t.ex. kdnuggets,
# artificialintelligence-news) har sträng bot-detection som blockerar
# webbläsar-spoofs OCH feedparsers standard-agent. De vitlistar dock
# feed-readers som Feedly/Inoreader (för att feeden ska syndikeras korrekt).
_FEED_USER_AGENT = "Feedly/1.0 (+https://feedly.com)"

# Browser-headers som används vid artikelnedladdning. Många sajter
# bot-blockerar om man bara skickar User-Agent utan tillhörande headers
# som en riktig webbläsare skulle skicka.
_HTTP_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
}

# Headers specifikt för feed-hämtning. Använder Feedly-User-Agent.
_FEED_HEADERS = {
    "User-Agent": _FEED_USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
}

# Sökväg till trafilaturas medföljande settings.cfg, så vi kan seeda en
# egen ConfigParser med alla defaults och sedan bara åsidosätta timeout/retries.
_TRAFILATURA_CFG_PATH = Path(trafilatura.__file__).parent / "settings.cfg"

# Förvalda gränser om anroparen inte anger något. Håller nedladdningstiden
# per artikel nere så att långsamma sajter inte blockerar hela pipelinen.
_DEFAULT_DOWNLOAD_TIMEOUT = 10
_DEFAULT_MAX_RETRIES = 1
_DEFAULT_FEED_TIMEOUT = 15


def _build_trafilatura_config(
    download_timeout: int, max_retries: int
) -> ConfigParser:
    """
    Skapar en ConfigParser seedad från trafilaturas settings.cfg där
    DOWNLOAD_TIMEOUT och MAX_REDIRECTS (som trafilatura använder som retry-
    gräns) har åsidosatts med våra egna värden. Sätter också USER_AGENTS så
    trafilatura inte avslöjar sig själv som bot (vilket vissa sajter blockerar).
    """
    config = ConfigParser()
    if _TRAFILATURA_CFG_PATH.is_file():
        config.read(_TRAFILATURA_CFG_PATH)
    # DEFAULT-sektionen finns alltid implicit i ConfigParser, så vi kan
    # sätta värden direkt utan att först skapa sektionen.
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(download_timeout))
    config.set("DEFAULT", "MAX_REDIRECTS", str(max_retries))
    # trafilatura läser USER_AGENTS rad för rad; en enda rad räcker.
    config.set("DEFAULT", "USER_AGENTS", _USER_AGENT)
    return config


def _fetch_feed_content(url: str, timeout: int = _DEFAULT_FEED_TIMEOUT) -> str:
    """
    Hämtar ett RSS/Atom-flöde som råtext med requests istället för att låta
    feedparser hämta det själv. Detta ger oss kontroll över:

    - User-Agent (feedparser avslöjar sig själv, vissa sajter blockerar).
    - Encoding (vi tvingar UTF-8 baserat på XML-deklarationen och ignorerar
      HTTP-headerns charset, vilket fixar feeds som deklareras us-ascii men
      innehåller UTF-8 – t.ex. venturebeat och deepmind.google).
    - Leading whitespace/BOM (kan orsaka "junk after document element").

    Returnerar feeden som avkodad sträng, eller tom sträng vid fel.
    """
    response = requests.get(
        url, headers=_FEED_HEADERS, timeout=timeout
    )
    response.raise_for_status()
    content = response.content
    # Rensa BOM och leading whitespace som vissa feeds inleds med och som
    # får feedparser att kasta "junk after document element".
    text = content.lstrip()
    # Försök följa XML-deklarationens encoding om angiven; fall tillbaka på
    # UTF-8 (och ignorer därmed HTTP-headerns charset=us-ascii).
    try:
        return text.decode("utf-8")
    except UnicodeDecodeError:
        return text.decode("utf-8", errors="replace")


def extract_source_site(url: str) -> str:
    """
    Extraherar domännamnet från en URL, utan 'www.'.
    Exempel: 'https://www.techcrunch.com/artikel' -> 'techcrunch.com'
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc

class RSSScraper(BaseScraper):
    """
    En universell skrapa för alla sajter som erbjuder RSS-feeds.
    """
    
    def __init__(
        self,
        feed_url: str,
        download_timeout: int = _DEFAULT_DOWNLOAD_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        feed_timeout: int = _DEFAULT_FEED_TIMEOUT,
    ):
        super().__init__(feed_url)
        self._download_config = _build_trafilatura_config(
            download_timeout, max_retries
        )
        self._feed_timeout = feed_timeout

    def fetch_headlines(self, limit: int = 10) -> List[Dict[str, str]]:
        self.logger.info(f"Hämtar RSS-feed från {self.base_url}")

        # Hämta feeden med requests så vi kan styra User-Agent och encoding,
        # sen låt feedparser parsaa den redan hämtade strängen. Detta kringgår
        # problem där feedparser annars får fel charset eller blockeras av
        # sajter som inte gillar feedparsers User-Agent.
        try:
            feed_content = _fetch_feed_content(
                self.base_url, timeout=self._feed_timeout
            )
        except Exception as e:
            self.logger.warning(
                f"Kunde inte hämta feeden från {self.base_url}. Fel: {e}"
            )
            return []

        if not feed_content:
            self.logger.warning(
                f"Tomt innehåll från feeden: {self.base_url}"
            )
            return []

        feed = feedparser.parse(feed_content)

        if feed.bozo:
            self.logger.warning(
                f"Kunde inte parsa feeden korrekt från {self.base_url}. "
                f"Fel: {feed.bozo_exception}"
            )
            return []

        headlines = []
        for entry in feed.entries[:limit]:
            summary = entry.get('summary', '')
            
            headlines.append({
                "title": entry.title,
                "url": entry.link,
                "summary": summary 
            })
            
        # Räkna hur många nyheter vi hittade i feeden
        num_headlines = len(headlines)
        self.logger.info(f"Hittade {num_headlines} nyheter i feeden.")
        return headlines

    def fetch_article_content(self, article_url: str) -> str:
        """
        Laddar ner hela artikelsidan och extraherar enbart brödtexten.
        """
        self.logger.info(f"Laddar ner fulltext från: {article_url}")
        
        # Ladda ner HTML-koden för sidan. Vi skickar in en egen ConfigParser
        # så att trafilatura använder kort timeout och få retries, annars kan
        # en enda hängande sida blockera pipelinen i ~120s.
        downloaded = trafilatura.fetch_url(article_url, config=self._download_config)
        
        if downloaded is None:
            self.logger.warning(f"Misslyckades att ladda ner sidan: {article_url}")
            return ""
            
        # Extrahera den faktiska texten (tar bort menyer, sidfötter etc.)
        text = trafilatura.extract(downloaded)
        
        if text is None:
            self.logger.warning(f"Kunde inte hitta någon artikeltext på: {article_url}")
            return ""
            
        # Räkna hur många tecken den extraherade texten består av
        num_chars = len(text)
        self.logger.debug(f"Extraherade {num_chars} tecken från artikeln.")
        return text