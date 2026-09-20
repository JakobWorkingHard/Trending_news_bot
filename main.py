import argparse
import json
from pathlib import Path

from trending_news_bot.core.logger import setup_logging
from trending_news_bot.core.content_manager import ContentManager

def _load_logging_settings():
    """
    Läser logging-sektionen från config/settings.json.

    Returnerar (console_level, file_level) som strängar med nuvarande
    default-värden om filen eller sektionen saknas / är ogiltig.
    """
    default_console = "INFO"
    default_file = "DEBUG"
    try:
        with open(Path("config/settings.json"), "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception:
        return default_console, default_file

    logging_cfg = settings.get("logging", {}) if isinstance(settings, dict) else {}
    return (
        logging_cfg.get("console_level", default_console),
        logging_cfg.get("file_level", default_file),
    )

def main():
    console_level, file_level = _load_logging_settings()
    setup_logging(console_level=console_level, file_level=file_level)

    parser = argparse.ArgumentParser(
        description="Skrapa nyheter och identifiera trender via LLM."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Antal timmar bakåt att hämta artiklar för trendanalys. "
        "Utelämnas detta används default_hours från config/settings.json (standard: 24).",
    )
    args = parser.parse_args()

    print("\n🚀 Startar Nyhets-Trendbot...\n")

    try:
        manager = ContentManager()
        manager.run_pipeline(hours=args.hours)
        print("\n✅ Körning avslutad! Kolla logs/pipeline.log för detaljer.")
    except Exception as e:
        print(f"\n❌ Ett fel uppstod: {e}")

if __name__ == "__main__":
    main()
