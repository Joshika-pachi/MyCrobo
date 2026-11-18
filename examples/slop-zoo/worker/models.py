from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    quantity: int
