from unittest.mock import Mock, patch

from app.adapters.yandex_market import (
    YandexFbsItem,
    YandexFbsOrder,
    YandexMarketAdapter,
)
from app.fbs_assembly_order import (
    apply_assembly_order_to_yandex_rows,
    parse_assembly_sheet_values,
)
from app.google_sheet_write import yandex_order_highlight_range
from app.yandex_fbs_labels import (
    YandexFbsListRow,
    _fetch_labels_in_order,
    build_order_box_labels,
    build_sorted_list_rows,
    fetch_awaiting_assembly_labels,
)


def _order(*, quantity: int = 1) -> YandexFbsOrder:
    return YandexFbsOrder(
        order_id="1001",
        status="PROCESSING",
        substatus="STARTED",
        lines=(("SKU-1", quantity),),
        items=(YandexFbsItem(item_id=501, sku="SKU-1", quantity=quantity),),
    )


def test_yandex_list_has_one_row_per_product_unit() -> None:
    rows = build_sorted_list_rows([_order(quantity=3)])

    assert len(rows) == 3
    assert [row.quantity for row in rows] == [1, 1, 1]
    assert [row.order_id for row in rows] == ["1001", "1001", "1001"]


def test_yandex_rows_follow_assembly_order() -> None:
    rows = [
        YandexFbsListRow(1, "O-1", "SKU-B", 1, "STARTED"),
        YandexFbsListRow(2, "O-2", "SKU-A", 1, "STARTED"),
    ]
    entries = parse_assembly_sheet_values(
        [["Артикул", "", "Ячейка"], ["SKU-A", "", "A-1"], ["SKU-B", "", "A-2"]]
    )

    with patch(
        "app.fbs_assembly_order.load_assembly_entries_from_google_sheet",
        return_value=entries,
    ):
        reordered, _ = apply_assembly_order_to_yandex_rows(
            rows,
            default_stocks_sheet_url="https://docs.google.com/spreadsheets/d/x/edit",
            google_service_account_file="/tmp/creds.json",
            assembly_sheet_name="assembly",
            row_factory=YandexFbsListRow,
        )

    assert [(row.sku, row.order_id) for row in reordered] == [
        ("SKU-A", "O-2"),
        ("SKU-B", "O-1"),
    ]


def test_yandex_adapter_takes_only_started_orders_and_keeps_item_ids() -> None:
    adapter = YandexMarketAdapter("123", "secret")
    adapter._iter_orders = Mock(
        return_value=iter(
            [
                {
                    "id": 1001,
                    "substatus": "STARTED",
                    "items": [{"id": 501, "offerId": "SKU-1", "count": 2}],
                },
                {
                    "id": 1002,
                    "substatus": "READY_TO_SHIP",
                    "items": [{"id": 502, "offerId": "SKU-2", "count": 1}],
                },
            ]
        )
    )

    orders = adapter.list_awaiting_assembly_orders()

    assert len(orders) == 1
    assert orders[0].order_id == "1001"
    assert orders[0].substatus == "STARTED"
    assert orders[0].items == (YandexFbsItem(item_id=501, sku="SKU-1", quantity=2),)


def test_yandex_order_iterator_uses_all_token_pages() -> None:
    adapter = YandexMarketAdapter("123", "secret")
    first = Mock()
    first.raise_for_status.return_value = None
    first.json.return_value = {
        "orders": [{"id": 1}],
        "paging": {"nextPageToken": "next-50"},
    }
    second = Mock()
    second.raise_for_status.return_value = None
    second.json.return_value = {"orders": [{"id": 2}], "paging": {}}

    with patch(
        "app.adapters.yandex_market.requests.get", side_effect=[first, second]
    ) as get:
        orders = list(adapter._iter_orders())

    assert [order["id"] for order in orders] == [1, 2]
    assert "pageToken" not in get.call_args_list[0].kwargs["params"]
    assert get.call_args_list[1].kwargs["params"]["pageToken"] == "next-50"


