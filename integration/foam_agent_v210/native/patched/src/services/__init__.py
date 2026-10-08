"""Delay backend initialization until an actual model call."""
from utils import LLMService
from config import Config


class LazyLLMService:
    def __init__(self):
        self.service = None

    def __getattr__(self, name):
        if self.service is None:
            self.service = LLMService(Config())
        return getattr(self.service, name)


global_llm_service = LazyLLMService()
