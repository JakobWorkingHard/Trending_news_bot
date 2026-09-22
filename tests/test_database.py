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


def test_save_duplicate_url_does_not_raise(tmp_db):
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

    # Skapa en tom lista för att samla titlarna
    titles = []

    # Gå igenom varje artikel och plocka ut titeln
    for r in recent:
        current_title = r["title"]
        titles.append(current_title)

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


# --- summary-sparning ---


def test_save_article_stores_summary(tmp_db):
    # summary ska sparas och kunna hämtas via get_articles_since
    tmp_db.save_article(
        url="http://example.com/sum",
        title="Med summary",
        content="brödtext",
        source_site="example.com",
        summary="Kort sammanfattning",
    )
    recent = tmp_db.get_articles_since(hours=24)
    assert len(recent) == 1
    assert recent[0]["summary"] == "Kort sammanfattning"
    # url ska också finnas med (behövs i Call 2)
    assert recent[0]["url"] == "http://example.com/sum"


def test_save_article_without_summary_defaults_empty(tmp_db):
    # Utan summary-argument ska summary vara "" i hämtat resultat
    tmp_db.save_article(
        url="http://example.com/nosum",
        title="Utan summary",
        content="brödtext",
        source_site="example.com",
    )
    recent = tmp_db.get_articles_since(hours=24)
    assert recent[0]["summary"] == ""


def test_get_articles_since_includes_url_and_summary_keys(tmp_db):
    # Varje artikel-dict ska ha title, source_site, summary och url
    tmp_db.save_article(
        url="http://example.com/k",
        title="K",
        content="x",
        source_site="example.com",
        summary="s",
    )
    recent = tmp_db.get_articles_since(hours=24)
    a = recent[0]
    assert set(a.keys()) >= {"title", "source_site", "summary", "url"}


# --- get_articles_by_urls ---


def test_get_articles_by_urls_returns_matching(tmp_db):
    # Spara tre artiklar, hämta två specifika via URL-lista
    tmp_db.save_article("http://a.com/1", "A1", "contentA1", "a.com", "sumA")
    tmp_db.save_article("http://b.com/1", "B1", "contentB1", "b.com", "sumB")
    tmp_db.save_article("http://c.com/1", "C1", "contentC1", "c.com", "sumC")

    rows = tmp_db.get_articles_by_urls(["http://a.com/1", "http://c.com/1"])
    urls = {r["url"] for r in rows}
    assert urls == {"http://a.com/1", "http://c.com/1"}
    # Varje rad ska ha url, title och content
    for r in rows:
        assert "content" in r and r["content"]
        assert "title" in r


def test_get_articles_by_urls_empty_list_returns_empty(tmp_db):
    # Tom lista får inte ge ogiltig SQL (IN ()) — ska returnera []
    assert tmp_db.get_articles_by_urls([]) == []


def test_get_articles_by_urls_unknown_urls_return_empty(tmp_db):
    # URL:er som inte finns i DB ger tomt resultat (ingen krasch)
    tmp_db.save_article("http://a.com/1", "A1", "c", "a.com", "s")
    rows = tmp_db.get_articles_by_urls(["http://finns.inte/x"])
    assert rows == []


# --- Migrering av summary-kolumn ---


def test_summary_column_migration(tmp_path, monkeypatch):
    # Simulera en äldre databas UTAN summary-kolumn och verifiera att
    # DatabaseManager migrerar den vid initiering (ALTER TABLE ADD COLUMN).
    import sqlite3
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_file = data_dir / "articles.db"

    # Skapa tabellen med det GAMLA schemat (ingen summary-kolumn)
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "CREATE TABLE articles ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT UNIQUE NOT NULL, "
            "title TEXT NOT NULL, "
            "content TEXT, "
            "source_site TEXT NOT NULL, "
            "scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO articles (url, title, content, source_site) "
            "VALUES (?, ?, ?, ?)",
            ("http://old.com/1", "Gammal", "text", "old.com"),
        )

    # Initiera DatabaseManager — ska köra ALTER TABLE utan att krascha
    db = DatabaseManager()
    # Verifiera att summary-kolumnen nu finns
    with db._get_connection() as conn:
        cursor = conn.execute("PRAGMA table_info(articles)")
        columns = {row[1] for row in cursor.fetchall()}
    assert "summary" in columns

    # Gammal data ska finnas kvar, summary ska vara NULL → "" vid hämtning
    recent = db.get_articles_since(hours=24)
    assert len(recent) == 1
    assert recent[0]["title"] == "Gammal"
    assert recent[0]["summary"] == ""