def test_yandex_adapter_creates_one_box_per_unit_without_status_request() -> None:
    adapter = YandexMarketAdapter("123", "secret")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "status": "OK",
        "result": {"boxes": [{"boxId": 7001}, {"boxId": 7002}, {"boxId": 7003}]},
    }

    with patch("app.adapters.yandex_market.requests.put", return_value=response) as put:
        box_ids = adapter.set_order_unit_boxes(_order(quantity=3))

    assert box_ids == [7001, 7002, 7003]
    assert put.call_count == 1
    assert put.call_args.kwargs["json"] == {
        "boxes": [
            {"items": [{"id": 501, "fullCount": 1}]},
            {"items": [{"id": 501, "fullCount": 1}]},
            {"items": [{"id": 501, "fullCount": 1}]},
        ]
    }
    assert put.call_args.args[0].endswith("/orders/1001/boxes")


def test_yandex_box_labels_are_requested_in_sorted_list_order() -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.label_calls = []

        def set_order_unit_boxes(self, order):
            assert order.order_id == "1001"
            return [7001, 7002]

        def fetch_box_label_pdf(self, order_id, box_id, *, label_format):
            self.label_calls.append((order_id, box_id, label_format))
            return f"PDF-{box_id}".encode()

    adapter = FakeAdapter()
    order = YandexFbsOrder(
        order_id="1001",
        status="PROCESSING",
        substatus="STARTED",
        lines=(("SKU-B", 1), ("SKU-A", 1)),
        items=(
            YandexFbsItem(item_id=501, sku="SKU-B", quantity=1),
            YandexFbsItem(item_id=502, sku="SKU-A", quantity=1),
        ),
    )
    sorted_rows = [
        YandexFbsListRow(1, "1001", "SKU-A", 1, "STARTED"),
        YandexFbsListRow(2, "1001", "SKU-B", 1, "STARTED"),
    ]

    with patch("app.yandex_fbs_labels.merge_label_pdfs", return_value=None):
        files, warnings = _fetch_labels_in_order(adapter, [order], sorted_rows)

    assert len(warnings) == 1
    assert "Не удалось объединить PDF" in warnings[0]
    assert adapter.label_calls == [
        ("1001", 7002, "A9_HORIZONTALLY"),
        ("1001", 7001, "A9_HORIZONTALLY"),
    ]
    assert [name for name, _ in files] == [
        "yandex_label_1001_7002.pdf",
        "yandex_label_1001_7001.pdf",
    ]


def test_yandex_sheet_adds_box_number_matching_label_layout() -> None:
    order = YandexFbsOrder(
        order_id="123456",
        status="PROCESSING",
        substatus="STARTED",
        lines=(("SKU-B", 1), ("SKU-A", 1)),
        items=(
            YandexFbsItem(item_id=501, sku="SKU-B", quantity=1),
            YandexFbsItem(item_id=502, sku="SKU-A", quantity=1),
        ),
    )
    assembly_sorted_rows = [
        YandexFbsListRow(1, "123456", "SKU-A", 1, "STARTED"),
        YandexFbsListRow(2, "123456", "SKU-B", 1, "STARTED"),
    ]

    assert build_order_box_labels(assembly_sorted_rows, [order]) == [
        "123456 2/2",
        "123456 1/2",
    ]
    assert yandex_order_highlight_range("123456 2/2") == (2, 6)


def test_yandex_item_limit_takes_first_sorted_units_not_orders() -> None:
    first_order = _order(quantity=3)
    second_order = YandexFbsOrder(
        order_id="1002",
        status="PROCESSING",
        substatus="STARTED",
        lines=(("SKU-2", 2),),
        items=(YandexFbsItem(item_id=502, sku="SKU-2", quantity=2),),
    )

    class FakeAdapter:
        def list_awaiting_assembly_orders(self, *, substatus):
            assert substatus == "STARTED"
            return [first_order, second_order]

    with patch(
        "app.yandex_fbs_labels._fetch_labels_in_order",
        return_value=([("labels.pdf", b"%PDF")], []),
    ) as fetch_labels:
        bundle = fetch_awaiting_assembly_labels(FakeAdapter(), max_units=4)

    assert bundle.available_units == 5
    assert len(bundle.list_rows) == 4
    assert [row.order_id for row in bundle.list_rows] == ["1001", "1001", "1001", "1002"]
    assert [order.order_id for order in bundle.orders] == ["1001", "1002"]
    assert len(fetch_labels.call_args.args[2]) == 4
