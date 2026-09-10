"""
Tester for setup_logging (core/logger.py).

Loggningssystemet måste:
 - Skapa logs/-mappen och pipeline.log om de saknas
 - Inte lägga till dubbla handlers om funktionen anropas två gånger
   (annars dubbleras varje loggrad i filen/terminalen)
"""
import logging
from pathlib import Path

from trending_news_bot.core.logger import setup_logging


def test_setup_logging_creates_logfile(tmp_path, monkeypatch):
    # Efter anrop ska logs/pipeline.log existera i tmp_path
    monkeypatch.chdir(tmp_path)
    setup_logging()

    log_file = tmp_path / "logs" / "pipeline.log"
    assert log_file.exists()


def test_setup_logging_no_duplicate_handlers(tmp_path, monkeypatch):
    # Två anrop ska ge exakt 2 handlers (file + console), inte 4.
    # Vi rensar befintliga handlers först så vi testar setup_logging isolerat
    # (pytest har annars en egen handler på root-loggern vid testkörning).
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    setup_logging()
    setup_logging()

    handlers = [h for h in root.handlers
                if isinstance(h, (logging.FileHandler, logging.StreamHandler))]
    assert len(handlers) == 2
    # En ska vara FileHandler, en StreamHandler
    assert sum(1 for h in handlers if isinstance(h, logging.FileHandler)) == 1
    assert sum(1 for h in handlers if isinstance(h, logging.StreamHandler)
               and not isinstance(h, logging.FileHandler)) == 1


def test_setup_logging_skipped_when_handlers_exist(tmp_path, monkeypatch):
    # DOKUMENTATION AV KÄNT BETEENDE (potentiell bugg):
    # setup_logging har guard `if not root_logger.handlers:`. Om NÅGON annan
    # (pytest, ett bibliotek, etc.) redan lagt en handler på root-loggern
    # blir setup_logging en no-op — inga fil- eller konsolhandlers läggs till.
    # I produktion kan detta göra att loggning tystnar om ett bibliotek konfar
    # logging före setup_logging körs. Vill du ha robusthet får du byta
    # guarden mot t.ex. en flagga på funktionen/attribut på root-loggern.
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    # Simulera att ett bibliotek redan lagt en handler
    pre_existing = logging.StreamHandler()
    root.addHandler(pre_existing)

    setup_logging()

    # Vår pre-existing handler ska finnas kvar...
    assert pre_existing in root.handlers
    # ...men setup_loggings egna handlers ska INTE ha lagts till.
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers == []

