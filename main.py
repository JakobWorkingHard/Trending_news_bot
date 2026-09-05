import argparse
from trending_news_bot.core.logger import setup_logging
from trending_news_bot.core.content_manager import ContentManager

def main():
    setup_logging()

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
