from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
Money = Annotated[int, Field(strict=True, gt=0, le=10**14)]  # INR paise
Label = Annotated[str, Field(min_length=1, max_length=120)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatasetRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Account(DatasetRecord):
    id: Identifier
    name: Label
    device: Identifier | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_account(cls, value):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        if "id" not in out and "account_id" in out:
            out["id"] = out["account_id"]
        if not out.get("name"):
            if out.get("account_name"):
                out["name"] = out["account_name"]
            elif out.get("institution") and out.get("id"):
                out["name"] = f"{out['institution']} · {out['id']}"[:120]
            elif out.get("id") is not None:
                out["name"] = str(out["id"])[:120]
        if "device" not in out and "device_id" in out:
            out["device"] = out["device_id"]
        if isinstance(out.get("device"), str) and not out["device"].strip():
            out["device"] = None
        return out


class Dated(DatasetRecord):
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def require_offset(cls, value):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Transfer(Dated):
    id: Identifier
    sender: Identifier
    receiver: Identifier
    amount_paise: Money

    @model_validator(mode="before")
    @classmethod
    def normalize_transfer(cls, value):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        for dst, aliases in (
            ("id", ("txn_id", "transaction_id")),
            ("sender", ("sender_id", "from_account", "from")),
            ("receiver", ("receiver_id", "to_account", "to")),
        ):
            if dst not in out:
                for alias in aliases:
                    if alias in out:
                        out[dst] = out[alias]
                        break
        if "amount_paise" not in out and "amount" in out:
            try:
                out["amount_paise"] = round(float(out["amount"]) * 100)
            except (TypeError, ValueError):
                pass
        return out

    @model_validator(mode="after")
    def different_accounts(self):
        if self.sender == self.receiver:
            raise ValueError("Sender and receiver must be distinct accounts")
        return self


class Complaint(Dated):
    id: Identifier
    account: Identifier
    category: Label
    transfer: Identifier | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_complaint(cls, value):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        for dst, aliases in (
            ("id", ("report_id", "complaint_id")),
            ("account", ("account_id",)),
            ("category", ("outcome", "type", "reason")),
            ("transfer", ("txn_id", "transaction_id")),
            ("timestamp", ("created", "reported_at")),
        ):
            if dst not in out:
                for alias in aliases:
                    if alias in out:
                        out[dst] = out[alias]
                        break
        if isinstance(out.get("transfer"), str) and not out["transfer"].strip():
            out["transfer"] = None
        return out


class Dataset(DatasetRecord):
    name: Label = "Imported ledger"
    accounts: list[Account] = Field(min_length=1, max_length=3000)
    transactions: list[Transfer] = Field(max_length=20000)
    complaints: list[Complaint] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="before")
    @classmethod
    def normalize_dataset(cls, value):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        if "transactions" not in out and "transfers" in out:
            out["transactions"] = out["transfers"]
        if "complaints" not in out and "fraud_reports" in out:
            out["complaints"] = out["fraud_reports"]
        txs = out.get("transactions")
        comps = out.get("complaints")
        if isinstance(txs, list) and isinstance(comps, list) and comps:
            by_id = {}
            for t in txs:
                if isinstance(t, dict):
                    tid = t.get("id", t.get("txn_id", t.get("transaction_id")))
                    if tid:
                        by_id[tid] = t
            fixed = []
            for c in comps:
                if isinstance(c, dict):
                    c = dict(c)
                    tid = c.get("transfer", c.get("txn_id", c.get("transaction_id")))
                    tx = by_id.get(tid) if tid else None
                    if tx:
                        if "account" not in c and "account_id" not in c:
                            c["account"] = tx.get(
                                "receiver",
                                tx.get("receiver_id", tx.get("to_account", tx.get("to"))),
                            )
                        if (
                            "timestamp" not in c
                            and "created" not in c
                            and "reported_at" not in c
                        ):
                            c["timestamp"] = tx.get("timestamp")
                fixed.append(c)
            out["complaints"] = fixed
        return out

    @field_validator("name", mode="before")
    @classmethod
    def default_name(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return "Imported ledger"
        return value

    @model_validator(mode="after")
    def check_links(self):
        for rows in (self.accounts, self.transactions, self.complaints):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("Duplicate ID within a record type")
        accounts = {a.id for a in self.accounts}
        transfers = {t.id: t for t in self.transactions}
        for t in self.transactions:
            if t.sender not in accounts or t.receiver not in accounts:
                raise ValueError(f"Unknown account in transfer {t.id}")
        for c in self.complaints:
            if c.account not in accounts:
                raise ValueError(f"Unknown account in complaint {c.id}")
            if c.transfer is not None:
                t = transfers.get(c.transfer)
                if not t or c.account not in (t.sender, t.receiver):
                    raise ValueError(f"Unrelated transfer in complaint {c.id}")
        return self


class CaseUpdate(Record):
    event_id: Identifier
    expected_revision: int = Field(ge=0, strict=True)
    analyst: Label
    status: Literal["Investigating", "Escalated", "Closed"]
    note: Annotated[str, Field(min_length=4, max_length=1000)]
