#!/usr/bin/env python3
"""Run quality gates for the local kolej.duckdb data warehouse."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Final

import duckdb


ROOT: Final = Path(__file__).resolve().parent
DATABASE: Final = ROOT / "kolej.duckdb"
MONTH_COLUMNS: Final = (
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
Check: Final = tuple[str, str, str, Callable[[int], bool], str]


def build_checks() -> list[Check]:
    checks: list[Check] = []
    facts: Final = {
        "Fakt_Zatrzymanie": (
            "ID_Dnia",
            "ID_Godziny",
            "SK_Stacja",
            "SK_Kurs",
            "SK_Przewoznik",
        ),
        "Fakt_Przesiadka": (
            "ID_Dnia",
            "ID_Godziny_Przyj",
            "SK_Stacja",
            "SK_Kurs_Przyj",
            "SK_Kurs_Odj",
            "SK_Przewoznik_Przyj",
            "SK_Przewoznik_Odj",
        ),
        "Fakt_Zatrzymania_UTK": (
            "ID_Miesiaca",
            "SK_Stacja",
            "SK_Przewoznik",
        ),
    }
    dimensions: Final = {
        "ID_Dnia": ("Dim_Czas", "ID_Dnia"),
        "ID_Godziny": ("Dim_Pora_Doby", "ID_Godziny"),
        "ID_Godziny_Przyj": ("Dim_Pora_Doby", "ID_Godziny"),
        "ID_Miesiaca": ("Dim_Miesiac", "ID_Miesiaca"),
        "SK_Stacja": ("Dim_Stacja", "SK_Stacja"),
        "SK_Kurs": ("Dim_Kurs", "SK_Kurs"),
        "SK_Kurs_Przyj": ("Dim_Kurs", "SK_Kurs"),
        "SK_Kurs_Odj": ("Dim_Kurs", "SK_Kurs"),
        "SK_Przewoznik": ("Dim_Przewoznik", "SK_Przewoznik"),
        "SK_Przewoznik_Przyj": ("Dim_Przewoznik", "SK_Przewoznik"),
        "SK_Przewoznik_Odj": ("Dim_Przewoznik", "SK_Przewoznik"),
    }

    for fact, columns in facts.items():
        null_condition = " OR ".join(f"{column} IS NULL" for column in columns)
        checks.append(
            (
                f"NULL FK: {fact}",
                f"0 rekordów z NULL w kluczach faktu ({', '.join(columns)})",
                f"SELECT COUNT(*) FROM dw.{fact} WHERE {null_condition}",
                lambda value: value == 0,
                "oczekiwano 0",
            )
        )
        for column in columns:
            dimension, key = dimensions[column]
            checks.append(
                (
                    f"FK: {fact}.{column} -> Dim_{dimension.removeprefix('Dim_')}",
                    "0 kluczy osieroconych względem wymiaru",
                    f"""
                    SELECT COUNT(*)
                    FROM dw.{fact} f
                    LEFT JOIN dw.{dimension} d ON f.{column} = d.{key}
                    WHERE d.{key} IS NULL
                    """,
                    lambda value: value == 0,
                    "oczekiwano 0 sierot",
                )
            )

    primary_keys: Final = {
        "Dim_Stacja": ("SK_Stacja",),
        "Dim_Przewoznik": ("SK_Przewoznik",),
        "Dim_Kurs": ("SK_Kurs",),
        "Dim_Czas": ("ID_Dnia",),
        "Dim_Miesiac": ("ID_Miesiaca",),
        "Dim_Pora_Doby": ("ID_Godziny",),
        "Fakt_Zatrzymanie": ("ID_Dnia", "SK_Kurs", "SK_Stacja", "nr_postoju"),
        "Fakt_Przesiadka": (
            "ID_Dnia",
            "SK_Stacja",
            "SK_Kurs_Przyj",
            "SK_Kurs_Odj",
        ),
        "Fakt_Zatrzymania_UTK": ("ID_Miesiaca", "SK_Stacja", "SK_Przewoznik"),
    }
    for table, columns in primary_keys.items():
        keys = ", ".join(columns)
        checks.append(
            (
                f"PK: {table}",
                f"brak duplikatów klucza ({keys})",
                f"""
                SELECT COUNT(*) FROM (
                    SELECT {keys}
                    FROM dw.{table}
                    GROUP BY {keys}
                    HAVING COUNT(*) > 1
                )
                """,
                lambda value: value == 0,
                "oczekiwano 0 grup duplikatów",
            )
        )

    months = ", ".join(f'"{month}"' for month in MONTH_COLUMNS)
    checks.extend(
        [
            (
                "Reconciliation UTK",
                "suma miesięczna źródła dla 325 stacji = suma Fakt_Zatrzymania_UTK",
                f"""
                WITH source_months AS (
                    SELECT
                        TRIM(Stacja) AS station,
                        COALESCE(TRY_CAST(value AS BIGINT), 0) AS stops
                    FROM stg_utk_zatrzymania
                    UNPIVOT INCLUDE NULLS (
                        value FOR month_name IN ({months})
                    )
                    WHERE COALESCE(CAST(Stacja AS VARCHAR), '') NOT ILIKE '%Suma%'
                ),
                source_total AS (
                    SELECT SUM(m.stops) AS total
                    FROM source_months m
                    JOIN dw.Dim_Stacja s ON s.nazwa = m.station
                ),
                warehouse_total AS (
                    SELECT SUM(liczba_zatrzyman) AS total
                    FROM dw.Fakt_Zatrzymania_UTK
                )
                SELECT ABS(source_total.total - warehouse_total.total)::BIGINT
                FROM source_total CROSS JOIN warehouse_total
                """,
                lambda value: value == 0,
                "różnica sum = 0",
            ),
            (
                "Liczba stacji",
                "dw.Dim_Stacja zawiera dokładnie 325 stacji",
                "SELECT COUNT(*) FROM dw.Dim_Stacja",
                lambda value: value == 325,
                "oczekiwano 325",
            ),
            (
                "Powiat stacji",
                "każda stacja ma niepusty powiat",
                """
                SELECT COUNT(*) FROM dw.Dim_Stacja
                WHERE powiat IS NULL OR TRIM(powiat) = ''
                """,
                lambda value: value == 0,
                "oczekiwano 0 braków",
            ),
            (
                "Sześć węzłów",
                "wymiar zawiera dokładnie 6 wskazanych węzłów",
                """
                SELECT ABS(
                    COUNT(*) FILTER (WHERE czy_wezel) - 6
                ) + ABS(
                    COUNT(*) FILTER (
                        WHERE czy_wezel AND nazwa IN (
                            'Wrocław Główny', 'Legnica', 'Jelenia Góra',
                            'Wałbrzych Główny', 'Jaworzyna Śląska',
                            'Kłodzko Główne'
                        )
                    ) - 6
                )
                FROM dw.Dim_Stacja
                """,
                lambda value: value == 0,
                "oczekiwano dokładnie 6",
            ),
            (
                "Czas oczekiwania",
                "każdy czas oczekiwania mieści się włącznie w [5, 30] min",
                """
                SELECT COUNT(*) FROM dw.Fakt_Przesiadka
                WHERE czas_oczekiwania_min NOT BETWEEN 5 AND 30
                """,
                lambda value: value == 0,
                "oczekiwano 0 poza zakresem",
            ),
            (
                "Flagi okna przesiadki",
                "SUM(flaga_okno_10_25) <= SUM(liczba_par)",
                """
                SELECT GREATEST(
                    COALESCE(SUM(flaga_okno_10_25), 0)
                    - COALESCE(SUM(liczba_par), 0),
                    0
                )
                FROM dw.Fakt_Przesiadka
                """,
                lambda value: value == 0,
                "nadmiar flag nad liczbą par = 0",
            ),
            (
                "Godziny nocne 24-29",
                "fakt zawiera co najmniej jeden odjazd 24:00-29:59 oznaczony jako noc",
                """
                SELECT COUNT(*)
                FROM dw.Fakt_Zatrzymanie f
                JOIN dw.Dim_Pora_Doby h ON h.ID_Godziny = f.ID_Godziny
                WHERE f.ID_Godziny BETWEEN 24 AND 29
                  AND h.pora_doby = 'Noc'
                """,
                lambda value: value > 0,
                "oczekiwano co najmniej 1",
            ),
        ]
    )
    return checks


def run_tests() -> int:
    if not DATABASE.is_file():
        print(f"FAILED: nie znaleziono bazy danych: {DATABASE}", file=sys.stderr)
        return 2

    connection = duckdb.connect(str(DATABASE), read_only=True)
    results: list[tuple[str, str, str, str]] = []
    try:
        for name, condition, query, predicate, expected in build_checks():
            try:
                row = connection.execute(query).fetchone()
                if row is None or row[0] is None:
                    raise ValueError("zapytanie kontrolne nie zwróciło wartości")
                actual = int(row[0])
                passed = predicate(actual)
                detail = f"{actual} ({expected})"
            except duckdb.Error as error:
                passed = False
                detail = f"błąd SQL: {error}"
            except ValueError as error:
                passed = False
                detail = str(error)
            results.append((name, condition, "PASSED" if passed else "FAILED", detail))
    finally:
        connection.close()

    widths = (
        max(len("Test"), *(len(row[0]) for row in results)),
        max(len("Warunek"), *(len(row[1]) for row in results)),
        len("Status"),
        max(len("Szczegóły"), *(len(row[3]) for row in results)),
    )
    header = (
        f"| {'Test':<{widths[0]}} | {'Warunek':<{widths[1]}} | "
        f"{'Status':<{widths[2]}} | {'Szczegóły':<{widths[3]}} |"
    )
    separator = (
        f"|{'-' * (widths[0] + 2)}|{'-' * (widths[1] + 2)}|"
        f"{'-' * (widths[2] + 2)}|{'-' * (widths[3] + 2)}|"
    )
    print(header)
    print(separator)
    for name, condition, status, detail in results:
        print(
            f"| {name:<{widths[0]}} | {condition:<{widths[1]}} | "
            f"{status:<{widths[2]}} | {detail:<{widths[3]}} |"
        )

    passed_count = sum(result[2] == "PASSED" for result in results)
    failed_count = len(results) - passed_count
    print(f"\nPodsumowanie: {passed_count} PASSED, {failed_count} FAILED.")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_tests())
