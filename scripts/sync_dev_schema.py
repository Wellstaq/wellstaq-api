"""
scripts/sync_dev_schema.py — ONE-OFF dev tool, not part of the app.

The local SQLite dev DB has drifted from the model definitions (several
migrations past a certain point are Postgres-only — gen_random_uuid(),
NOW() — and were never meant to run against SQLite). Rather than fight
that history, this diffs Base.metadata against the live DB and adds
whatever's missing: new tables wholesale, missing columns via ALTER
TABLE ADD COLUMN (CHECK constraints are skipped — SQLite can't ALTER
those in without a batch rebuild, and Pydantic/service-layer validation
already enforces them for new writes).

Safe to rerun — every step checks first.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import inspect, text
from app.core.database import engine
from app.models.base import Base
import app.models  # noqa: F401  — populate Base.metadata with every model


def render_type(column) -> str:
    return column.type.compile(dialect=engine.dialect)


async def main():
    async with engine.begin() as conn:
        def sync_schema(sync_conn):
            inspector = inspect(sync_conn)
            existing_tables = set(inspector.get_table_names())

            # New tables entirely: create via metadata (checkfirst skips existing ones)
            Base.metadata.create_all(sync_conn, checkfirst=True)

            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue  # just created above
                existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing_cols:
                        continue
                    ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {render_type(column)}"
                    default = column.default.arg if column.default is not None else None
                    if isinstance(default, bool):
                        ddl += f" NOT NULL DEFAULT {1 if default else 0}" if not column.nullable else f" DEFAULT {1 if default else 0}"
                    elif isinstance(default, (int, float)):
                        ddl += f" NOT NULL DEFAULT {default}" if not column.nullable else f" DEFAULT {default}"
                    elif isinstance(default, str):
                        ddl += f" NOT NULL DEFAULT '{default}'" if not column.nullable else f" DEFAULT '{default}'"
                    elif callable(default):
                        # dict/list-style callables (e.g. default=dict) — '{}' / '[]' are
                        # valid defaults for a JSON column; anything NOT NULL without a
                        # determinable scalar default is added nullable instead of
                        # crashing the whole sync over one column.
                        sample = default()
                        if isinstance(sample, dict):
                            ddl += " DEFAULT '{}'"
                        elif isinstance(sample, list):
                            ddl += " DEFAULT '[]'"
                    elif not column.nullable:
                        pass  # no safe default available — add nullable, let app code backfill on write
                    print(ddl)
                    sync_conn.execute(text(ddl))

        await conn.run_sync(sync_schema)
    print("Schema sync complete.")


if __name__ == "__main__":
    asyncio.run(main())
