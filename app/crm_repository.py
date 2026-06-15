"""CRM: контрагенты, справочники статусов, групп, типов и видов цен."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, delete, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

_DEFAULT_STATUSES = (
    ("Новый", "#f9a825"),
    ("Старый", "#1565c0"),
)
_DEFAULT_TYPES = (
    "Юридическое лицо",
    "Физическое лицо",
    "Индивидуальный предприниматель",
)


class _Base(DeclarativeBase):
    pass


class CrmCounterpartyStatus(_Base):
    __tablename__ = "crm_counterparty_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#9e9e9e")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CrmCounterpartyGroup(_Base):
    __tablename__ = "crm_counterparty_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CrmCounterpartyType(_Base):
    __tablename__ = "crm_counterparty_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CrmPriceType(_Base):
    __tablename__ = "crm_price_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CrmCounterparty(_Base):
    __tablename__ = "crm_counterparties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status_id: Mapped[int] = mapped_column(Integer, ForeignKey("crm_counterparty_statuses.id"), nullable=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("crm_counterparty_groups.id"), nullable=True)
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    type_id: Mapped[int] = mapped_column(Integer, ForeignKey("crm_counterparty_types.id"), nullable=True)
    inn: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    legal_address: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    address_comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    fias_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    kpp: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ogrn: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    okpo: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    price_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("crm_price_types.id"), nullable=True)
    discount_card_number: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    contact_persons = relationship(
        "CrmContactPerson",
        back_populates="counterparty",
        cascade="all, delete-orphan",
        order_by="CrmContactPerson.sort_order",
    )


class CrmContactPerson(_Base):
    __tablename__ = "crm_contact_persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    counterparty_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("crm_counterparties.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    position: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    counterparty = relationship("CrmCounterparty", back_populates="contact_persons")


@dataclass
class ContactPersonRow:
    id: Optional[int]
    full_name: str
    position: str
    phone: str
    email: str
    comment: str
    sort_order: int = 0


@dataclass
class CounterpartyRow:
    id: int
    status_id: Optional[int]
    group_id: Optional[int]
    phone: str
    email: str
    type_id: Optional[int]
    inn: str
    full_name: str
    legal_address: str
    address_comment: str
    fias_code: str
    kpp: str
    ogrn: str
    okpo: str
    price_type_id: Optional[int]
    discount_card_number: str
    created_at_ts: int
    updated_at_ts: int
    contact_persons: list[ContactPersonRow] = field(default_factory=list)
    status_name: str = ""
    status_color: str = ""
    group_name: str = ""
    type_name: str = ""
    price_type_name: str = ""


def _like(pattern: str) -> str:
    p = pattern.strip()
    if not p:
        return ""
    return f"%{p}%"


class CrmRepository:
    def __init__(self, db_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(db_url, future=True)

    def init_schema(self) -> None:
        _Base.metadata.create_all(self.engine)
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        with Session(self.engine) as session:
            if not session.scalar(select(func.count()).select_from(CrmCounterpartyStatus)):
                for i, (name, color) in enumerate(_DEFAULT_STATUSES):
                    session.add(
                        CrmCounterpartyStatus(
                            name=name, color=color, sort_order=i, is_default=True
                        )
                    )
            if not session.scalar(select(func.count()).select_from(CrmCounterpartyType)):
                for i, name in enumerate(_DEFAULT_TYPES):
                    session.add(
                        CrmCounterpartyType(name=name, sort_order=i, is_default=True)
                    )
            session.commit()

    def get_meta(self) -> dict[str, list[dict[str, Any]]]:
        with Session(self.engine) as session:
            statuses = session.scalars(
                select(CrmCounterpartyStatus).order_by(
                    CrmCounterpartyStatus.sort_order, CrmCounterpartyStatus.name
                )
            ).all()
            groups = session.scalars(
                select(CrmCounterpartyGroup).order_by(
                    CrmCounterpartyGroup.sort_order, CrmCounterpartyGroup.name
                )
            ).all()
            types = session.scalars(
                select(CrmCounterpartyType).order_by(
                    CrmCounterpartyType.sort_order, CrmCounterpartyType.name
                )
            ).all()
            price_types = session.scalars(
                select(CrmPriceType).order_by(CrmPriceType.sort_order, CrmPriceType.name)
            ).all()
        return {
            "statuses": [self._status_dict(s) for s in statuses],
            "groups": [self._group_dict(g) for g in groups],
            "types": [self._type_dict(t) for t in types],
            "price_types": [self._price_type_dict(p) for p in price_types],
        }

    def _status_dict(self, row: CrmCounterpartyStatus) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "color": row.color,
            "sort_order": row.sort_order,
            "is_default": bool(row.is_default),
        }

    def _group_dict(self, row: CrmCounterpartyGroup) -> dict[str, Any]:
        return {"id": row.id, "name": row.name, "sort_order": row.sort_order}

    def _type_dict(self, row: CrmCounterpartyType) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "sort_order": row.sort_order,
            "is_default": bool(row.is_default),
        }

    def _price_type_dict(self, row: CrmPriceType) -> dict[str, Any]:
        return {"id": row.id, "name": row.name, "sort_order": row.sort_order}

    def save_statuses(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {s.id: s for s in session.scalars(select(CrmCounterpartyStatus)).all()}
            keep_ids: set[int] = set()
            for i, item in enumerate(items):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                color = str(item.get("color") or "#9e9e9e").strip() or "#9e9e9e"
                raw_id = item.get("id")
                row = None
                if raw_id is not None:
                    try:
                        row = existing.get(int(raw_id))
                    except (TypeError, ValueError):
                        row = None
                if row is None:
                    row = CrmCounterpartyStatus(name=name, color=color, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.color = color
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for sid, row in existing.items():
                if sid not in keep_ids and not row.is_default:
                    session.delete(row)
            session.commit()
            rows = session.scalars(
                select(CrmCounterpartyStatus).order_by(
                    CrmCounterpartyStatus.sort_order, CrmCounterpartyStatus.name
                )
            ).all()
            return [self._status_dict(r) for r in rows]

    def save_groups(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {g.id: g for g in session.scalars(select(CrmCounterpartyGroup)).all()}
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
                    row = CrmCounterpartyGroup(name=name, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for gid, row in existing.items():
                if gid not in keep_ids:
                    session.delete(row)
            session.commit()
            rows = session.scalars(
                select(CrmCounterpartyGroup).order_by(
                    CrmCounterpartyGroup.sort_order, CrmCounterpartyGroup.name
                )
            ).all()
            return [self._group_dict(r) for r in rows]

    def save_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {t.id: t for t in session.scalars(select(CrmCounterpartyType)).all()}
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
                    row = CrmCounterpartyType(name=name, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for tid, row in existing.items():
                if tid not in keep_ids and not row.is_default:
                    session.delete(row)
            session.commit()
            rows = session.scalars(
                select(CrmCounterpartyType).order_by(
                    CrmCounterpartyType.sort_order, CrmCounterpartyType.name
                )
            ).all()
            return [self._type_dict(r) for r in rows]

    def save_price_types(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            existing = {p.id: p for p in session.scalars(select(CrmPriceType)).all()}
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
                    row = CrmPriceType(name=name, sort_order=i)
                    session.add(row)
                else:
                    row.name = name
                    row.sort_order = i
                session.flush()
                keep_ids.add(int(row.id))
            for pid, row in existing.items():
                if pid not in keep_ids:
                    session.delete(row)
            session.commit()
            rows = session.scalars(
                select(CrmPriceType).order_by(CrmPriceType.sort_order, CrmPriceType.name)
            ).all()
            return [self._price_type_dict(r) for r in rows]

    def list_counterparties(self, filters: dict[str, str]) -> list[CounterpartyRow]:
        with Session(self.engine) as session:
            q = select(CrmCounterparty).order_by(
                CrmCounterparty.updated_at_ts.desc(), CrmCounterparty.id.desc()
            )
            conditions = self._filter_conditions(filters)
            if conditions:
                q = q.where(*conditions)
            rows = session.scalars(q).all()
            return [self._counterparty_row(session, r, load_contacts=False) for r in rows]

    def _filter_conditions(self, filters: dict[str, str]) -> list:
        conds = []
        mapping = {
            "phone": CrmCounterparty.phone,
            "email": CrmCounterparty.email,
            "inn": CrmCounterparty.inn,
            "full_name": CrmCounterparty.full_name,
            "legal_address": CrmCounterparty.legal_address,
            "address_comment": CrmCounterparty.address_comment,
            "fias_code": CrmCounterparty.fias_code,
            "kpp": CrmCounterparty.kpp,
            "ogrn": CrmCounterparty.ogrn,
            "okpo": CrmCounterparty.okpo,
            "discount_card_number": CrmCounterparty.discount_card_number,
        }
        for key, col in mapping.items():
            pat = _like(filters.get(key, ""))
            if pat:
                conds.append(col.ilike(pat))
        for key, col in (
            ("status_id", CrmCounterparty.status_id),
            ("group_id", CrmCounterparty.group_id),
            ("type_id", CrmCounterparty.type_id),
            ("price_type_id", CrmCounterparty.price_type_id),
        ):
            raw = (filters.get(key) or "").strip()
            if raw:
                try:
                    conds.append(col == int(raw))
                except ValueError:
                    pass
        q_text = (filters.get("q") or "").strip()
        if q_text:
            pat = _like(q_text)
            conds.append(
                or_(
                    CrmCounterparty.full_name.ilike(pat),
                    CrmCounterparty.phone.ilike(pat),
                    CrmCounterparty.email.ilike(pat),
                    CrmCounterparty.inn.ilike(pat),
                )
            )
        contact_q = (filters.get("contact") or "").strip()
        if contact_q:
            pat = _like(contact_q)
            sub = select(CrmContactPerson.counterparty_id).where(
                or_(
                    CrmContactPerson.full_name.ilike(pat),
                    CrmContactPerson.phone.ilike(pat),
                    CrmContactPerson.email.ilike(pat),
                    CrmContactPerson.position.ilike(pat),
                )
            )
            conds.append(CrmCounterparty.id.in_(sub))
        return conds

    def get_counterparty(self, counterparty_id: int) -> CounterpartyRow | None:
        with Session(self.engine) as session:
            row = session.get(CrmCounterparty, int(counterparty_id))
            if row is None:
                return None
            return self._counterparty_row(session, row, load_contacts=True)

    def create_counterparty(self, data: dict[str, Any]) -> CounterpartyRow:
        now = int(time.time())
        with Session(self.engine) as session:
            row = self._apply_counterparty_fields(CrmCounterparty(), data)
            row.created_at_ts = now
            row.updated_at_ts = now
            session.add(row)
            session.flush()
            self._sync_contacts(session, row, data.get("contact_persons") or [])
            session.commit()
            session.refresh(row)
            return self._counterparty_row(session, row, load_contacts=True)

    def update_counterparty(self, counterparty_id: int, data: dict[str, Any]) -> CounterpartyRow | None:
        with Session(self.engine) as session:
            row = session.get(CrmCounterparty, int(counterparty_id))
            if row is None:
                return None
            self._apply_counterparty_fields(row, data)
            row.updated_at_ts = int(time.time())
            self._sync_contacts(session, row, data.get("contact_persons") or [])
            session.commit()
            session.refresh(row)
            return self._counterparty_row(session, row, load_contacts=True)

    def _apply_counterparty_fields(self, row: CrmCounterparty, data: dict[str, Any]) -> CrmCounterparty:
        row.status_id = _opt_int(data.get("status_id"))
        row.group_id = _opt_int(data.get("group_id"))
        row.phone = str(data.get("phone") or "").strip()[:64]
        row.email = str(data.get("email") or "").strip()[:256]
        row.type_id = _opt_int(data.get("type_id"))
        row.inn = str(data.get("inn") or "").strip()[:32]
        row.full_name = str(data.get("full_name") or "").strip()[:512]
        row.legal_address = str(data.get("legal_address") or "").strip()[:512]
        row.address_comment = str(data.get("address_comment") or "").strip()[:512]
        row.fias_code = str(data.get("fias_code") or "").strip()[:64]
        row.kpp = str(data.get("kpp") or "").strip()[:32]
        row.ogrn = str(data.get("ogrn") or "").strip()[:32]
        row.okpo = str(data.get("okpo") or "").strip()[:32]
        row.price_type_id = _opt_int(data.get("price_type_id"))
        row.discount_card_number = str(data.get("discount_card_number") or "").strip()[:64]
        return row

    def _sync_contacts(
        self, session: Session, row: CrmCounterparty, contacts: list[dict[str, Any]]
    ) -> None:
        session.execute(delete(CrmContactPerson).where(CrmContactPerson.counterparty_id == row.id))
        for i, c in enumerate(contacts):
            session.add(
                CrmContactPerson(
                    counterparty_id=row.id,
                    full_name=str(c.get("full_name") or "").strip()[:256],
                    position=str(c.get("position") or "").strip()[:128],
                    phone=str(c.get("phone") or "").strip()[:64],
                    email=str(c.get("email") or "").strip()[:256],
                    comment=str(c.get("comment") or "").strip()[:512],
                    sort_order=i,
                )
            )

    def _counterparty_row(
        self, session: Session, row: CrmCounterparty, *, load_contacts: bool
    ) -> CounterpartyRow:
        status_name, status_color = "", ""
        if row.status_id:
            st = session.get(CrmCounterpartyStatus, row.status_id)
            if st:
                status_name, status_color = st.name, st.color
        group_name = ""
        if row.group_id:
            gr = session.get(CrmCounterpartyGroup, row.group_id)
            if gr:
                group_name = gr.name
        type_name = ""
        if row.type_id:
            tp = session.get(CrmCounterpartyType, row.type_id)
            if tp:
                type_name = tp.name
        price_type_name = ""
        if row.price_type_id:
            pt = session.get(CrmPriceType, row.price_type_id)
            if pt:
                price_type_name = pt.name
        contacts: list[ContactPersonRow] = []
        if load_contacts:
            for c in row.contact_persons:
                contacts.append(
                    ContactPersonRow(
                        id=c.id,
                        full_name=c.full_name,
                        position=c.position,
                        phone=c.phone,
                        email=c.email,
                        comment=c.comment,
                        sort_order=c.sort_order,
                    )
                )
        return CounterpartyRow(
            id=row.id,
            status_id=row.status_id,
            group_id=row.group_id,
            phone=row.phone,
            email=row.email,
            type_id=row.type_id,
            inn=row.inn,
            full_name=row.full_name,
            legal_address=row.legal_address,
            address_comment=row.address_comment,
            fias_code=row.fias_code,
            kpp=row.kpp,
            ogrn=row.ogrn,
            okpo=row.okpo,
            price_type_id=row.price_type_id,
            discount_card_number=row.discount_card_number,
            created_at_ts=row.created_at_ts,
            updated_at_ts=row.updated_at_ts,
            contact_persons=contacts,
            status_name=status_name,
            status_color=status_color,
            group_name=group_name,
            type_name=type_name,
            price_type_name=price_type_name,
        )

    def counterparty_to_dict(self, row: CounterpartyRow, *, include_contacts: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": row.id,
            "status_id": row.status_id,
            "status_name": row.status_name,
            "status_color": row.status_color,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "phone": row.phone,
            "email": row.email,
            "type_id": row.type_id,
            "type_name": row.type_name,
            "inn": row.inn,
            "full_name": row.full_name,
            "legal_address": row.legal_address,
            "address_comment": row.address_comment,
            "fias_code": row.fias_code,
            "kpp": row.kpp,
            "ogrn": row.ogrn,
            "okpo": row.okpo,
            "price_type_id": row.price_type_id,
            "price_type_name": row.price_type_name,
            "discount_card_number": row.discount_card_number,
            "created_at_ts": row.created_at_ts,
            "updated_at_ts": row.updated_at_ts,
        }
        if include_contacts:
            d["contact_persons"] = [
                {
                    "id": c.id,
                    "full_name": c.full_name,
                    "position": c.position,
                    "phone": c.phone,
                    "email": c.email,
                    "comment": c.comment,
                    "sort_order": c.sort_order,
                }
                for c in row.contact_persons
            ]
        return d


def _opt_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
