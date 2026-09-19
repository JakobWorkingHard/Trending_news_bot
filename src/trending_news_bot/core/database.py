import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict

class DatabaseManager:
    """
    Hanterar vår SQLite-databas för att spara nyhetsartiklar och dubblettkontroll.
    """

    def __init__(self, db_name: str = "articles.db"):
        self.logger = logging.getLogger(self.__class__.__name__)

        db_dir = Path("data")
        db_dir.mkdir(exist_ok=True)

        self.db_path = db_dir / db_name
        self._create_tables()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _utc_cutoff(self, **delta_kwargs) -> str:
        """Returnerar nu-minus-delta som UTC-sträng för SQL WHERE-satser."""
        return (datetime.now(timezone.utc) - timedelta(**delta_kwargs)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def _create_tables(self):
        query = """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            source_site TEXT NOT NULL,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        try:
            with self._get_connection() as conn:
                conn.execute(query)
        except Exception as e:
            self.logger.error(f"Fel vid skapande av tabeller: {e}")

    def is_url_seen(self, url: str) -> bool:
        query = "SELECT 1 FROM articles WHERE url = ?"
        try:
            with self._get_connection() as conn:
                return conn.execute(query, (url,)).fetchone() is not None
        except Exception as e:
            self.logger.error(f"Fel vid sökning efter URL {url}: {e}")
            return False

    def save_article(self, url: str, title: str, content: str, source_site: str):
        query = (
            "INSERT INTO articles (url, title, content, source_site) "
            "VALUES (?, ?, ?, ?)"
        )
        try:
            with self._get_connection() as conn:
                conn.execute(query, (url, title, content, source_site))
            self.logger.debug(f"Sparade ny artikel från {source_site}: {title}")
        except sqlite3.IntegrityError:
            self.logger.warning(f"Artikeln finns redan: {url}")
        except Exception as e:
            self.logger.error(f"Fel när {url} skulle sparas: {e}")

    def get_articles_since(self, hours: int) -> List[Dict[str, str]]:
        """
        Hämtar alla artiklar som skrapats inom de senaste 'hours' timmarna.
        Returnerar en lista med dictionaries innehållande 'title' och 'source_site'.
        """
        cutoff = self._utc_cutoff(hours=hours)
        query = (
            "SELECT title, source_site FROM articles "
            "WHERE scraped_at >= ? ORDER BY scraped_at DESC"
        )
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(query, (cutoff,)).fetchall()

                # Bygg en lista med dictionaries för varje artikelrad
                articles = []
                for r in rows:
                    # Plocka ut titeln och källsajten från aktuell rad
                    article = {
                        "title": r["title"],
                        "source_site": r["source_site"],
                    }
                    articles.append(article)
                return articles
        except Exception as e:
            self.logger.error(f"Kunde inte hämta artiklar från databasen: {e}")
            return []

    def delete_older_than(self, days: int) -> int:
        """
        Raderar artiklar där scraped_at är äldre än 'days' dagar från nu (UTC).
        Returnerar antalet raderade rader. Avbryter tyst om days <= 0.
        """
        if days is None or days <= 0:
            return 0
        cutoff = self._utc_cutoff(days=days)
        query = "DELETE FROM articles WHERE scraped_at < ?"
        try:
            with self._get_connection() as conn:
                 # conn.execute returnerar automatiskt en cursor i sqlite3
                cursor = conn.execute(query, (cutoff,))
                deleted = cursor.rowcount
            if deleted:
                self.logger.info(f"Raderade {deleted} artiklar äldre än {days} dagar.")
            return deleted
        except Exception as e:
            self.logger.error(f"Kunde inte radera gamla artiklar: {e}")
            return 0
