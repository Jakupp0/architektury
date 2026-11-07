#!/usr/bin/env python3
"""Create the data-warehouse schema and seed its static dimensions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import duckdb


ROOT: Final = Path(__file__).resolve().parent
DATABASE: Final = ROOT / "kolej.duckdb"

MONTH_NAMES: Final = (
    "styczeń",
    "luty",
    "marzec",
    "kwiecień",
    "maj",
    "czerwiec",
    "lipiec",
    "sierpień",
    "wrzesień",
    "październik",
    "listopad",
    "grudzień",
)
WEEKDAY_NAMES: Final = (
    "poniedziałek",
    "wtorek",
    "środa",
    "czwartek",
    "piątek",
    "sobota",
    "niedziela",
)

DDL: Final = (
    """
    CREATE TABLE IF NOT EXISTS dw.Mostek_Stacji (
        id_GTFS VARCHAR PRIMARY KEY,
        nazwa_UTK VARCHAR,
        metoda VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Dim_Stacja (
        SK_Stacja INTEGER PRIMARY KEY,
        id_GTFS VARCHAR,
        nazwa VARCHAR,
        powiat VARCHAR,
        gmina VARCHAR,
        wojewodztwo VARCHAR,
        lat DECIMAL(9, 6),
        lon DECIMAL(9, 6),
        czy_wezel BOOLEAN,
        wymiana_min INTEGER,
        wymiana_max INTEGER,
        kategoria_wymiany VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Dim_Przewoznik (
        SK_Przewoznik INTEGER PRIMARY KEY,
        id_agencji VARCHAR,
        nazwa_GTFS VARCHAR,
        nazwa_UTK VARCHAR,
        grupa VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Dim_Kurs (
        SK_Kurs INTEGER PRIMARY KEY,
        trip_id VARCHAR,
        numer_pociagu VARCHAR,
        kategoria VARCHAR,
        linia VARCHAR,
        stacja_poczatkowa INTEGER REFERENCES dw.Dim_Stacja (SK_Stacja),
        stacja_koncowa INTEGER REFERENCES dw.Dim_Stacja (SK_Stacja),
        kierunek VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Dim_Czas (
        ID_Dnia DATE PRIMARY KEY,
        data DATE,
        dzien_tygodnia VARCHAR,
        typ_dnia VARCHAR CHECK (typ_dnia IN ('Dzień roboczy', 'Weekend')),
        miesiac INTEGER,
        kwartal INTEGER,
        rok INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Dim_Miesiac (
        ID_Miesiaca INTEGER PRIMARY KEY,
        numer_miesiaca INTEGER CHECK (numer_miesiaca BETWEEN 1 AND 12),
        nazwa_miesiaca VARCHAR,
        kwartal INTEGER,
        rok INTEGER CHECK (rok = 2025)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Dim_Pora_Doby (
        ID_Godziny INTEGER PRIMARY KEY CHECK (ID_Godziny BETWEEN 0 AND 29),
        godzina_etykieta VARCHAR,
        pora_doby VARCHAR,
        flaga_po_polnocy BOOLEAN
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Fakt_Zatrzymanie (
        ID_Dnia DATE NOT NULL REFERENCES dw.Dim_Czas (ID_Dnia),
        ID_Godziny INTEGER NOT NULL REFERENCES dw.Dim_Pora_Doby (ID_Godziny),
        SK_Stacja INTEGER NOT NULL REFERENCES dw.Dim_Stacja (SK_Stacja),
        SK_Kurs INTEGER NOT NULL REFERENCES dw.Dim_Kurs (SK_Kurs),
        SK_Przewoznik INTEGER NOT NULL REFERENCES dw.Dim_Przewoznik (SK_Przewoznik),
        nr_postoju INTEGER NOT NULL,
        liczba_odjazdow INTEGER NOT NULL DEFAULT 1,
        odstep_min INTEGER,
        czas_od_startu_min INTEGER,
        PRIMARY KEY (ID_Dnia, SK_Kurs, SK_Stacja, nr_postoju)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Fakt_Przesiadka (
        ID_Dnia DATE NOT NULL REFERENCES dw.Dim_Czas (ID_Dnia),
        ID_Godziny_Przyj INTEGER NOT NULL REFERENCES dw.Dim_Pora_Doby (ID_Godziny),
        SK_Stacja INTEGER NOT NULL REFERENCES dw.Dim_Stacja (SK_Stacja),
        SK_Kurs_Przyj INTEGER NOT NULL REFERENCES dw.Dim_Kurs (SK_Kurs),
        SK_Kurs_Odj INTEGER NOT NULL REFERENCES dw.Dim_Kurs (SK_Kurs),
        SK_Przewoznik_Przyj INTEGER NOT NULL REFERENCES dw.Dim_Przewoznik (SK_Przewoznik),
        SK_Przewoznik_Odj INTEGER NOT NULL REFERENCES dw.Dim_Przewoznik (SK_Przewoznik),
        czas_oczekiwania_min INTEGER NOT NULL,
        liczba_par INTEGER NOT NULL DEFAULT 1,
        flaga_okno_10_25 INTEGER NOT NULL CHECK (flaga_okno_10_25 IN (0, 1)),
        PRIMARY KEY (ID_Dnia, SK_Stacja, SK_Kurs_Przyj, SK_Kurs_Odj)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dw.Fakt_Zatrzymania_UTK (
        ID_Miesiaca INTEGER NOT NULL REFERENCES dw.Dim_Miesiac (ID_Miesiaca),
        SK_Stacja INTEGER NOT NULL REFERENCES dw.Dim_Stacja (SK_Stacja),
        SK_Przewoznik INTEGER NOT NULL REFERENCES dw.Dim_Przewoznik (SK_Przewoznik),
        liczba_zatrzyman INTEGER NOT NULL,
        PRIMARY KEY (ID_Miesiaca, SK_Stacja, SK_Przewoznik)
    )
    """,
)


def read_calendar_dates(connection: duckdb.DuckDBPyConnection) -> list[date]:
    tables = connection.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'stg_calendar_dates'
        """
    ).fetchall()
    if not tables:
        raise RuntimeError("Brak wymaganej tabeli stagingowej stg_calendar_dates.")

    columns = {
        row[0]
        for row in connection.execute("DESCRIBE main.stg_calendar_dates").fetchall()
    }
    if "date" not in columns:
        raise RuntimeError(
            "Tabela stg_calendar_dates nie zawiera wymaganej kolumny date."
        )

    null_dates = connection.execute(
        "SELECT COUNT(*) FROM main.stg_calendar_dates WHERE date IS NULL"
    ).fetchone()[0]
    if null_dates:
        raise RuntimeError(
            f"stg_calendar_dates zawiera {null_dates} wierszy bez daty."
        )

    raw_dates = connection.execute(
        "SELECT DISTINCT CAST(date AS VARCHAR) FROM main.stg_calendar_dates "
    ).fetchall()
    if not raw_dates:
        raise RuntimeError("Tabela stg_calendar_dates nie zawiera żadnych dat.")

    dates: list[date] = []
    for (raw_date,) in raw_dates:
        try:
            dates.append(datetime.strptime(raw_date.strip(), "%Y%m%d").date())
        except (AttributeError, ValueError) as error:
            raise RuntimeError(
                f"Niepoprawna data w stg_calendar_dates: {raw_date!r}; "
                "oczekiwano formatu YYYYMMDD."
            ) from error
    return sorted(set(dates))


def build_calendar_rows(source_dates: list[date]) -> list[tuple[object, ...]]:
    start_date, end_date = source_dates[0], source_dates[-1]
    rows: list[tuple[object, ...]] = []
    current = start_date
    while current <= end_date:
        rows.append(
            (
                current,
                current,
                WEEKDAY_NAMES[current.weekday()],
                "Weekend" if current.weekday() >= 5 else "Dzień roboczy",
                current.month,
                (current.month - 1) // 3 + 1,
                current.year,
            )
        )
        current += timedelta(days=1)
    return rows


def build_month_rows() -> list[tuple[object, ...]]:
    return [
        (202500 + month, month, MONTH_NAMES[month - 1], (month - 1) // 3 + 1, 2025)
        for month in range(1, 13)
    ]


def build_hour_rows() -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for hour in range(30):
        if 6 <= hour < 9:
            period = "Szczyt poranny"
        elif 9 <= hour < 14:
            period = "Międzyszczyt"
        elif 14 <= hour < 18:
            period = "Szczyt popołudniowy"
        elif 18 <= hour < 22:
            period = "Wieczór"
        else:
            period = "Noc"
        rows.append((hour, f"{hour:02d}:00", period, hour >= 24))
    return rows


def assert_empty_facts(connection: duckdb.DuckDBPyConnection) -> None:
    fact_tables = (
        "Fakt_Zatrzymanie",
        "Fakt_Przesiadka",
        "Fakt_Zatrzymania_UTK",
    )
    populated = [
        f"dw.{table}"
        for table in fact_tables
        if connection.execute(f"SELECT COUNT(*) FROM dw.{table}").fetchone()[0] > 0
    ]
    if populated:
        raise RuntimeError(
            "Odmowa ponownego zasilenia wymiarów statycznych, ponieważ tabele "
            f"faktów zawierają dane: {', '.join(populated)}."
        )


def seed_static_dimensions(
    connection: duckdb.DuckDBPyConnection, source_dates: list[date]
) -> None:
    assert_empty_facts(connection)
    calendar_rows = build_calendar_rows(source_dates)
    month_rows = build_month_rows()
    hour_rows = build_hour_rows()

    for table in ("Dim_Czas", "Dim_Miesiac", "Dim_Pora_Doby"):
        connection.execute(f"DELETE FROM dw.{table}")

    connection.executemany(
        """
        INSERT INTO dw.Dim_Czas
        (ID_Dnia, data, dzien_tygodnia, typ_dnia, miesiac, kwartal, rok)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        calendar_rows,
    )
    connection.executemany(
        """
        INSERT INTO dw.Dim_Miesiac
        (ID_Miesiaca, numer_miesiaca, nazwa_miesiaca, kwartal, rok)
        VALUES (?, ?, ?, ?, ?)
        """,
        month_rows,
    )
    connection.executemany(
        """
        INSERT INTO dw.Dim_Pora_Doby
        (ID_Godziny, godzina_etykieta, pora_doby, flaga_po_polnocy)
        VALUES (?, ?, ?, ?)
        """,
        hour_rows,
    )


def verify_static_dimensions(
    connection: duckdb.DuckDBPyConnection, expected_calendar_rows: int
) -> None:
    expected_counts = {
        "Dim_Czas": expected_calendar_rows,
        "Dim_Miesiac": 12,
        "Dim_Pora_Doby": 30,
    }
    for table, expected_count in expected_counts.items():
        actual_count = connection.execute(
            f"SELECT COUNT(*) FROM dw.{table}"
        ).fetchone()[0]
        print(f"SELECT count(*) FROM dw.{table}; -- {actual_count}")
        if actual_count != expected_count:
            raise RuntimeError(
                f"Niepoprawna liczba wierszy w dw.{table}: "
                f"{actual_count}, oczekiwano {expected_count}."
            )

    invalid_calendar_rows = connection.execute(
        """
        SELECT COUNT(*)
        FROM dw.Dim_Czas
        WHERE ID_Dnia IS NULL OR data IS NULL OR ID_Dnia <> data
           OR dzien_tygodnia IS NULL OR typ_dnia IS NULL
           OR miesiac IS NULL OR kwartal IS NULL OR rok IS NULL
           OR dzien_tygodnia <> CASE strftime(ID_Dnia, '%w')
                WHEN '0' THEN 'niedziela'
                WHEN '1' THEN 'poniedziałek'
                WHEN '2' THEN 'wtorek'
                WHEN '3' THEN 'środa'
                WHEN '4' THEN 'czwartek'
                WHEN '5' THEN 'piątek'
                WHEN '6' THEN 'sobota'
              END
           OR typ_dnia <> CASE WHEN strftime(ID_Dnia, '%w') IN ('0', '6')
                THEN 'Weekend' ELSE 'Dzień roboczy' END
           OR miesiac <> EXTRACT(MONTH FROM ID_Dnia)
           OR kwartal <> ((EXTRACT(MONTH FROM ID_Dnia) - 1) // 3 + 1)
           OR rok <> EXTRACT(YEAR FROM ID_Dnia)
        """
    ).fetchone()[0]
    if invalid_calendar_rows:
        raise RuntimeError(
            f"dw.Dim_Czas zawiera niepoprawne wiersze: {invalid_calendar_rows}."
        )
    calendar_span = connection.execute(
        """
        SELECT COUNT(*) = (MAX(ID_Dnia) - MIN(ID_Dnia)) + 1
        FROM dw.Dim_Czas
        """
    ).fetchone()[0]
    if not calendar_span:
        raise RuntimeError("dw.Dim_Czas nie tworzy ciągłego zakresu dat.")

    bad_month_rows = connection.execute(
        """
        SELECT COUNT(*)
        FROM dw.Dim_Miesiac
        WHERE ID_Miesiaca IS NULL OR numer_miesiaca IS NULL
           OR kwartal IS NULL OR nazwa_miesiaca IS NULL OR rok IS NULL
           OR ID_Miesiaca <> rok * 100 + numer_miesiaca
           OR numer_miesiaca NOT BETWEEN 1 AND 12
           OR kwartal <> ((numer_miesiaca - 1) // 3 + 1)
           OR nazwa_miesiaca IS NULL OR rok <> 2025
        """
    ).fetchone()[0]
    if bad_month_rows:
        raise RuntimeError(
            f"dw.Dim_Miesiac zawiera niepoprawne wiersze: {bad_month_rows}."
        )

    bad_hour_rows = connection.execute(
        """
        SELECT COUNT(*)
        FROM dw.Dim_Pora_Doby
        WHERE ID_Godziny IS NULL OR godzina_etykieta IS NULL
           OR ID_Godziny NOT BETWEEN 0 AND 29
           OR godzina_etykieta <> LPAD(CAST(ID_Godziny AS VARCHAR), 2, '0') || ':00'
           OR pora_doby IS NULL
           OR flaga_po_polnocy IS NULL
           OR flaga_po_polnocy <> (ID_Godziny >= 24)
        """
    ).fetchone()[0]
    if bad_hour_rows:
        raise RuntimeError(
            f"dw.Dim_Pora_Doby zawiera niepoprawne wiersze: {bad_hour_rows}."
        )


def main() -> None:
    connection = duckdb.connect(str(DATABASE))
    try:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("CREATE SCHEMA IF NOT EXISTS dw")
            for statement in DDL:
                connection.execute(statement)

            source_dates = read_calendar_dates(connection)
            seed_static_dimensions(connection, source_dates)
            verify_static_dimensions(connection, len(build_calendar_rows(source_dates)))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

        print(
            "Zakres Dim_Czas: "
            f"{source_dates[0].isoformat()} – {source_dates[-1].isoformat()} "
            f"(ciągły kalendarz, {len(build_calendar_rows(source_dates))} dni)."
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
