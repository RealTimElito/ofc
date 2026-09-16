"""External database connectors for results and example reports."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Result

from app.models import DbConnection, SavedQuery
from app.services.crypto import decrypt_secret

# Only allow SELECT/WITH — air-gapped report tooling should be read-only.
_FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "attach",
    "pragma",
    "replace",
    "merge",
    "call",
    "exec",
)


def assert_readonly_sql(sql: str) -> None:
    normalized = " ".join(sql.strip().lower().split())
    if not (normalized.startswith("select") or normalized.startswith("with")):
        raise ValueError("Only SELECT / WITH queries are allowed")
    # Strip simple string literals before keyword scan
    scrubbed = normalized
    for quote in ("'", '"'):
        parts = scrubbed.split(quote)
        scrubbed = " ".join(parts[i] for i in range(0, len(parts), 2))
    for word in _FORBIDDEN:
        if f" {word} " in f" {scrubbed} " or scrubbed.startswith(f"{word} "):
            raise ValueError(f"Forbidden SQL keyword: {word}")


def build_engine(connection: DbConnection) -> Engine:
    dsn = decrypt_secret(connection.dsn_enc)
    dialect = connection.dialect.lower()
    if dialect == "sqlite":
        # Accept path or full sqlite URL
        if dsn.startswith("sqlite:"):
            url = dsn
        else:
            url = f"sqlite:///{dsn}"
        return create_engine(url)
    if dialect == "postgresql":
        if not dsn.startswith("postgresql"):
            raise ValueError("PostgreSQL DSN must start with postgresql:// or postgresql+psycopg2://")
        return create_engine(dsn)
    if dialect == "mysql":
        if dsn.startswith("mysql"):
            url = dsn
        else:
            raise ValueError("MySQL DSN must start with mysql+pymysql://")
        return create_engine(url)
    raise ValueError(f"Unsupported dialect: {dialect}")


def run_query(connection: DbConnection, sql: str, limit: int = 500) -> dict[str, Any]:
    assert_readonly_sql(sql)
    engine = build_engine(connection)
    with engine.connect() as conn:
        result: Result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = []
        for i, row in enumerate(result):
            if i >= limit:
                break
            rows.append({col: _jsonable(row._mapping[col]) for col in columns})
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": len(rows) >= limit}


def query_as_markdown(connection: DbConnection, saved: SavedQuery, limit: int = 200) -> str:
    data = run_query(connection, saved.sql_text, limit=limit)
    lines = [f"### Query: {saved.name}", f"_purpose: {saved.purpose}_", ""]
    if not data["rows"]:
        lines.append("_(no rows)_")
        return "\n".join(lines)

    if saved.purpose in ("examples", "both") and saved.example_body_column:
        col = saved.example_body_column
        for i, row in enumerate(data["rows"], start=1):
            body = row.get(col, "")
            meta = {k: v for k, v in row.items() if k != col}
            lines.append(f"#### Example report {i}")
            if meta:
                lines.append(f"Metadata: `{json.dumps(meta, default=str)}`")
            lines.append("")
            lines.append(str(body))
            lines.append("")
        return "\n".join(lines)

    # Tabular results
    cols = data["columns"]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in data["rows"]:
        cells = [str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    if data["truncated"]:
        lines.append("")
        lines.append(f"_Truncated to {limit} rows._")
    return "\n".join(lines)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
