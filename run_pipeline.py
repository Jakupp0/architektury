#!/usr/bin/env python3
"""Run the complete staging, warehouse, analytics, and QA pipeline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "kolej.duckdb"
REQUIRED_DW_TABLES = (
    "Mostek_Stacji",
    "Dim_Stacja",
    "Dim_Przewoznik",
    "Dim_Kurs",
    "Dim_Czas",
    "Dim_Miesiac",
    "Dim_Pora_Doby",
    "Fakt_Zatrzymanie",
    "Fakt_Przesiadka",
    "Fakt_Zatrzymania_UTK",
)
SCHEMA_TABLES = ("Dim_Czas", "Dim_Miesiac", "Dim_Pora_Doby")
STEPS = (
    ROOT / "01_extract_staging.py",
    ROOT / "02_create_dw_schema.py",
    ROOT / "03_load_dw.py",
    ROOT / "05_analytics_and_security.sql",
    ROOT / "04_qa_tests.py",
)


def static_dimensions_are_ready() -> bool:
    import duckdb

    if not DATABASE.is_file():
        return False

    connection = duckdb.connect(str(DATABASE), read_only=True)
    try:
        existing_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'dw'
                """
            ).fetchall()
        }
        if not set(REQUIRED_DW_TABLES).issubset(existing_tables):
            return False

        counts = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM dw."{table}"'
            ).fetchone()[0]
            for table in SCHEMA_TABLES
        }
        return (
            counts["Dim_Czas"] > 0
            and counts["Dim_Miesiac"] == 12
            and counts["Dim_Pora_Doby"] == 30
        )
    finally:
        connection.close()


def run_script(path: Path) -> None:
    print(f"\n==> {path.name}", flush=True)
    subprocess.run([sys.executable, str(path)], cwd=ROOT, check=True)


def apply_analytics_sql(path: Path) -> None:
    import duckdb

    print(f"\n==> {path.name}", flush=True)
    connection = duckdb.connect(str(DATABASE))
    try:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Uruchamia pełny pipeline ETL, analitykę i testy jakości."
    )
    parser.parse_args()

    missing = [path.name for path in STEPS if not path.is_file()]
    if missing:
        parser.error(f"Brak wymaganych plików: {', '.join(missing)}")

    print("Uruchamianie pipeline'u kolej.duckdb.", flush=True)
    try:
        run_script(STEPS[0])

        if static_dimensions_are_ready():
            print(
                "\n==> 02_create_dw_schema.py pominięto "
                "(wymiary statyczne są już przygotowane).",
                flush=True,
            )
        else:
            run_script(STEPS[1])

        run_script(STEPS[2])
        apply_analytics_sql(STEPS[3])
        run_script(STEPS[4])
    except subprocess.CalledProcessError as error:
        print(
            f"\nPipeline przerwany: {error.cmd[-1]} zakończył się kodem "
            f"{error.returncode}.",
            file=sys.stderr,
        )
        return error.returncode or 1
    except Exception as error:
        print(f"\nPipeline przerwany: {error}", file=sys.stderr)
        return 1

    print("\nPipeline zakończony poprawnie.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
