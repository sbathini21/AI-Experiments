"""Emit PostgreSQL DDL for the data model:  python -m app.tools.dump_schema > migrations/schema.sql"""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from .. import models  # noqa: F401
from ..db import Base

if __name__ == "__main__":
    dialect = postgresql.dialect()
    print("-- Generated from app/models.py. PostgreSQL 15+.\n")
    for table in Base.metadata.sorted_tables:
        print(str(CreateTable(table).compile(dialect=dialect)).strip() + ";\n")
        for idx in table.indexes:
            print(str(CreateIndex(idx).compile(dialect=dialect)).strip() + ";")
        print()
