import logging
from pathlib import Path

def _to_level(value, default):
    """
    Konverterar en loggnivå till logging-modulens interna heltalsrepresentation.

    Accepterar antingen en sträng (t.ex. "INFO") eller ett heltal (t.ex. 20).
    Returnerar 'default' om värdet är ogiltigt eller okänt.
    """
    if isinstance(value, str):
        lvl = logging.getLevelName(value.upper())
        return lvl if isinstance(lvl, int) else default
    if isinstance(value, int):
        return value
    return default

def setup_logging(console_level="INFO", file_level="DEBUG"):
    """
    Konfigurerar projektets globala loggning.

    Parametrarna styr vilken nivå som skickas till respektive handler:
      - console_level: lägsta nivån som visas i terminalen (default INFO)
      - file_level:    lägsta nivån som skrivs till filen (default DEBUG)

    Värdena kan anges som strängar ("INFO", "DEBUG" osv.) eller som heltal.
    Rot-loggern låses till DEBUG så att alla meddelanden når hanterarna;
    varje handler filtrerar sedan själv.

    Normalt anropas funktionen från main.py som läser nivåerna från
    config/settings.json (sektionen "logging") och skickar in dem här.
    """
    # 1. Skapa mappen 'logs' i huvudkatalogen om den inte redan finns
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "pipeline.log"

    # 2. Skapa ett gemensamt format för hur texten ska se ut
    # Exempel: 2026-08-28 09:45:12 - RSSScraper - INFO - Hittade 10 rubriker
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 3. Sätt upp "Fil-hanteraren" (nivån styrs av file_level)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(_to_level(file_level, logging.DEBUG))
    file_handler.setFormatter(formatter)

    # 4. Sätt upp "Terminal-hanteraren" (nivån styrs av console_level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(_to_level(console_level, logging.INFO))
    console_handler.setFormatter(formatter)

    # 5. Hämta rot-loggern och koppla på våra hanterare
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG) # Roten måste släppa igenom allt till hanterarna
    
    # Undvik att lägga till hanterare flera gånger om funktionen råkar köras två gånger
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
    logging.info("Loggningssystemet är nu aktiverat och redo.")
