"""Каталог товаров и комплектов новой панели /warehouse."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, delete, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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
    volume: Mapped[str] = mapped_column(String(32), nullable=False, default="")
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


@dataclass
class KitComponentRow:
    component_product_id: int
    component_name: str
    component_sku: str
    component_is_kit: bool
    quantity: int


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
    volume: str
    marking_type_id: Optional[int]
    marking_type_name: str
    barcodes: list[str] = field(default_factory=list)
    components: list[KitComponentRow] = field(default_factory=list)
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


class CatalogRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._seed_defaults()

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
            "groups": [{"id": g.id, "name": g.name, "sort_order": g.sort_order} for g in groups],
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

    def save_groups(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_dict(CatalogProductGroup, items, "groups")

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
                    row = model(name=name, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
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
                return [{"id": r.id, "name": r.name, "sort_order": r.sort_order} for r in rows]
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "sort_order": r.sort_order,
                    "is_default": bool(getattr(r, "is_default", False)),
                }
                for r in rows
            ]

    def list_products(self, filters: dict[str, str]) -> list[CatalogProductRow]:
        with Session(self.engine) as session:
            q = select(CatalogProduct).order_by(CatalogProduct.name, CatalogProduct.sku)
            conds = self._filter_conditions(session, filters)
            if conds:
                q = q.where(*conds)
            rows = session.scalars(q).all()
            return [self._product_row(session, r, load_details=False) for r in rows]

    def list_products_picker(self, *, q: str = "", exclude_id: int | None = None) -> list[dict[str, Any]]:
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
            rows = session.scalars(stmt.limit(200)).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "sku": r.sku,
                    "code": r.code,
                    "is_kit": bool(r.is_kit),
                }
                for r in rows
            ]

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
        barcodes_raw = data.get("barcodes") or []
        if not isinstance(barcodes_raw, list):
            raise ValueError("barcodes должен быть массивом")
        barcodes = self._normalize_barcodes(barcodes_raw)
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
            self._validate_barcodes_unique(session, barcodes, exclude_product_id=int(row.id))
            session.execute(
                delete(CatalogProductBarcode).where(CatalogProductBarcode.product_id == row.id)
            )
            for i, bc in enumerate(barcodes):
                session.add(
                    CatalogProductBarcode(product_id=int(row.id), barcode=bc, sort_order=i)
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
        row.volume = str(data.get("volume") or "").strip()[:32]
        row.marking_type_id = _opt_int(data.get("marking_type_id"))

    def _normalize_barcodes(self, raw: list) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            code = _validate_code128(str(item or ""))
            if code in seen:
                raise ValueError(f"Дублирующийся штрихкод в карточке: «{code}»")
            seen.add(code)
            out.append(code)
        return out

    def _validate_barcodes_unique(
        self, session: Session, barcodes: list[str], *, exclude_product_id: int
    ) -> None:
        for code in barcodes:
            other = session.scalar(
                select(CatalogProductBarcode.product_id).where(
                    CatalogProductBarcode.barcode == code,
                    CatalogProductBarcode.product_id != exclude_product_id,
                )
            )
            if other is not None:
                raise ValueError(f"Штрихкод «{code}» уже используется другим товаром")

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
        barcodes: list[str] = []
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
            barcodes = [b.barcode for b in bc_rows]
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
            volume=row.volume or "",
            marking_type_id=row.marking_type_id,
            marking_type_name=marking_name,
            barcodes=barcodes,
            components=components,
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
            "volume": row.volume,
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
        return d


def _opt_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
