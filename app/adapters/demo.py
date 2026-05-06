from app.adapters.base import MarketplaceAdapter, ReservationAction


class DemoMarketplaceAdapter(MarketplaceAdapter):
    def __init__(self, name: str) -> None:
        self.name = name

    def fetch_reservations_full(self) -> list[ReservationAction]:
        return [
            ReservationAction(
                source=self.name,
                external_order_id=f"{self.name}-order-001",
                sku="DEMO-SKU-001",
                quantity=1,
            )
        ]

    def fetch_reservations_delta(self, date_from: int, date_to: int) -> list[ReservationAction]:
        _ = date_from
        _ = date_to
        return self.fetch_reservations_full()

    def fetch_new_reservations(self) -> list[ReservationAction]:
        return self.fetch_reservations_full()

    def sync_available_stock(self, available_stock_by_sku: dict[str, int]) -> None:
        # TODO: Replace with stock update API calls for this marketplace.
        _ = available_stock_by_sku
