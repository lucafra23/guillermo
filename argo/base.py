from abc import ABC, abstractmethod
from typing import List
from nautilus_trader.model.identifiers import InstrumentId
from argo.models import Strategy, Portfolio


class BaseScanner(ABC):
    """
    Abstract base class for all alpha scanners.
    """
    def __init__(self, instruments: List[InstrumentId], **params):
        self.instruments = instruments
        self.params = params

    @abstractmethod
    def run(self):
        """
        The main logic for the scanner. This method should fetch data,
        perform analysis, and generate Signals and Recommendations in the database.

        This method will be called by a Celery task or a management command.
        """
        raise NotImplementedError

    def get_strategy_model(self, class_path: str) -> Strategy:
        """Helper to retrieve a strategy model from the database."""
        try:
            return Strategy.objects.get(class_path=class_path)
        except Strategy.DoesNotExist:
            raise LookupError(f"Strategy with class_path '{class_path}' not found in the database. Please register it in the Django admin.")