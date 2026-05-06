from dataclasses import dataclass
from typing import Protocol


@dataclass
class ReservationAction:
    source: str
    external_order_id: str
    sku: str
    quantity: int


def is_value_configured(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    return not normalized.lower().startswith("your_")


class MarketplaceAdapter(Protocol):
    name: str

    def is_configured(self) -> bool:
        """Return True when adapter has required API credentials."""

    def fetch_new_reservations(self) -> list[ReservationAction]:
        """Fetch new orders and convert them into reservation actions."""

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        """Push stock available for sale after applying reserves."""
