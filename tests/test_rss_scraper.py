from unittest.mock import patch, MagicMock

# Importera dina funktioner/klasser. (Ändra 'rss_scraper' till vad din fil heter)
from trending_news_bot.scrapers.rss_scraper import (
    RSSScraper,
    extract_source_site,
    _clean_summary,
)

# --- 1. Tester för extract_source_site ---

def test_extract_source_site():
    # Testa normal url med www
    assert extract_source_site("https://www.techcrunch.com/artikel") == "techcrunch.com"
    # Testa utan www
    assert extract_source_site("https://news.ycombinator.com/item?id=123") == "news.ycombinator.com"
    # Testa subdomän
    assert extract_source_site("http://blog.python.org/") == "blog.python.org"


def test_extract_source_site_empty():
    # En tom sträng ska inte krascha funktionen
    assert extract_source_site("") == ""


def test_extract_source_site_with_port():
    # URL med portnummer ska behålla porten i netloc
    assert extract_source_site("https://example.com:8080/path") == "example.com:8080"


# --- 2. Tester för fetch_headlines ---

@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
@patch('trending_news_bot.scrapers.rss_scraper.feedparser.parse')
def test_fetch_headlines_success(mock_parse, mock_fetch_feed):
    # _fetch_feed_content returnerar bara någon sträng; feedparser är mockad
    mock_fetch_feed.return_value = "<rss>fake</rss>"

    # Skapa en fejkad feed (mock)
    mock_feed = MagicMock()
    mock_feed.bozo = False # Ingen error

    # Skapa fejkade artiklar (med summary för att testa HTML-rensning)
    mock_entry_1 = MagicMock(
        title="Nyhet 1", link="http://länk1", summary="<p>Sammanfattning 1</p>"
    )
    mock_entry_2 = MagicMock(
        title="Nyhet 2", link="http://länk2", summary="Plain text 2"
    )
    mock_feed.entries = [mock_entry_1, mock_entry_2]

    # Säg till mocken vad den ska returnera när den anropas
    mock_parse.return_value = mock_feed

    # Kör koden
    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines(limit=1) # Vi ber bara om 1!

    # Kontrollera resultatet
    assert len(headlines) == 1
    assert headlines[0]["title"] == "Nyhet 1"
    assert headlines[0]["url"] == "http://länk1"
    # summary ska finnas och vara HTML-stripped
    assert headlines[0]["summary"] == "Sammanfattning 1"


@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
@patch('trending_news_bot.scrapers.rss_scraper.feedparser.parse')
def test_fetch_headlines_bozo_error(mock_parse, mock_fetch_feed):
    mock_fetch_feed.return_value = "<rss>fake</rss>"
    # Simulera att feeden är trasig
    mock_feed = MagicMock()
    mock_feed.bozo = True
    mock_feed.bozo_exception = Exception("Usel XML")
    mock_parse.return_value = mock_feed

    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines()

    # Ska returnera tom lista om bozo är True
    assert headlines == []


@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
@patch('trending_news_bot.scrapers.rss_scraper.feedparser.parse')
def test_fetch_headlines_limit_zero(mock_parse, mock_fetch_feed):
    mock_fetch_feed.return_value = "<rss>fake</rss>"
    # limit=0 ska ge tom lista, inte krascha
    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = [MagicMock(title="A", link="http://a")]
    mock_parse.return_value = mock_feed

    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines(limit=0)

    assert headlines == []


@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
@patch('trending_news_bot.scrapers.rss_scraper.feedparser.parse')
def test_fetch_headlines_empty_feed(mock_parse, mock_fetch_feed):
    mock_fetch_feed.return_value = "<rss>fake</rss>"
    # En feed utan entries ska ge tom lista
    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = []
    mock_parse.return_value = mock_feed

    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines()

    assert headlines == []


@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
def test_fetch_headlines_fetch_error_returns_empty(mock_fetch_feed):
    # Om _fetch_feed_content kastar (t.ex. timeout, 404) ska fetch_headlines
    # hantera det snyggt och returnera tom lista, inte krascha pipelinen.
    mock_fetch_feed.side_effect = Exception("nätverksfel")

    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines()

    assert headlines == []


@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
def test_fetch_headlines_empty_content_returns_empty(mock_fetch_feed):
    # Om _fetch_feed_content returnerar tom sträng ska fetch_headlines
    # returnera tom lista utan att anropa feedparser alls.
    mock_fetch_feed.return_value = ""

    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines()

    assert headlines == []


# --- 2b. Tester för _clean_summary och summary-saknas ---


def test_clean_summary_strips_html_and_entities():
    # HTML-taggar ska bort, entiteter avkodas, whitespace kollapsat
    raw = "<p>Hej &amp;  hopp<br/>rad<b>2</b></p>"
    assert _clean_summary(raw) == "Hej & hopprad2"


def test_clean_summary_collapses_whitespace():
    assert _clean_summary("  rad\t1\n\nrad   2 ") == "rad 1 rad 2"


def test_clean_summary_empty_for_none():
    assert _clean_summary(None) == ""


def test_clean_summary_empty_for_empty_string():
    assert _clean_summary("") == ""


def test_clean_summary_plain_text_unchanged():
    assert _clean_summary("En vanlig mening.") == "En vanlig mening."


@patch('trending_news_bot.scrapers.rss_scraper._fetch_feed_content')
@patch('trending_news_bot.scrapers.rss_scraper.feedparser.parse')
def test_fetch_headlines_summary_missing_returns_empty(mock_parse, mock_fetch_feed):
    # En entry utan summary-attribut (feedparser-liknande objekt) ska ge summary=""
    from types import SimpleNamespace
    mock_fetch_feed.return_value = "<rss>fake</rss>"
    mock_feed = MagicMock()
    mock_feed.bozo = False

    # SimpleNamespace saknar både .get och .summary — exakt som en
    # feedparser-entry som inte har med summary-fältet.
    entry = SimpleNamespace(title="Utan summary", link="http://x")
    mock_feed.entries = [entry]
    mock_parse.return_value = mock_feed

    scraper = RSSScraper("http://fake-feed.com")
    headlines = scraper.fetch_headlines(limit=5)

    assert len(headlines) == 1
    assert headlines[0]["title"] == "Utan summary"
    assert headlines[0]["summary"] == ""


# --- 3. Tester för fetch_article_content ---

@patch('trending_news_bot.scrapers.rss_scraper.trafilatura.extract')
@patch('trending_news_bot.scrapers.rss_scraper.trafilatura.fetch_url')
def test_fetch_article_content_success(mock_fetch, mock_extract):
    # Simulera lyckad hämtning och extrahering
    mock_fetch.return_value = "<html>Bra innehåll</html>"
    mock_extract.return_value = "Detta är brödtexten."

    scraper = RSSScraper("http://fake-url.com")
    text = scraper.fetch_article_content("http://fake-url.com")

    assert text == "Detta är brödtexten."
    # fetch_url anropas nu med en custom ConfigParser som andra argument
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args.args[0] == "http://fake-url.com"
    assert "config" in mock_fetch.call_args.kwargs
    mock_extract.assert_called_once_with("<html>Bra innehåll</html>")


@patch('trending_news_bot.scrapers.rss_scraper.trafilatura.fetch_url')
def test_fetch_article_content_download_fails(mock_fetch):
    # Simulera att sidan är nere (trafilatura returnerar None)
    mock_fetch.return_value = None

    scraper = RSSScraper("http://fake-url.com")
    text = scraper.fetch_article_content("http://fake-url.com")

    # Ska hanteras snyggt och returnera en tom sträng
    assert text == ""


@patch('trending_news_bot.scrapers.rss_scraper.trafilatura.extract')
@patch('trending_news_bot.scrapers.rss_scraper.trafilatura.fetch_url')
def test_fetch_article_content_extract_fails(mock_fetch, mock_extract):
    mock_fetch.return_value = "<html>Tom sida</html>"
    # Sidan laddas, men trafilatura hittar ingen text
    mock_extract.return_value = None

    scraper = RSSScraper("http://fake-url.com")
    text = scraper.fetch_article_content("http://fake-url.com")

    assert text == ""
