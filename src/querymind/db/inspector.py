"""Automatic SQLite schema discovery using the trusted inspection engine."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, inspect

from querymind.db.engine import create_inspection_engine
from querymind.domain.exceptions import (
    DatabaseConnectionError,
    NoDatabaseConnectedError,
    SchemaDiscoveryError,
)
from querymind.domain.models import ColumnInfo, ForeignKeyInfo, SchemaSnapshot, TableInfo


class SchemaInspector:
    """Discovers and caches structural database metadata without reading table rows."""

    def __init__(self, db_path: Path | None, engine: Engine | None = None) -> None:
        self._db_path = db_path
        self._engine = engine
        self._cached_snapshot: SchemaSnapshot | None = None

    @property
    def db_path(self) -> Path | None:
        """Return the active database path, if any."""
        return self._db_path

    def set_database(self, db_path: Path | None) -> None:
        """Switch or disconnect the inspected database and clear cached metadata."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        self._db_path = db_path
        self._cached_snapshot = None

    def get_schema(self, *, force_refresh: bool = False) -> SchemaSnapshot:
        """Return the cached ``SchemaSnapshot`` or inspect the database automatically."""
        if self._cached_snapshot is not None and not force_refresh:
            return self._cached_snapshot

        if self._engine is None and self._db_path is None:
            raise NoDatabaseConnectedError(
                "No database is currently connected.",
                user_message="No database connected. Please upload and connect a SQLite database.",
            )

        engine = self._engine or create_inspection_engine(self._db_path)  # type: ignore[arg-type]
        try:
            inspector = inspect(engine)
            table_names = sorted(
                name
                for name in inspector.get_table_names()
                if not name.lower().startswith("sqlite_")
            )
            tables: list[TableInfo] = []
            for table_name in table_names:
                pk_info = inspector.get_pk_constraint(table_name)
                pk_cols = {c.lower() for c in pk_info.get("constrained_columns", [])}

                columns = tuple(
                    ColumnInfo(
                        name=col["name"],
                        data_type=str(col["type"]),
                        nullable=bool(col.get("nullable", True)),
                        primary_key=col["name"].lower() in pk_cols,
                    )
                    for col in inspector.get_columns(table_name)
                )

                fks: list[ForeignKeyInfo] = []
                for fk in inspector.get_foreign_keys(table_name):
                    referred_table = fk.get("referred_table")
                    constrained = fk.get("constrained_columns") or []
                    referred = fk.get("referred_columns") or []
                    if referred_table and len(constrained) == len(referred):
                        for src_col, ref_col in zip(constrained, referred, strict=True):
                            fks.append(
                                ForeignKeyInfo(
                                    column=src_col,
                                    referred_table=referred_table,
                                    referred_column=ref_col,
                                )
                            )

                tables.append(
                    TableInfo(
                        name=table_name,
                        columns=columns,
                        foreign_keys=tuple(fks),
                    )
                )

            snapshot = SchemaSnapshot(dialect=engine.dialect.name, tables=tuple(tables))
            self._cached_snapshot = snapshot
            return snapshot
        except DatabaseConnectionError:
            raise
        except Exception as exc:
            raise SchemaDiscoveryError(
                f"Failed to inspect database schema: {exc}",
                user_message="Unable to inspect database schema.",
            ) from exc
        finally:
            if self._engine is None:
                engine.dispose()
