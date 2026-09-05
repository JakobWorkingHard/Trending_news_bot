import os
import logging
from openai import OpenAI
from dotenv import load_dotenv

class LLMClient:
    """
    Hanterar all kommunikation med vår LLM (Kimi / Moonshot AI).
    """

    def __init__(
        self,
        model: str = "zai-org/GLM-5.2",
        temperature: float = 0.7,
        base_url: str = "https://models.think.evroc.com/v1",
    ):
        self.logger = logging.getLogger(self.__class__.__name__)

        # Läs in vår .env-fil så att vi får tillgång till hemliga nycklar
        load_dotenv()

        api_key = os.getenv("EVROC_API_KEY")
        if not api_key:
            self.logger.error("EVROC_API_KEY hittades inte i .env-filen!")
            raise ValueError("EVROC_API_KEY saknas.")

        # Eftersom Kimis API är byggt på samma standard som OpenAI,
        # kan vi använda OpenAI-klienten men peka den mot valfri
        # OpenAI-kompatibel server (konfigurerbar via settings.json).
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model
        self.temperature = temperature
        self.base_url = base_url
        self.logger.info(
            f"LLMClient initierad. Använder modell: {self.model} "
            f"(temperature={self.temperature}, base_url={self.base_url})"
        )

    def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        """
        Skickar prompter till Kimi och returnerar svaret.
        """
        self.logger.info("Skickar begäran till Kimi...")
        self.logger.debug(f"System Prompt: {system_prompt}")
        self.logger.debug(f"User Prompt: {user_prompt[:100]}...") # Loggar bara starten av prompten

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=self.temperature
            )
            
            result = response.choices[0].message.content
            self.logger.info("Fick svar från Kimi.")
            return result

        except Exception as e:
            self.logger.error(f"Ett fel uppstod vid kontakt med LLM: {e}")
            return f"Fel vid generering: {e}"