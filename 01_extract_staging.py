#!/usr/bin/env python3
"""Extract GTFS and UTK source files into an auditable DuckDB staging layer."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "kolej.duckdb"

GTFS_TABLES = {
    "stop_times.txt": "stg_stop_times",
    "trips.txt": "stg_trips",
    "stops.txt": "stg_stops",
    "calendar_dates.txt": "stg_calendar_dates",
    "routes.txt": "stg_routes",
    "agency.txt": "stg_agency",
}
UTK_PATTERNS = {
    "stg_utk_wymiana": "*informacje*.xlsx",
    "stg_utk_zatrzymania": "*Liczba_zatrzyman*.xlsx",
}


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_source_files() -> tuple[dict[str, Path], dict[str, Path]]:
    gtfs_roots = [ROOT / "raw" / "gtfs", ROOT / "polish_trains"]
    utk_roots = [ROOT / "raw" / "utk", ROOT]
    gtfs_root = next((path for path in gtfs_roots if path.is_dir()), None)
    if gtfs_root is None:
        raise FileNotFoundError("Nie znaleziono katalogu raw/gtfs ani polish_trains.")

    gtfs_files: dict[str, Path] = {}
    for filename in GTFS_TABLES:
        path = gtfs_root / filename
        if not path.is_file():
            raise FileNotFoundError(f"Brak wymaganego pliku GTFS: {path}")
        gtfs_files[filename] = path

    utk_root = next((path for path in utk_roots if path.is_dir()), None)
    if utk_root is None:
        raise FileNotFoundError("Nie znaleziono katalogu raw/utk ani plików UTK w katalogu projektu.")
    utk_files: dict[str, Path] = {}
    for table, pattern in UTK_PATTERNS.items():
        matches = sorted(utk_root.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"Nie znaleziono pliku UTK pasującego do {utk_root / pattern}")
        utk_files[table] = matches[0]
    return gtfs_files, utk_files


def source_name(path: Path) -> str:
    return str(path.relative_to(ROOT))


def already_loaded(connection: duckdb.DuckDBPyConnection, path: Path, digest: str) -> bool:
    return connection.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM stg_etl_load_log
            WHERE nazwa_pliku = ? AND hash_sha256 = ? AND status = 'SUCCESS'
        )
        """,
        [source_name(path), digest],
    ).fetchone()[0]


def write_load_log(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    digest: str,
    row_count: int,
    status: str,
) -> None:
    next_id = connection.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM stg_etl_load_log"
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO stg_etl_load_log
        (id, nazwa_pliku, hash_sha256, data_zaladowania, liczba_wierszy, status)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
        [next_id, source_name(path), digest, row_count, status],
    )


def trim_text_columns(connection: duckdb.DuckDBPyConnection, table: str) -> str:
    description = connection.execute(f"DESCRIBE {quote_identifier(table)}").fetchall()
    columns = []
    for name, data_type, *_ in description:
        quoted = quote_identifier(name)
        if data_type.upper().startswith(("VARCHAR", "CHAR", "TEXT", "STRING")):
            columns.append(f"TRIM({quoted}) AS {quoted}")
        else:
            columns.append(quoted)
    return ", ".join(columns)


