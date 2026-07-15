"""Каталог товаров и комплектов новой панели /warehouse."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, delete, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.crm_repository import CrmPriceType

_DEFAULT_UNITS = ("шт", "л", "мл", "г", "кг")
_DEFAULT_MARKING_TYPES = (
    "Не подлежит маркировке",
    "Табачная продукция",
    "Обувь",
    "Одежда, белье",
    "Шины и покрышки",
    "Духи и туалетная вода",
    "Фотоаппараты и лампы-вспышки",
    "Молочная продукция",
    "Упакованная вода",
    "Безалкогольные напитки",
    "Пиво и слабоалкогольные напитки",
    "БАД",
    "Антисептики",
    "Медицинские изделия",
    "Лекарственные препараты",
    "Икра осетровых и лососевых",
    "Корма для животных",
    "Растительные масла",
    "Консервированная продукция",
    "Ветеринарные препараты",
)
_CODE128_RE = re.compile(r"^[\x20-\x7E]+$")


class _Base(DeclarativeBase):
    pass


class CatalogProductGroup(_Base):
    __tablename__ = "catalog_product_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    cost: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CatalogCargoPlaceType(_Base):
    __tablename__ = "catalog_cargo_place_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    length_mm: Mapped[str] = mapped_column(String(32), nullable=False)
    width_mm: Mapped[str] = mapped_column(String(32), nullable=False)
    height_mm: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CatalogUnit(_Base):
    __tablename__ = "catalog_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CatalogMarkingType(_Base):
    __tablename__ = "catalog_marking_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CatalogProduct(_Base):
    __tablename__ = "catalog_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    is_kit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(8192), nullable=False, default="")
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("catalog_product_groups.id"), nullable=True)
    country: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sku: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    external_code: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    unit_id: Mapped[int] = mapped_column(Integer, ForeignKey("catalog_units.id"), nullable=True)
    weight: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    width_mm: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    height_mm: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    length_mm: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    volume: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    volume_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    marking_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("catalog_marking_types.id"), nullable=True
    )
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CatalogProductBarcode(_Base):
    __tablename__ = "catalog_product_barcodes"
    __table_args__ = (UniqueConstraint("barcode", name="uq_catalog_barcode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    barcode: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    barcode_group: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CatalogKitComponent(_Base):
    __tablename__ = "catalog_kit_components"
    __table_args__ = (
        UniqueConstraint("kit_product_id", "component_product_id", name="uq_kit_component"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kit_product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    component_product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CatalogProductPrice(_Base):
    __tablename__ = "catalog_product_prices"
    __table_args__ = (
        UniqueConstraint("product_id", "price_type_id", name="uq_catalog_product_price"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("catalog_products.id", ondelete="CASCADE"), nullable=False
    )
    price_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[str] = mapped_column(String(32), nullable=False)


@dataclass
class KitComponentRow:
    component_product_id: int
    component_name: str
    component_sku: str
    component_is_kit: bool
    quantity: int


@dataclass
class ProductPriceRow:
    price_type_id: int
    price_type_name: str
    price: Optional[str] = None


@dataclass
class CatalogProductRow:
    id: int
    is_kit: bool
    name: str
    image_url: str
    description: str
    group_id: Optional[int]
    group_name: str
    country: str
    sku: str
    code: str
    external_code: str
    unit_id: Optional[int]
    unit_name: str
    weight: str
    width_mm: str
    height_mm: str
    length_mm: str
    volume: str
    marking_type_id: Optional[int]
    marking_type_name: str
    volume_manual: bool = False
    barcodes: list[dict[str, str]] = field(default_factory=list)
    components: list[KitComponentRow] = field(default_factory=list)
    prices: list[ProductPriceRow] = field(default_factory=list)
    barcode_count: int = 0
    created_at_ts: int = 0
    updated_at_ts: int = 0


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


def _validate_code128(barcode: str) -> str:
    code = barcode.strip()
    if not code:
        raise ValueError("Штрихкод не может быть пустым")
    if len(code) > 128:
        raise ValueError("Штрихкод слишком длинный (макс. 128 символов)")
    if not _CODE128_RE.match(code):
        raise ValueError(f"Штрихкод «{code}» не соответствует формату Code128")
    return code


def _normalize_barcode_label(label: str) -> str:
    return str(label or "").strip()[:128]


def _normalize_barcode_group(group: str) -> str:
    return str(group or "").strip()[:128]


def _format_mm_number(val: Decimal) -> str:
    if val == val.to_integral_value():
        return str(int(val))
    return format(val.normalize(), "f").rstrip("0").rstrip(".")


def _parse_optional_mm(raw: object, *, field_label: str) -> str:
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        return ""
    try:
        val = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_label} должна быть числом (мм)") from exc
    if val < 0:
        raise ValueError(f"{field_label} не может быть отрицательной")
    return _format_mm_number(val)[:32]


def _parse_positive_number(raw: object, *, field_label: str) -> str:
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        raise ValueError(f"{field_label} не указан")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_label} должен быть числом") from exc
    if value <= 0:
        raise ValueError(f"{field_label} должен быть больше нуля")
    return _format_mm_number(value)[:32]


MM3_PER_LITER = Decimal("1000000")


def compute_volume_liters(width_mm: object, height_mm: object, length_mm: object) -> str:
    values: list[Decimal] = []
    for raw, label in (
        (width_mm, "Ширина"),
        (height_mm, "Высота"),
        (length_mm, "Длина"),
    ):
        text = str(raw or "").strip().replace(",", ".")
        if not text:
            return ""
        try:
            val = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{label} должна быть числом (мм)") from exc
        if val < 0:
            raise ValueError(f"{label} не может быть отрицательной")
        values.append(val)
    return _format_mm_number(values[0] * values[1] * values[2] / MM3_PER_LITER)[:32]


def resolve_product_volume(
    volume_raw: object,
    width_mm: object,
    height_mm: object,
    length_mm: object,
    *,
    volume_manual: bool = False,
) -> str:
    manual = str(volume_raw or "").strip()
    if volume_manual and manual:
        return manual[:32]
    computed = compute_volume_liters(width_mm, height_mm, length_mm)
    if computed:
        return computed
    return manual[:32]


def barcode_display_name(item: Any) -> str:
    if isinstance(item, dict):
        label = _normalize_barcode_label(item.get("label"))
        if label:
            return label
        return _normalize_barcode_group(item.get("group"))
    return ""


def _parse_barcode_item(item: Any) -> dict[str, str]:
    if isinstance(item, dict):
        code = _validate_code128(str(item.get("barcode") or ""))
        return {
            "barcode": code,
            "label": _normalize_barcode_label(item.get("label")),
            "group": _normalize_barcode_group(item.get("group")),
        }
    code = _validate_code128(str(item or ""))
    return {"barcode": code, "label": "", "group": ""}


class CatalogRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._migrate_product_group_comment()
        self._migrate_product_group_cost()
        self._migrate_barcode_label()
        self._migrate_barcode_group()
        self._migrate_product_dimensions()
        self._seed_defaults()

    def _migrate_barcode_group(self) -> None:
        from sqlalchemy import inspect, text

        if "catalog_product_barcodes" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("catalog_product_barcodes")}
        if "barcode_group" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE catalog_product_barcodes "
                    "ADD COLUMN barcode_group VARCHAR(128) NOT NULL DEFAULT ''"
                )
            )
            session.commit()

    def _migrate_barcode_label(self) -> None:
        from sqlalchemy import inspect, text

        if "catalog_product_barcodes" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("catalog_product_barcodes")}
        if "label" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE catalog_product_barcodes "
                    "ADD COLUMN label VARCHAR(128) NOT NULL DEFAULT ''"
                )
            )
            session.commit()

    def _migrate_product_dimensions(self) -> None:
        from sqlalchemy import inspect, text

        if "catalog_products" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("catalog_products")}
        dialect = self.engine.dialect.name
        additions = ("width_mm", "height_mm", "length_mm")
        with Session(self.engine) as session:
            for col_name in additions:
                if col_name in cols:
                    continue
                if dialect == "postgresql":
                    sql = (
                        f"ALTER TABLE catalog_products "
                        f"ADD COLUMN IF NOT EXISTS {col_name} VARCHAR(32) NOT NULL DEFAULT ''"
                    )
                else:
                    sql = (
                        f"ALTER TABLE catalog_products "
                        f"ADD COLUMN {col_name} VARCHAR(32) NOT NULL DEFAULT ''"
                    )
                session.execute(text(sql))
            if "volume_manual" not in cols:
                if dialect == "postgresql":
                    sql = (
                        "ALTER TABLE catalog_products "
                        "ADD COLUMN IF NOT EXISTS volume_manual BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                else:
                    sql = (
                        "ALTER TABLE catalog_products "
                        "ADD COLUMN volume_manual BOOLEAN NOT NULL DEFAULT 0"
                    )
                session.execute(text(sql))
            session.commit()

    def _migrate_product_group_cost(self) -> None:
        from sqlalchemy import inspect, text

        if "catalog_product_groups" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("catalog_product_groups")}
        if "cost" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE catalog_product_groups "
                    "ADD COLUMN cost VARCHAR(32) NOT NULL DEFAULT ''"
                )
            )
            session.commit()

    def _migrate_product_group_comment(self) -> None:
        from sqlalchemy import inspect, text

        if "catalog_product_groups" not in inspect(self.engine).get_table_names():
            return
        cols = {c["name"] for c in inspect(self.engine).get_columns("catalog_product_groups")}
        if "comment" in cols:
            return
        with Session(self.engine) as session:
            session.execute(
                text(
                    "ALTER TABLE catalog_product_groups "
                    "ADD COLUMN comment VARCHAR(512) NOT NULL DEFAULT ''"
                )
            )
            session.commit()

    def _seed_defaults(self) -> None:
        with Session(self.engine) as session:
            if not session.scalar(select(func.count()).select_from(CatalogUnit)):
                for i, name in enumerate(_DEFAULT_UNITS):
                    session.add(
                        CatalogUnit(name=name, sort_order=i, is_default=(name == "шт"))
                    )
            if not session.scalar(select(func.count()).select_from(CatalogMarkingType)):
                for i, name in enumerate(_DEFAULT_MARKING_TYPES):
                    session.add(
                        CatalogMarkingType(
                            name=name, sort_order=i, is_default=(i == 0)
                        )
                    )
            session.commit()

    def get_meta(self) -> dict[str, list[dict[str, Any]]]:
        with Session(self.engine) as session:
            groups = session.scalars(
                select(CatalogProductGroup).order_by(
                    CatalogProductGroup.sort_order, CatalogProductGroup.name
                )
            ).all()
            units = session.scalars(
                select(CatalogUnit).order_by(CatalogUnit.sort_order, CatalogUnit.name)
            ).all()
            marking = session.scalars(
                select(CatalogMarkingType).order_by(
                    CatalogMarkingType.sort_order, CatalogMarkingType.name
                )
            ).all()
        return {
            "groups": [
                {
                    "id": g.id,
                    "name": g.name,
                    "comment": g.comment or "",
                    "cost": _group_cost_api_value(g.cost),
                    "sort_order": g.sort_order,
                }
                for g in groups
            ],
            "units": [
                {
                    "id": u.id,
                    "name": u.name,
                    "sort_order": u.sort_order,
                    "is_default": bool(u.is_default),
                }
                for u in units
            ],
            "marking_types": [
                {
                    "id": m.id,
                    "name": m.name,
                    "sort_order": m.sort_order,
                    "is_default": bool(m.is_default),
                }
                for m in marking
            ],
        }

    def list_cargo_place_types(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CatalogCargoPlaceType).order_by(
                    CatalogCargoPlaceType.sort_order, CatalogCargoPlaceType.name
                )
            ).all()
            return [
                {
                    "id": int(row.id),
                    "name": row.name,
                    "length_mm": row.length_mm,
                    "width_mm": row.width_mm,
                    "height_mm": row.height_mm,
                    "volume_liters": compute_volume_liters(
                        row.width_mm, row.height_mm, row.length_mm
                    ),
                    "comment": row.comment or "",
                }
                for row in rows
            ]

    def save_cargo_place_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {
                int(row.id): row
                for row in session.scalars(select(CatalogCargoPlaceType)).all()
            }
            keep_ids: set[int] = set()
            seen_names: set[str] = set()
            for index, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                name_key = name.casefold()
                if name_key in seen_names:
                    raise ValueError(f"Название грузоместа «{name}» повторяется")
                seen_names.add(name_key)
                length_mm = _parse_positive_number(
                    item.get("length_mm"), field_label=f"Длина грузоместа «{name}»"
                )
                width_mm = _parse_positive_number(
                    item.get("width_mm"), field_label=f"Ширина грузоместа «{name}»"
                )
                height_mm = _parse_positive_number(
                    item.get("height_mm"), field_label=f"Высота грузоместа «{name}»"
                )
                row = None
                raw_id = item.get("id")
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = CatalogCargoPlaceType(
                        name=name[:128],
                        length_mm=length_mm,
                        width_mm=width_mm,
                        height_mm=height_mm,
                    )
                    session.add(row)
                    session.flush()
                else:
                    row.name = name[:128]
                    row.length_mm = length_mm
                    row.width_mm = width_mm
                    row.height_mm = height_mm
                row.comment = str(item.get("comment") or "").strip()[:512]
                row.sort_order = index
                keep_ids.add(int(row.id))

            for row_id, row in existing.items():
                if row_id not in keep_ids:
                    session.delete(row)
            try:
                session.commit()
            except Exception as exc:
                session.rollback()
                raise ValueError("Названия грузомест должны быть уникальными") from exc
        return self.list_cargo_place_types()

    def save_groups(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_dict(CatalogProductGroup, items, "groups")

    @staticmethod
    def _parse_group_cost(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str) and not raw.strip():
            return ""
        return _parse_price(raw) or ""

    def save_units(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_dict(CatalogUnit, items, "units", keep_default=True)

    def save_marking_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_dict(CatalogMarkingType, items, "marking_types", keep_default=True)

    def _save_dict(
        self,
        model,
        items: list[dict[str, Any]],
        key: str,
        *,
        keep_default: bool = False,
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {r.id: r for r in session.scalars(select(model)).all()}
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                raw_id = item.get("id")
                row = None
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    create_kwargs: dict[str, Any] = {"name": name, "sort_order": i}
                    if key == "groups":
                        create_kwargs["comment"] = str(item.get("comment") or "").strip()[:512]
                        create_kwargs["cost"] = self._parse_group_cost(item.get("cost"))
                    row = model(**create_kwargs)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
                    if key == "groups":
                        row.comment = str(item.get("comment") or "").strip()[:512]
                        row.cost = self._parse_group_cost(item.get("cost"))
                session.flush()
                keep_ids.add(int(row.id))
            for rid, row in existing.items():
                if rid not in keep_ids:
                    if keep_default and getattr(row, "is_default", False):
                        continue
                    session.delete(row)
            session.commit()
            rows = session.scalars(select(model).order_by(model.sort_order, model.name)).all()
            if key == "groups":
                return [
                    {
                        "id": r.id,
                        "name": r.name,
                        "comment": r.comment or "",
                        "cost": _group_cost_api_value(r.cost),
                        "sort_order": r.sort_order,
                    }
                    for r in rows
                ]
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "sort_order": r.sort_order,
                    "is_default": bool(getattr(r, "is_default", False)),
                }
                for r in rows
            ]

    def list_products_for_price_type(
        self, price_type_id: int, filters: dict[str, str]
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            if session.get(CrmPriceType, int(price_type_id)) is None:
                raise ValueError("Вид цены не найден")
            q = select(CatalogProduct).order_by(CatalogProduct.name, CatalogProduct.sku)
            conds = self._filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            products = session.scalars(q).all()
            price_rows = session.scalars(
                select(CatalogProductPrice).where(
                    CatalogProductPrice.price_type_id == int(price_type_id)
                )
            ).all()
            price_by_product = {int(r.product_id): r.price for r in price_rows}
            return [
                {
                    "product_id": int(p.id),
                    "sku": p.sku,
                    "name": p.name,
                    "image_url": p.image_url or "",
                    "is_kit": bool(p.is_kit),
                    "price": price_by_product.get(int(p.id)),
                }
                for p in products
            ]

    def save_prices_for_price_type(
        self, price_type_id: int, items: list[dict[str, Any]]
    ) -> int:
        with Session(self.engine) as session:
            if session.get(CrmPriceType, int(price_type_id)) is None:
                raise ValueError("Вид цены не найден")
            updated = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    product_id = int(item.get("product_id"))
                except (TypeError, ValueError):
                    continue
                if session.get(CatalogProduct, product_id) is None:
                    continue
                price = _parse_price(item.get("price"))
                row = session.scalar(
                    select(CatalogProductPrice).where(
                        CatalogProductPrice.product_id == product_id,
                        CatalogProductPrice.price_type_id == int(price_type_id),
                    )
                )
                if price is None:
                    if row is not None:
                        session.delete(row)
                        updated += 1
                    continue
                if row is None:
                    session.add(
                        CatalogProductPrice(
                            product_id=product_id,
                            price_type_id=int(price_type_id),
                            price=price,
                        )
                    )
                    updated += 1
                elif row.price != price:
                    row.price = price
                    updated += 1
            session.commit()
            return updated

    def list_products(self, filters: dict[str, str]) -> list[CatalogProductRow]:
        with Session(self.engine) as session:
            q = select(CatalogProduct).order_by(CatalogProduct.name, CatalogProduct.sku)
            conds = self._filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q).all()
            return [self._product_row(session, r, load_details=False) for r in rows]

    def list_products_for_export(self, filters: dict[str, str]) -> dict[str, Any]:
        """Все данные каталога для массовой выгрузки без N+1 запросов."""
        with Session(self.engine) as session:
            stmt = select(CatalogProduct).order_by(CatalogProduct.name, CatalogProduct.sku)
            conds = self._filter_conditions(session, filters)
            if conds:
                stmt = stmt.where(*conds)
            products = session.scalars(stmt).all()
            price_types = session.scalars(
                select(CrmPriceType).order_by(CrmPriceType.sort_order, CrmPriceType.name)
            ).all()
            if not products:
                return {
                    "products": [],
                    "price_types": [
                        {"id": int(row.id), "name": row.name} for row in price_types
                    ],
                }

            product_ids = [int(row.id) for row in products]
            id_batches = [
                product_ids[start : start + 900] for start in range(0, len(product_ids), 900)
            ]
            groups = {
                int(row.id): row.name
                for row in session.scalars(select(CatalogProductGroup)).all()
            }
            units = {
                int(row.id): row.name for row in session.scalars(select(CatalogUnit)).all()
            }
            markings = {
                int(row.id): row.name
                for row in session.scalars(select(CatalogMarkingType)).all()
            }

            barcodes_by_product: dict[int, list[dict[str, str]]] = {
                product_id: [] for product_id in product_ids
            }
            for batch in id_batches:
                for row in session.scalars(
                    select(CatalogProductBarcode)
                    .where(CatalogProductBarcode.product_id.in_(batch))
                    .order_by(CatalogProductBarcode.product_id, CatalogProductBarcode.sort_order)
                ).all():
                    barcodes_by_product[int(row.product_id)].append(
                        {
                            "barcode": row.barcode,
                            "label": row.label or "",
                            "group": row.barcode_group or "",
                        }
                    )

            prices_by_product: dict[int, dict[int, str]] = {
                product_id: {} for product_id in product_ids
            }
            for batch in id_batches:
                for row in session.scalars(
                    select(CatalogProductPrice).where(
                        CatalogProductPrice.product_id.in_(batch)
                    )
                ).all():
                    prices_by_product[int(row.product_id)][int(row.price_type_id)] = row.price

            components_by_product: dict[int, list[dict[str, Any]]] = {
                product_id: [] for product_id in product_ids
            }
            component_rows = []
            for batch in id_batches:
                component_rows.extend(
                    session.scalars(
                        select(CatalogKitComponent).where(
                            CatalogKitComponent.kit_product_id.in_(batch)
                        )
                    ).all()
                )
            component_ids = {int(row.component_product_id) for row in component_rows}
            component_products = {}
            component_id_list = list(component_ids)
            for start in range(0, len(component_id_list), 900):
                batch = component_id_list[start : start + 900]
                component_products.update(
                    {
                        int(row.id): row
                        for row in session.scalars(
                            select(CatalogProduct).where(CatalogProduct.id.in_(batch))
                        ).all()
                    }
                )
            for row in component_rows:
                component = component_products.get(int(row.component_product_id))
                if component is None:
                    continue
                components_by_product[int(row.kit_product_id)].append(
                    {
                        "sku": component.sku,
                        "name": component.name,
                        "quantity": int(row.quantity),
                    }
                )

            return {
                "price_types": [
                    {"id": int(row.id), "name": row.name} for row in price_types
                ],
                "products": [
                    {
                        "id": int(row.id),
                        "is_kit": bool(row.is_kit),
                        "name": row.name,
                        "sku": row.sku,
                        "code": row.code,
                        "external_code": row.external_code or "",
                        "description": row.description or "",
                        "image_url": row.image_url or "",
                        "group_name": groups.get(int(row.group_id), "") if row.group_id else "",
                        "country": row.country or "",
                        "unit_name": units.get(int(row.unit_id), "") if row.unit_id else "",
                        "weight": row.weight or "",
                        "width_mm": row.width_mm or "",
                        "height_mm": row.height_mm or "",
                        "length_mm": row.length_mm or "",
                        "volume": row.volume or "",
                        "volume_manual": bool(row.volume_manual),
                        "marking_type_name": (
                            markings.get(int(row.marking_type_id), "")
                            if row.marking_type_id
                            else ""
                        ),
                        "barcodes": barcodes_by_product[int(row.id)],
                        "components": components_by_product[int(row.id)],
                        "prices": prices_by_product[int(row.id)],
                    }
                    for row in products
                ],
            }

    def list_products_picker(
        self,
        *,
        q: str = "",
        name: str = "",
        sku: str = "",
        code: str = "",
        exclude_id: int | None = None,
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            stmt = select(CatalogProduct).order_by(CatalogProduct.name)
            if exclude_id is not None:
                stmt = stmt.where(CatalogProduct.id != int(exclude_id))
            pat = _like(q)
            if pat:
                stmt = stmt.where(
                    or_(
                        CatalogProduct.name.ilike(pat),
                        CatalogProduct.sku.ilike(pat),
                        CatalogProduct.code.ilike(pat),
                    )
                )
            for key, col, val in (
                ("name", CatalogProduct.name, name),
                ("sku", CatalogProduct.sku, sku),
                ("code", CatalogProduct.code, code),
            ):
                p = _like(val)
                if p:
                    stmt = stmt.where(col.ilike(p))
            rows = session.scalars(stmt.limit(200)).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "sku": r.sku,
                    "code": r.code,
                    "is_kit": bool(r.is_kit),
                    "image_url": r.image_url or "",
                }
                for r in rows
            ]

    def lookup_products_by_skus(self, skus: list[str]) -> dict[str, dict[str, Any]]:
        """Ключ — sku в нижнем регистре; значение — id, sku, name, image_url."""
        clean = [str(s).strip() for s in skus if str(s).strip()]
        if not clean:
            return {}
        lowered = {s.casefold() for s in clean}
        out: dict[str, dict[str, Any]] = {}
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CatalogProduct).where(func.lower(CatalogProduct.sku).in_(list(lowered)))
            ).all()
            for row in rows:
                key = str(row.sku or "").strip().casefold()
                if key not in lowered:
                    continue
                out[key] = {
                    "id": int(row.id),
                    "sku": str(row.sku or ""),
                    "name": str(row.name or ""),
                    "image_url": str(row.image_url or ""),
                }
        return out

    def lookup_products_with_metrics_by_skus(
        self, skus: list[str]
    ) -> dict[str, dict[str, Any]]:
        clean = [str(sku).strip() for sku in skus if str(sku).strip()]
        lowered = sorted({sku.casefold() for sku in clean})
        if not lowered:
            return {}
        out: dict[str, dict[str, Any]] = {}
        with Session(self.engine) as session:
            for start in range(0, len(lowered), 900):
                batch = lowered[start : start + 900]
                rows = session.scalars(
                    select(CatalogProduct).where(func.lower(CatalogProduct.sku).in_(batch))
                ).all()
                for row in rows:
                    key = str(row.sku or "").strip().casefold()
                    out[key] = {
                        "id": int(row.id),
                        "sku": str(row.sku or ""),
                        "name": str(row.name or ""),
                        "length_mm": str(row.length_mm or ""),
                        "width_mm": str(row.width_mm or ""),
                        "height_mm": str(row.height_mm or ""),
                        "volume_liters": compute_volume_liters(
                            row.width_mm, row.height_mm, row.length_mm
                        ),
                        "weight_kg": str(row.weight or ""),
                    }
        return out

    def update_product_metrics(
        self,
        product_id: int,
        *,
        length_mm: object,
        width_mm: object,
        height_mm: object,
        weight_kg: object,
    ) -> dict[str, Any]:
        length = _parse_positive_number(length_mm, field_label="Длина")
        width = _parse_positive_number(width_mm, field_label="Ширина")
        height = _parse_positive_number(height_mm, field_label="Высота")
        weight = _parse_positive_number(weight_kg, field_label="Вес")
        with Session(self.engine) as session:
            row = session.get(CatalogProduct, int(product_id))
            if row is None:
                raise ValueError("Товар не найден")
            row.length_mm = length
            row.width_mm = width
            row.height_mm = height
            row.weight = weight
            row.volume = compute_volume_liters(width, height, length)
            row.volume_manual = False
            row.updated_at_ts = int(time.time())
            session.commit()
            return {
                "id": int(row.id),
                "sku": row.sku,
                "name": row.name,
                "length_mm": row.length_mm,
                "width_mm": row.width_mm,
                "height_mm": row.height_mm,
                "volume_liters": row.volume,
                "weight_kg": row.weight,
            }

    def build_product_import_index(
        self,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        with Session(self.engine) as session:
            products = session.scalars(select(CatalogProduct)).all()
            by_sku: dict[str, dict[str, Any]] = {}
            by_code: dict[str, dict[str, Any]] = {}
            picker_by_id: dict[int, dict[str, Any]] = {}
            for row in products:
                item = {
                    "id": int(row.id),
                    "name": row.name,
                    "sku": row.sku,
                    "code": row.code,
                    "is_kit": bool(row.is_kit),
                    "image_url": row.image_url or "",
                }
                picker_by_id[int(row.id)] = item
                by_sku[str(row.sku or "").strip().casefold()] = item
                by_code[str(row.code or "").strip().casefold()] = item
            by_barcode: dict[str, dict[str, Any]] = {}
            bc_rows = session.scalars(select(CatalogProductBarcode)).all()
            for bc in bc_rows:
                product = picker_by_id.get(int(bc.product_id))
                if product is None:
                    continue
                by_barcode[str(bc.barcode or "").strip().casefold()] = product
        return by_sku, by_code, by_barcode

    def get_prices_for_products(
        self, product_ids: list[int], price_type_id: int
    ) -> dict[int, str]:
        if not product_ids:
            return {}
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CatalogProductPrice).where(
                    CatalogProductPrice.price_type_id == int(price_type_id),
                    CatalogProductPrice.product_id.in_([int(x) for x in product_ids]),
                )
            ).all()
            return {int(r.product_id): r.price for r in rows}

    def expand_kit_to_lines(self, product_id: int, kit_quantity: int) -> list[dict[str, Any]]:
        qty_k = max(1, int(kit_quantity))
        with Session(self.engine) as session:
            kit = session.get(CatalogProduct, int(product_id))
            if kit is None:
                raise ValueError("Товар не найден")
            if not kit.is_kit:
                raise ValueError("Товар не является комплектом")
            comps = session.scalars(
                select(CatalogKitComponent).where(CatalogKitComponent.kit_product_id == int(product_id))
            ).all()
            if not comps:
                raise ValueError("У комплекта нет составляющих")
            out: list[dict[str, Any]] = []
            for comp in comps:
                p = session.get(CatalogProduct, int(comp.component_product_id))
                if p is None:
                    continue
                out.append(
                    {
                        "product_id": int(p.id),
                        "name": p.name,
                        "sku": p.sku,
                        "code": p.code,
                        "image_url": p.image_url or "",
                        "is_kit": bool(p.is_kit),
                        "quantity": int(comp.quantity) * qty_k,
                    }
                )
            return out

    def _filter_conditions(self, session: Session, filters: dict[str, str]) -> list:
        conds = []
        text_map = {
            "name": CatalogProduct.name,
            "sku": CatalogProduct.sku,
            "code": CatalogProduct.code,
            "external_code": CatalogProduct.external_code,
            "country": CatalogProduct.country,
            "description": CatalogProduct.description,
            "weight": CatalogProduct.weight,
            "width_mm": CatalogProduct.width_mm,
            "height_mm": CatalogProduct.height_mm,
            "length_mm": CatalogProduct.length_mm,
            "volume": CatalogProduct.volume,
        }
        for key, col in text_map.items():
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        for key, col in (
            ("group_id", CatalogProduct.group_id),
            ("unit_id", CatalogProduct.unit_id),
            ("marking_type_id", CatalogProduct.marking_type_id),
        ):
            raw = (filters.get(key) or "").strip()
            if raw:
                try:
                    conds.append(col == int(raw))
                except ValueError:
                    pass
        kind = (filters.get("kind") or "").strip().lower()
        if kind == "kit":
            conds.append(CatalogProduct.is_kit.is_(True))
        elif kind == "product":
            conds.append(CatalogProduct.is_kit.is_(False))
        barcode = (filters.get("barcode") or "").strip()
        if barcode:
            sub = select(CatalogProductBarcode.product_id).where(
                CatalogProductBarcode.barcode.ilike(_like(barcode))
            )
            conds.append(CatalogProduct.id.in_(sub))
        q_text = (filters.get("q") or "").strip()
        if q_text:
            pat = _like(q_text)
            conds.append(
                or_(
                    CatalogProduct.name.ilike(pat),
                    CatalogProduct.sku.ilike(pat),
                    CatalogProduct.code.ilike(pat),
                    CatalogProduct.external_code.ilike(pat),
                )
            )
        return conds

    def get_product(self, product_id: int) -> CatalogProductRow | None:
        with Session(self.engine) as session:
            row = session.get(CatalogProduct, int(product_id))
            if row is None:
                return None
            return self._product_row(session, row, load_details=True)

    def create_product(self, data: dict[str, Any]) -> CatalogProductRow:
        return self._save_product(None, data)

    def update_product(self, product_id: int, data: dict[str, Any]) -> CatalogProductRow | None:
        with Session(self.engine) as session:
            if session.get(CatalogProduct, int(product_id)) is None:
                return None
        return self._save_product(int(product_id), data)

    def delete_product(self, product_id: int) -> bool:
        """Удаляет товар/комплект. False если не найден."""
        with Session(self.engine) as session:
            row = session.get(CatalogProduct, int(product_id))
            if row is None:
                return False
            kits = self._kits_containing_component(session, int(product_id))
            if kits:
                parts = [
                    f"«{name}» (арт. {sku}, код {code})" for _id, name, sku, code in kits
                ]
                raise ValueError(
                    "Нельзя удалить: товар входит в состав комплектов: " + "; ".join(parts)
                )
            session.delete(row)
            session.commit()
            return True

    def delete_products(self, product_ids: list[int]) -> dict[str, Any]:
        """Массовое удаление. Возвращает число удалённых и ошибки по id."""
        deleted = 0
        deleted_skus: list[str] = []
        failed: list[dict[str, Any]] = []
        seen: set[int] = set()
        for raw_id in product_ids:
            try:
                pid = int(raw_id)
            except (TypeError, ValueError):
                continue
            if pid in seen:
                continue
            seen.add(pid)
            old = self.get_product(pid)
            old_sku = str(old.sku).strip() if old and old.sku else ""
            try:
                if self.delete_product(pid):
                    deleted += 1
                    if old_sku:
                        deleted_skus.append(old_sku)
                else:
                    failed.append({"id": pid, "error": "Не найден"})
            except ValueError as exc:
                failed.append({"id": pid, "error": str(exc)})
        return {"deleted": deleted, "failed": failed, "deleted_skus": deleted_skus}

    def generate_next_product_code(self) -> str:
        with Session(self.engine) as session:
            codes = session.scalars(select(CatalogProduct.code)).all()
            max_num = 0
            found_numeric = False
            for raw in codes:
                code = str(raw or "").strip()
                if not code.isdigit():
                    continue
                found_numeric = True
                max_num = max(max_num, int(code))
            next_num = 1 if not found_numeric else max_num + 1
            return self._format_product_code(next_num)

    @staticmethod
    def _format_product_code(number: int) -> str:
        if number <= 99_999:
            return f"{number:05d}"
        return str(number)

    def _kits_containing_component(
        self, session: Session, component_product_id: int
    ) -> list[tuple[int, str, str, str]]:
        rows = session.execute(
            select(
                CatalogProduct.id,
                CatalogProduct.name,
                CatalogProduct.sku,
                CatalogProduct.code,
            )
            .join(CatalogKitComponent, CatalogKitComponent.kit_product_id == CatalogProduct.id)
            .where(CatalogKitComponent.component_product_id == int(component_product_id))
            .order_by(CatalogProduct.name)
        ).all()
        return [(int(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]

    def _save_product(self, product_id: int | None, data: dict[str, Any]) -> CatalogProductRow:
        is_kit = bool(data.get("is_kit"))
        name = str(data.get("name") or "").strip()
        sku = str(data.get("sku") or "").strip()
        code = str(data.get("code") or "").strip()
        if not name:
            raise ValueError("Название обязательно")
        if not sku:
            raise ValueError("Артикул обязателен")
        if not code:
            raise ValueError("Код обязателен")
        update_barcodes = "barcodes" in data
        barcodes_raw = data.get("barcodes") if update_barcodes else []
        if update_barcodes and not isinstance(barcodes_raw, list):
            raise ValueError("barcodes должен быть массивом")
        barcodes = self._normalize_barcodes(barcodes_raw) if update_barcodes else []
        components_raw = data.get("components") or []
        if not isinstance(components_raw, list):
            raise ValueError("components должен быть массивом")
        now = int(time.time())
        with Session(self.engine) as session:
            if product_id is None:
                if session.scalar(select(CatalogProduct.id).where(CatalogProduct.sku == sku)):
                    raise ValueError(f"Артикул «{sku}» уже используется")
                if session.scalar(select(CatalogProduct.id).where(CatalogProduct.code == code)):
                    raise ValueError(f"Код «{code}» уже используется")
                row = CatalogProduct(is_kit=is_kit, created_at_ts=now)
                session.add(row)
            else:
                row = session.get(CatalogProduct, int(product_id))
                if row is None:
                    raise ValueError("Товар не найден")
                other_sku = session.scalar(
                    select(CatalogProduct.id).where(
                        CatalogProduct.sku == sku, CatalogProduct.id != row.id
                    )
                )
                if other_sku is not None:
                    raise ValueError(f"Артикул «{sku}» уже используется")
                other_code = session.scalar(
                    select(CatalogProduct.id).where(
                        CatalogProduct.code == code, CatalogProduct.id != row.id
                    )
                )
                if other_code is not None:
                    raise ValueError(f"Код «{code}» уже используется")
                row.is_kit = is_kit
            self._apply_product_fields(row, data, sku, code, name)
            row.updated_at_ts = now
            session.flush()
            if update_barcodes:
                self._validate_barcodes_unique(session, barcodes, exclude_product_id=int(row.id))
                session.execute(
                    delete(CatalogProductBarcode).where(CatalogProductBarcode.product_id == row.id)
                )
                for i, bc in enumerate(barcodes):
                    session.add(
                        CatalogProductBarcode(
                            product_id=int(row.id),
                            barcode=bc["barcode"],
                            label=bc.get("label", ""),
                            barcode_group=bc.get("group", ""),
                            sort_order=i,
                        )
                    )
            if is_kit:
                comps = self._normalize_components(components_raw)
                self._validate_kit_graph(session, int(row.id), comps)
                session.execute(
                    delete(CatalogKitComponent).where(CatalogKitComponent.kit_product_id == row.id)
                )
                for comp in comps:
                    session.add(
                        CatalogKitComponent(
                            kit_product_id=int(row.id),
                            component_product_id=comp["component_product_id"],
                            quantity=comp["quantity"],
                        )
                    )
            else:
                session.execute(
                    delete(CatalogKitComponent).where(CatalogKitComponent.kit_product_id == row.id)
                )
            if "prices" in data:
                self._save_product_prices(session, int(row.id), data.get("prices"))
            session.commit()
            session.refresh(row)
            return self._product_row(session, row, load_details=True)

    def _apply_product_fields(
        self,
        row: CatalogProduct,
        data: dict[str, Any],
        sku: str,
        code: str,
        name: str,
    ) -> None:
        row.name = name[:512]
        row.sku = sku[:128]
        row.code = code[:64]
        row.image_url = str(data.get("image_url") or "").strip()[:2048]
        row.description = str(data.get("description") or "").strip()[:8192]
        row.group_id = _opt_int(data.get("group_id"))
        row.country = str(data.get("country") or "").strip()[:128]
        row.external_code = str(data.get("external_code") or "").strip()[:128]
        row.unit_id = _opt_int(data.get("unit_id"))
        row.weight = str(data.get("weight") or "").strip()[:32]
        row.width_mm = _parse_optional_mm(data.get("width_mm"), field_label="Ширина")
        row.height_mm = _parse_optional_mm(data.get("height_mm"), field_label="Высота")
        row.length_mm = _parse_optional_mm(data.get("length_mm"), field_label="Длина")
        row.volume_manual = bool(str(data.get("volume") or "").strip())
        row.volume = resolve_product_volume(
            data.get("volume"),
            row.width_mm,
            row.height_mm,
            row.length_mm,
            volume_manual=row.volume_manual,
        )
        row.marking_type_id = _opt_int(data.get("marking_type_id"))

    def _normalize_barcodes(self, raw: list) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw:
            parsed = _parse_barcode_item(item)
            code = parsed["barcode"]
            if code in seen:
                raise ValueError(f"Дублирующийся штрихкод в карточке: «{code}»")
            seen.add(code)
            out.append(parsed)
        return out

    def _validate_barcodes_unique(
        self, session: Session, barcodes: list[dict[str, str]], *, exclude_product_id: int
    ) -> None:
        for item in barcodes:
            code = item["barcode"]
            other = session.scalar(
                select(CatalogProductBarcode.product_id).where(
                    CatalogProductBarcode.barcode == code,
                    CatalogProductBarcode.product_id != exclude_product_id,
                )
            )
            if other is not None:
                raise ValueError(f"Штрихкод «{code}» уже используется другим товаром")

    def merge_product_barcode(
        self,
        *,
        product_id: int,
        barcode: str,
        label: str | None = None,
        group: str | None = None,
        touch_label: bool = False,
        touch_group: bool = False,
    ) -> str:
        code = _validate_code128(barcode)
        with Session(self.engine) as session:
            if session.get(CatalogProduct, int(product_id)) is None:
                raise ValueError("Товар не найден")
            existing = session.scalar(
                select(CatalogProductBarcode).where(
                    CatalogProductBarcode.product_id == int(product_id),
                    CatalogProductBarcode.barcode == code,
                )
            )
            if existing is not None:
                if touch_label:
                    existing.label = _normalize_barcode_label(label or "")
                if touch_group:
                    existing.barcode_group = _normalize_barcode_group(group or "")
                session.commit()
                return "updated"
            other = session.scalar(
                select(CatalogProductBarcode.product_id).where(
                    CatalogProductBarcode.barcode == code,
                )
            )
            if other is not None:
                raise ValueError(f"Штрихкод «{code}» уже используется другим товаром")
            max_order = session.scalar(
                select(func.max(CatalogProductBarcode.sort_order)).where(
                    CatalogProductBarcode.product_id == int(product_id)
                )
            )
            sort_order = int(max_order or -1) + 1
            session.add(
                CatalogProductBarcode(
                    product_id=int(product_id),
                    barcode=code,
                    label=_normalize_barcode_label(label or ""),
                    barcode_group=_normalize_barcode_group(group or ""),
                    sort_order=sort_order,
                )
            )
            session.commit()
            return "created"

    def _normalize_components(self, raw: list) -> list[dict[str, int]]:
        out: list[dict[str, int]] = []
        seen: set[int] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                cid = int(item.get("component_product_id"))
            except (TypeError, ValueError):
                continue
            try:
                qty = int(item.get("quantity") or 1)
            except (TypeError, ValueError):
                qty = 1
            qty = max(1, qty)
            if cid in seen:
                raise ValueError("Компонент комплекта указан дважды")
            seen.add(cid)
            out.append({"component_product_id": cid, "quantity": qty})
        return out

    def _validate_kit_graph(
        self, session: Session, kit_id: int, components: list[dict[str, int]]
    ) -> None:
        for comp in components:
            cid = comp["component_product_id"]
            if cid == kit_id:
                raise ValueError("Комплект не может включать сам себя")
            if session.get(CatalogProduct, cid) is None:
                raise ValueError(f"Компонент id={cid} не найден")
            if self._kit_contains(session, cid, kit_id):
                raise ValueError("Циклическая ссылка: комплект не может входить в сам себя через вложенность")

    def _kit_contains(self, session: Session, parent_kit_id: int, target_id: int) -> bool:
        stack = [parent_kit_id]
        visited: set[int] = set()
        while stack:
            kid = stack.pop()
            if kid in visited:
                continue
            visited.add(kid)
            rows = session.scalars(
                select(CatalogKitComponent.component_product_id).where(
                    CatalogKitComponent.kit_product_id == kid
                )
            ).all()
            for cid in rows:
                if int(cid) == int(target_id):
                    return True
                comp = session.get(CatalogProduct, int(cid))
                if comp and comp.is_kit:
                    stack.append(int(cid))
        return False

    def _product_row(
        self, session: Session, row: CatalogProduct, *, load_details: bool
    ) -> CatalogProductRow:
        group_name = ""
        if row.group_id:
            g = session.get(CatalogProductGroup, row.group_id)
            if g:
                group_name = g.name
        unit_name = ""
        if row.unit_id:
            u = session.get(CatalogUnit, row.unit_id)
            if u:
                unit_name = u.name
        marking_name = ""
        if row.marking_type_id:
            m = session.get(CatalogMarkingType, row.marking_type_id)
            if m:
                marking_name = m.name
        barcodes: list[dict[str, str]] = []
        components: list[KitComponentRow] = []
        barcode_count = int(
            session.scalar(
                select(func.count())
                .select_from(CatalogProductBarcode)
                .where(CatalogProductBarcode.product_id == row.id)
            )
            or 0
        )
        if load_details:
            bc_rows = session.scalars(
                select(CatalogProductBarcode)
                .where(CatalogProductBarcode.product_id == row.id)
                .order_by(CatalogProductBarcode.sort_order)
            ).all()
            barcodes = [
                {"barcode": b.barcode, "label": b.label or "", "group": b.barcode_group or ""}
                for b in bc_rows
            ]
            if row.is_kit:
                comp_rows = session.scalars(
                    select(CatalogKitComponent).where(CatalogKitComponent.kit_product_id == row.id)
                ).all()
                for c in comp_rows:
                    p = session.get(CatalogProduct, c.component_product_id)
                    if p is None:
                        continue
                    components.append(
                        KitComponentRow(
                            component_product_id=int(p.id),
                            component_name=p.name,
                            component_sku=p.sku,
                            component_is_kit=bool(p.is_kit),
                            quantity=int(c.quantity),
                        )
                    )
        prices: list[ProductPriceRow] = []
        if load_details:
            prices = self._product_prices(session, int(row.id))
        return CatalogProductRow(
            id=int(row.id),
            is_kit=bool(row.is_kit),
            name=row.name,
            image_url=row.image_url or "",
            description=row.description or "",
            group_id=row.group_id,
            group_name=group_name,
            country=row.country or "",
            sku=row.sku,
            code=row.code,
            external_code=row.external_code or "",
            unit_id=row.unit_id,
            unit_name=unit_name,
            weight=row.weight or "",
            width_mm=row.width_mm or "",
            height_mm=row.height_mm or "",
            length_mm=row.length_mm or "",
            volume=row.volume or "",
            volume_manual=bool(row.volume_manual),
            marking_type_id=row.marking_type_id,
            marking_type_name=marking_name,
            barcodes=barcodes,
            components=components,
            prices=prices,
            barcode_count=barcode_count,
            created_at_ts=int(row.created_at_ts),
            updated_at_ts=int(row.updated_at_ts),
        )

    def product_to_dict(self, row: CatalogProductRow, *, include_details: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": row.id,
            "is_kit": row.is_kit,
            "name": row.name,
            "image_url": row.image_url,
            "description": row.description,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "country": row.country,
            "sku": row.sku,
            "code": row.code,
            "external_code": row.external_code,
            "unit_id": row.unit_id,
            "unit_name": row.unit_name,
            "weight": row.weight,
            "width_mm": row.width_mm,
            "height_mm": row.height_mm,
            "length_mm": row.length_mm,
            "volume": row.volume,
            "volume_manual": row.volume_manual,
            "marking_type_id": row.marking_type_id,
            "marking_type_name": row.marking_type_name,
            "barcode_count": row.barcode_count,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
        }
        if include_details:
            d["barcodes"] = row.barcodes
            d["components"] = [
                {
                    "component_product_id": c.component_product_id,
                    "component_name": c.component_name,
                    "component_sku": c.component_sku,
                    "component_is_kit": c.component_is_kit,
                    "quantity": c.quantity,
                }
                for c in row.components
            ]
            d["prices"] = [
                {
                    "price_type_id": p.price_type_id,
                    "price_type_name": p.price_type_name,
                    "price": p.price,
                }
                for p in row.prices
            ]
        return d

    def _product_prices(self, session: Session, product_id: int) -> list[ProductPriceRow]:
        types = session.scalars(
            select(CrmPriceType).order_by(CrmPriceType.sort_order, CrmPriceType.name)
        ).all()
        existing = {
            int(r.price_type_id): r.price
            for r in session.scalars(
                select(CatalogProductPrice).where(CatalogProductPrice.product_id == int(product_id))
            ).all()
        }
        return [
            ProductPriceRow(
                price_type_id=int(t.id),
                price_type_name=t.name,
                price=existing.get(int(t.id)),
            )
            for t in types
        ]

    def _save_product_prices(
        self, session: Session, product_id: int, prices_raw: Any
    ) -> None:
        if prices_raw is None:
            return
        if not isinstance(prices_raw, list):
            raise ValueError("prices должен быть массивом")
        for item in prices_raw:
            if not isinstance(item, dict):
                continue
            try:
                price_type_id = int(item.get("price_type_id"))
            except (TypeError, ValueError):
                continue
            if session.get(CrmPriceType, price_type_id) is None:
                continue
            price = _parse_price(item.get("price"))
            row = session.scalar(
                select(CatalogProductPrice).where(
                    CatalogProductPrice.product_id == int(product_id),
                    CatalogProductPrice.price_type_id == price_type_id,
                )
            )
            if price is None:
                if row is not None:
                    session.delete(row)
                continue
            if row is None:
                session.add(
                    CatalogProductPrice(
                        product_id=int(product_id),
                        price_type_id=price_type_id,
                        price=price,
                    )
                )
            else:
                row.price = price

    def get_product_group_unit_costs(self, product_ids: list[int]) -> dict[int, float]:
        ids = sorted({int(x) for x in product_ids if int(x) > 0})
        if not ids:
            return {}
        with Session(self.engine) as session:
            rows = session.execute(
                select(CatalogProduct.id, CatalogProductGroup.cost)
                .outerjoin(CatalogProductGroup, CatalogProduct.group_id == CatalogProductGroup.id)
                .where(CatalogProduct.id.in_(ids))
            ).all()
        out: dict[int, float] = {}
        for product_id, cost_raw in rows:
            out[int(product_id)] = _group_cost_api_value(str(cost_raw or "")) or 0.0
        return out


def _opt_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _group_cost_api_value(raw: str) -> float | None:
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_price(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not raw:
        return None
    raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Некорректная цена: {value}") from exc
    if amount < 0:
        raise ValueError("Цена не может быть отрицательной")
    return f"{amount:.2f}"
