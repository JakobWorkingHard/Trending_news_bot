import logging
from abc import ABC, abstractmethod
from typing import List, Dict

class BaseScraper(ABC):
    """
    Abstrakt basklass för alla webbskrapor.
    Tvingar subklasser att definiera hur de hämtar data från specifika sidor.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        
        # Sätter upp en logger specifik för den klass som körs.
        # Om 'TechCrunchScraper' körs, kommer loggen heta just så.
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Initierar skrapa för: {self.base_url}")

    @abstractmethod
    def fetch_headlines(self) -> List[Dict[str, str]]:
        """
        Måste implementeras av subklasser.
        Ska hämta rubriker och returnera en lista med dictionaries, t.ex:
        [{'title': 'Ny AI-modell släppt', 'url': 'https://...'}]
        """
        pass

    @abstractmethod
    def fetch_article_content(self, article_url: str) -> str:
        """
        Måste implementeras av subklasser.
        Ska gå in på en specifik artikel och hämta brödtexten.
        """
        pass
    
    def log_status(self, message: str):
        """
        En gemensam metod som alla skrapor ärver för att logga extra info.
        """
        self.logger.debug(message)