def load_gtfs_file(
    connection: duckdb.DuckDBPyConnection, path: Path, target_table: str
) -> None:
    digest = sha256_file(path)
    if already_loaded(connection, path, digest):
        print(f"POMINIĘTO (bez zmian): {source_name(path)}")
        return

    raw_table = f"{target_table}_source"
    filename = quote_literal(path.as_posix())
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE {quote_identifier(raw_table)} AS
            SELECT * FROM read_csv_auto(
                {filename}, header = true, all_varchar = true,
                sample_size = -1, nullstr = ''
            )
            """
        )
        row_count = connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(raw_table)}"
        ).fetchone()[0]
        connection.execute("COMMIT")
        write_load_log(connection, path, digest, row_count, "SUCCESS")
        print(f"ZAŁADOWANO: {source_name(path)} ({row_count:,} wierszy)")
    except Exception:
        connection.execute("ROLLBACK")
        write_load_log(connection, path, digest, 0, "FAILED")
        raise


def load_utk_file(
    connection: duckdb.DuckDBPyConnection, path: Path, target_table: str
) -> None:
    digest = sha256_file(path)
    if already_loaded(connection, path, digest):
        print(f"POMINIĘTO (bez zmian): {source_name(path)}")
        return

    if target_table == "stg_utk_wymiana":
        sheet_name, header_row = "Alfabetycznie", 0
    else:
        sheet_name, header_row = 0, 2

    excel_connection = connection.cursor()
    in_transaction = False
    try:
        frame = pd.read_excel(
            path, sheet_name=sheet_name, header=header_row, dtype=object
        )
        frame = frame.dropna(axis=1, how="all").dropna(axis=0, how="all")
        frame.columns = [str(column).strip() for column in frame.columns]
        raw_table = f"{target_table}_source"
        registration = f"excel_{target_table}"
        excel_connection.register(registration, frame)
        try:
            excel_connection.execute("BEGIN TRANSACTION")
            in_transaction = True
            excel_connection.execute(
                f"CREATE OR REPLACE TABLE {quote_identifier(raw_table)} AS "
                f"SELECT * FROM {quote_identifier(registration)}"
            )
            row_count = excel_connection.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(raw_table)}"
            ).fetchone()[0]
            excel_connection.execute("COMMIT")
            in_transaction = False
        finally:
            excel_connection.unregister(registration)
        write_load_log(connection, path, digest, row_count, "SUCCESS")
        print(
            f"ZAŁADOWANO: {source_name(path)} / {sheet_name} "
            f"({row_count:,} wierszy)"
        )
    except Exception:
        if in_transaction:
            excel_connection.execute("ROLLBACK")
        write_load_log(connection, path, digest, 0, "FAILED")
        raise
    finally:
        excel_connection.close()


def column_names(connection: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [
        row[0]
        for row in connection.execute(f"DESCRIBE {quote_identifier(table)}").fetchall()
    ]


def find_column(columns: list[str], pattern: str) -> str:
    for column in columns:
        if re.search(pattern, column.strip(), flags=re.IGNORECASE):
            return column
    raise ValueError(f"Nie znaleziono wymaganej kolumny pasującej do {pattern!r}: {columns}")


def append_rejections(
    connection: duckdb.DuckDBPyConnection,
    source: str,
    reason: str,
    identifier_sql: str,
    raw_sql: str,
    from_where_sql: str,
) -> int:
    connection.execute("DELETE FROM stg_err_rejected_records WHERE zrodlo = ?", [source])
    start_id = connection.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM stg_err_rejected_records"
    ).fetchone()[0]
    connection.execute(
        f"""
        INSERT INTO stg_err_rejected_records
        (id, zrodlo, identyfikator_rekordu, powod_odrzucenia, surowe_dane, data_odrzucenia)
        SELECT {start_id} + ROW_NUMBER() OVER () - 1,
               {quote_literal(source)}, CAST({identifier_sql} AS VARCHAR),
               {quote_literal(reason)}, CAST({raw_sql} AS VARCHAR), CURRENT_TIMESTAMP
        {from_where_sql}
        """
    )
    return connection.execute(
        "SELECT COUNT(*) FROM stg_err_rejected_records WHERE zrodlo = ?", [source]
    ).fetchone()[0]


def apply_quality_gates(connection: duckdb.DuckDBPyConnection) -> None:
    trip_columns = column_names(connection, "stg_trips_source")
    route_columns = column_names(connection, "stg_routes_source")
    if "trip_id" not in trip_columns or "route_id" not in trip_columns:
        raise ValueError("GTFS trips.txt musi zawierać kolumny trip_id i route_id.")
    if "route_id" not in route_columns or "route_type" not in route_columns:
        raise ValueError("GTFS routes.txt musi zawierać kolumny route_id i route_type.")

    trip_text = [
        name
        for name in ("trip_short_name", "trip_headsign", "plk_category_code", "plk_train_name")
        if name in trip_columns
    ]
    route_text = [
        name
        for name in ("route_short_name", "route_long_name")
        if name in route_columns
    ]
    route_predicate = " OR ".join(
        [f"LOWER(TRIM(COALESCE(r.{quote_identifier('route_type')}, ''))) = '3'"]
        + [
            f"COALESCE(r.{quote_identifier(name)}, '') ILIKE '%ZKA%'"
            for name in route_text
        ]
    )
    trip_predicate = " OR ".join(
        [route_predicate]
        + [
            f"COALESCE(t.{quote_identifier(name)}, '') ILIKE '%ZKA%'"
            for name in trip_text
        ]
    )
    joined = (
        "FROM stg_trips_source AS t "
        "JOIN stg_routes_source AS r ON t.route_id = r.route_id "
        f"WHERE ({trip_predicate})"
    )
    zka_count = append_rejections(
        connection,
        "GTFS.trips/routes",
        "KOMUNIKACJA_ZASTEPCZA",
        "t.trip_id",
        "to_json(t)",
        joined,
    )

    connection.execute(
        """
        CREATE OR REPLACE TABLE stg_trips AS
        SELECT t.* FROM stg_trips_source AS t
        WHERE NOT EXISTS (
            SELECT 1 FROM stg_err_rejected_records AS e
            WHERE e.zrodlo = 'GTFS.trips/routes'
              AND e.identyfikator_rekordu = CAST(t.trip_id AS VARCHAR)
        )
        """
    )
    stop_time_columns = column_names(connection, "stg_stop_times_source")
    if "trip_id" not in stop_time_columns:
        raise ValueError("GTFS stop_times.txt musi zawierać kolumnę trip_id.")
    trim_sql = trim_text_columns(connection, "stg_stop_times_source")
    if "platform" not in stop_time_columns:
        raise ValueError("GTFS stop_times.txt nie zawiera wymaganej kolumny platform.")
    trim_parts = [
        part
        for part in trim_sql.split(", ")
        if not part.endswith(f"AS {quote_identifier('platform')}")
    ]
    trim_parts.append(
        "COALESCE(NULLIF(TRIM(platform), ''), 'Brak') AS platform"
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE stg_stop_times AS
        SELECT {', '.join(trim_parts)}
        FROM stg_stop_times_source AS s
        WHERE NOT EXISTS (
            SELECT 1 FROM stg_err_rejected_records AS e
            WHERE e.zrodlo = 'GTFS.trips/routes'
              AND e.identyfikator_rekordu = CAST(s.trip_id AS VARCHAR)
        )
        """
    )

    for table in ("stg_stops", "stg_calendar_dates", "stg_agency"):
        raw_table = f"{table}_source"
        connection.execute(
            f"CREATE OR REPLACE TABLE {quote_identifier(table)} AS "
            f"SELECT {trim_text_columns(connection, raw_table)} "
            f"FROM {quote_identifier(raw_table)}"
        )

    route_trim = trim_text_columns(connection, "stg_routes_source")
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE stg_routes AS
        SELECT {route_trim} FROM stg_routes_source AS r
        WHERE NOT ({route_predicate})
        """
    )

    exchange_source = "stg_utk_wymiana_source"
    connection.execute(
        f"CREATE OR REPLACE TABLE stg_utk_wymiana AS "
        f"SELECT {trim_text_columns(connection, exchange_source)} "
        f"FROM {quote_identifier(exchange_source)}"
    )

    stop_source = "stg_utk_zatrzymania_source"
    stop_columns = column_names(connection, stop_source)
    station_column = find_column(stop_columns, r"stacja|nazwa.*stacji")
    quoted_station = quote_identifier(station_column)
    sum_filter = f"COALESCE(CAST({quoted_station} AS VARCHAR), '') ILIKE '%Suma%'"
    rejected_sums = append_rejections(
        connection,
        "UTK.zatrzymania",
        "WIERSZ_PODSUMOWANIA_UTK",
        f"CAST({quoted_station} AS VARCHAR)",
        "to_json(u)",
        f"FROM {quote_identifier(stop_source)} AS u WHERE {sum_filter}",
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE stg_utk_zatrzymania AS
        SELECT {trim_text_columns(connection, stop_source)}
        FROM {quote_identifier(stop_source)}
        WHERE NOT {sum_filter}
        """
    )
    print(f"ODRZUCENIA: kursy ZKA/autobusowe={zka_count:,}, wiersze UTK 'Suma'={rejected_sums:,}")


