"""
Tester for BaseScraper (scrapers/base_scraper.py).

BaseScraper är en abstrakt basklass. Testerna verifierar att
abstraktionskontraktet upprätthålls:
 - Direkt instansiering av BaseScraper ska misslyckas
 - En subklass utan implementerade abstrakta metoder ska misslyckas
 - En komplett subklass fungerar och log_status anropar logger.debug
"""
import logging
from unittest.mock import MagicMock

import pytest

from trending_news_bot.scrapers.base_scraper import BaseScraper


def test_cannot_instantiate_abstract_class():
    # BaseScraper är abstrakt — ska kasta TypeError
    with pytest.raises(TypeError):
        BaseScraper("http://example.com")


def test_incomplete_subclass_raises():
    # Subklass utan implementerade abstrakta metoder ska också kasta TypeError
    class IncompleteScraper(BaseScraper):
        pass

    with pytest.raises(TypeError):
        IncompleteScraper("http://example.com")


def test_log_status_calls_debug(monkeypatch):
    # En komplett subklass: log_status ska anropa logger.debug med meddelandet
    class CompleteScraper(BaseScraper):
        def fetch_headlines(self):
            return []

        def fetch_article_content(self, article_url):
            return ""

    # Byt ut loggern mot en mock så vi kan inspektera anrop
    mock_logger = MagicMock()
    monkeypatch.setattr(logging, "getLogger", lambda name=None: mock_logger)

    scraper = CompleteScraper("http://example.com")
    scraper.log_status("test-meddelande")

    mock_logger.debug.assert_called_with("test-meddelande")
