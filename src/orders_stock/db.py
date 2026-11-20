import argparse
import os
from dataclasses import dataclass
from importlib.resources import files
from typing import Self

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


@dataclass(frozen=True)
class Database:
    DEFAULT_DATABASE_URL = "postgresql://orders:orders@127.0.0.1:54329/orders"

    database_url: str

    @classmethod
    def from_env(cls) -> Self:
        return cls(os.environ.get("DATABASE_URL", Database.DEFAULT_DATABASE_URL))

    def connect(self) -> psycopg.Connection:
        # Keep existing connection options when adding the UTC timezone.
        options = conninfo_to_dict(self.database_url).get("options", "")
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=5,
            options=f"{options} -c timezone=UTC",
        )

    def initialize(self) -> None:
        self._execute_sql(["schema.sql"])

    def seed_products(self) -> None:
        """Add sample products."""
        self._execute_sql(["seed.sql"])

    def reset(self) -> None:
        """Drop and recreate tables, then restore sample products."""
        self._execute_sql(["drop.sql", "schema.sql", "seed.sql"])

    def _execute_sql(self, filenames: list[str]) -> None:
        """
        Execute SQL files in order within one transaction.

        Roll back all statements if any file cannot be read or executed.

        Raises:
            OSError: If a SQL file cannot be read.
            psycopg.Error: If the connection or transaction fails.
        """
        sql_dir = files("orders_stock").joinpath("sql")
        with self.connect() as conn:
            for filename in filenames:
                conn.execute(sql_dir.joinpath(filename).read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the Orders and Stock database")
    parser.add_argument("command", choices=["init", "seed", "reset"])
    args = parser.parse_args()
    db = Database.from_env()
    if args.command == "init":
        db.initialize()
        print("Database tables and indexes are ready.")
    elif args.command == "seed":
        db.seed_products()
        print("Sample products added; existing products were left unchanged.")
    else:
        db.reset()
        print("Database tables recreated and sample products restored.")


if __name__ == "__main__":
    main()