def initialize_database(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stg_etl_load_log (
            id INTEGER,
            nazwa_pliku VARCHAR,
            hash_sha256 VARCHAR,
            data_zaladowania TIMESTAMP,
            liczba_wierszy INTEGER,
            status VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stg_err_rejected_records (
            id INTEGER,
            zrodlo VARCHAR,
            identyfikator_rekordu VARCHAR,
            powod_odrzucenia VARCHAR,
            surowe_dane VARCHAR,
            data_odrzucenia TIMESTAMP
        )
        """
    )


def print_summary(connection: duckdb.DuckDBPyConnection) -> None:
    tables = [
        "stg_stop_times",
        "stg_trips",
        "stg_stops",
        "stg_calendar_dates",
        "stg_routes",
        "stg_agency",
        "stg_utk_wymiana",
        "stg_utk_zatrzymania",
    ]
    print("\nPODSUMOWANIE STAGINGU")
    for table in tables:
        count = connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0]
        print(f"  {table}: {count:,} wierszy")
    print("\nPODSUMOWANIE ODRZUCEŃ")
    for source, reason, count in connection.execute(
        """
        SELECT zrodlo, powod_odrzucenia, COUNT(*)
        FROM stg_err_rejected_records
        GROUP BY zrodlo, powod_odrzucenia
        ORDER BY zrodlo, powod_odrzucenia
        """
    ).fetchall():
        print(f"  {source} / {reason}: {count:,}")


def main() -> int:
    gtfs_files, utk_files = find_source_files()
    connection = duckdb.connect(str(DATABASE))
    try:
        initialize_database(connection)
        for filename, target_table in GTFS_TABLES.items():
            load_gtfs_file(connection, gtfs_files[filename], target_table)
        for target_table, path in utk_files.items():
            load_utk_file(connection, path, target_table)
        connection.execute("BEGIN TRANSACTION")
        try:
            apply_quality_gates(connection)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        print_summary(connection)
        print(f"\nBaza danych: {DATABASE}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"BŁĄD ETL: {error}", file=sys.stderr)
        raise
