import logging
from pathlib import Path

def setup_logging():
    """
    Konfigurerar projektets globala loggning.
    Skriver INFO (och uppåt) till terminalen.
    Skriver DEBUG (allt) till en fil i logs-mappen.
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

    # 3. Sätt upp "Fil-hanteraren" (sparar allt)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 4. Sätt upp "Terminal-hanteraren" (visar bara det viktigaste)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Ändra till DEBUG här om du vill se allt i terminalen
    console_handler.setFormatter(formatter)

    # 5. Hämta rot-loggern och koppla på våra hanterare
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG) # Roten måste släppa igenom allt till hanterarna
    
    # Undvik att lägga till hanterare flera gånger om funktionen råkar köras två gånger
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
    logging.info("Loggningssystemet är nu aktiverat och redo.")