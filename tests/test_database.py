"""
Tester for DatabaseManager (core/database.py).

Databasen är hjärtat i systemet — om den tappar data, släpper in
dubbletter eller filtrerar fel på tid blir trendanalysen fel.
Dessa tester verifierar:
 - Att tabellen skapas vid initiering
 - Att dubblettkontrollen (is_url_seen) fungerar
 - Att UNIQUE-constraint hindrar dublettsparning
 - Att tidsfiltreringen i get_articles_since stämmer (kritiskt för trendanalys)
 - Att retention-raderingen är säker (räddar aktuell data, raderar gammal)
 - Att retention med days=0/None inte raderar något (säkerhetsventil)
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from trending_news_bot.core.database import DatabaseManager


def _insert_with_timestamp(db: DatabaseManager, url: str, title: str,
                          source_site: str, scraped_at: str):
    """Hjälpmetod: sätt in artikel med explicit scraped_at via raw SQL."""
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO articles (url, title, source_site, scraped_at) "
            "VALUES (?, ?, ?, ?)",
            (url, title, source_site, scraped_at),
        )
        conn.commit()


def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --- Initiering ---


def test_create_tables_on_init(tmp_db):
    # Tabellen ska finnas efter att DatabaseManager skapats
    with tmp_db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
        )
        assert cursor.fetchone() is not None


# --- Dubblettkontroll ---


def test_save_and_is_url_seen(tmp_db):
    # Sparad URL ska hittas av is_url_seen
    assert tmp_db.is_url_seen("http://example.com/a") is False

    tmp_db.save_article(
        url="http://example.com/a",
        title="Test",
        content="innehåll",
        source_site="example.com",
    )
    assert tmp_db.is_url_seen("http://example.com/a") is True


def test_is_url_seen_false_for_unknown(tmp_db):
    # En URL som inte sparats ska returnera False
    assert tmp_db.is_url_seen("http://never-seen.com/x") is False


def test_save_duplicate_url_does_not_raise(tmp_db, caplog):
    # Andra sparningen av samma URL ska fångas av IntegrityError, inte krascha
    tmp_db.save_article(
        url="http://dup.com/a",
        title="Första",
        content="x",
        source_site="dup.com",
    )
    # Spara samma URL igen — ska logga varning, inte kasta undantag
    tmp_db.save_article(
        url="http://dup.com/a",
        title="Andra",
        content="y",
        source_site="dup.com",
    )
    # Bekräfta att det fortfarande bara finns EN rad i databasen
    with tmp_db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles")
        assert cursor.fetchone()[0] == 1


# --- get_articles_since ---


def test_get_articles_since_filters_by_hours(tmp_db):
    # En artikel nyss, en artikel 48h gammal — bara den nya ska komma med
    now = _now_utc_str()
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

    _insert_with_timestamp(tmp_db, "http://new.com/1", "Ny", "new.com", now)
    _insert_with_timestamp(tmp_db, "http://old.com/1", "Gammal", "old.com", old)

    recent = tmp_db.get_articles_since(hours=24)
    assert len(recent) == 1
    assert recent[0]["title"] == "Ny"


def test_get_articles_since_orders_descending(tmp_db):
    # Artiklar ska komma i fallande ordning (nyaste först)
    t1 = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    t2 = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    t3 = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    _insert_with_timestamp(tmp_db, "http://a.com/1", "Äldst", "a.com", t3)
    _insert_with_timestamp(tmp_db, "http://a.com/2", "Mellan", "a.com", t2)
    _insert_with_timestamp(tmp_db, "http://a.com/3", "Nyast", "a.com", t1)

    recent = tmp_db.get_articles_since(hours=24)
    titles = [r["title"] for r in recent]
    assert titles == ["Nyast", "Mellan", "Äldst"]


def test_get_articles_since_returns_empty_when_none(tmp_db):
    # Ingen data i databasen → tom lista, inte krasch
    assert tmp_db.get_articles_since(hours=24) == []


# --- delete_older_than ---


def test_delete_older_than_removes_only_old(tmp_db):
    # Gammal artikel raderas, ny spararas, returnerar antal raderade
    now = _now_utc_str()
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")

    _insert_with_timestamp(tmp_db, "http://keep.com/1", "Ny", "keep.com", now)
    _insert_with_timestamp(tmp_db, "http://old.com/1", "Gammal", "old.com", old)

    deleted = tmp_db.delete_older_than(days=30)

    assert deleted == 1
    # Den nya ska finnas kvar
    assert tmp_db.is_url_seen("http://keep.com/1") is True
    # Den gamla ska vara borta
    assert tmp_db.is_url_seen("http://old.com/1") is False


def test_delete_older_than_zero_is_noop(tmp_db):
    # days=0 ska inte radera något (säkerhetsventil)
    tmp_db.save_article(
        url="http://safe.com/1",
        title="Spara",
        content="x",
        source_site="safe.com",
    )
    deleted = tmp_db.delete_older_than(days=0)
    assert deleted == 0
    assert tmp_db.is_url_seen("http://safe.com/1") is True


def test_delete_older_than_none_is_noop(tmp_db):
    # days=None ska inte radera något
    tmp_db.save_article(
        url="http://safe.com/2",
        title="Spara",
        content="x",
        source_site="safe.com",
    )
    deleted = tmp_db.delete_older_than(days=None)
    assert deleted == 0
    assert tmp_db.is_url_seen("http://safe.com/2") is True


def test_delete_older_than_negative_is_noop(tmp_db):
    # Negativt värde ska inte radera något (extra försiktighet)
    tmp_db.save_article(
        url="http://safe.com/3",
        title="Spara",
        content="x",
        source_site="safe.com",
    )
    deleted = tmp_db.delete_older_than(days=-1)
    assert deleted == 0
    assert tmp_db.is_url_seen("http://safe.com/3") is True
