"""Base class for background jobs."""

from abc import ABC, abstractmethod


class Job(ABC):
    @abstractmethod
    def run(self) -> int:
        raise NotImplementedError

    def on_success(self) -> None:
        pass
