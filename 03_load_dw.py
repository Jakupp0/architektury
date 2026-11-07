#!/usr/bin/env python3
"""Transform staged GTFS/UTK data and load the business data warehouse."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Final

import duckdb


ROOT: Final = Path(__file__).resolve().parent
DATABASE: Final = ROOT / "kolej.duckdb"
LOG_PATH: Final = ROOT / "03_load_dw.log"
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

MANUAL_STATION_OVERRIDES: Final = (
    ("Wrocław Mikołajów", "58651"),
    ("Jelenia Góra Cieplice", "56093"),
)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("dw_loader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def require_source_tables(connection: duckdb.DuckDBPyConnection) -> None:
    required = (
        "stg_stop_times",
        "stg_trips",
        "stg_stops",
        "stg_calendar_dates",
        "stg_routes",
        "stg_agency",
        "stg_utk_wymiana",
        "stg_utk_zatrzymania",
    )
    existing = {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchall()
    }
    missing = sorted(set(required) - existing)
    if missing:
        raise RuntimeError(f"Brak wymaganych tabel stagingowych: {', '.join(missing)}.")

    dimension_counts = {
        "Dim_Czas": connection.execute(
            "SELECT COUNT(*) FROM dw.Dim_Czas"
        ).fetchone()[0],
        "Dim_Miesiac": connection.execute(
            "SELECT COUNT(*) FROM dw.Dim_Miesiac"
        ).fetchone()[0],
        "Dim_Pora_Doby": connection.execute(
            "SELECT COUNT(*) FROM dw.Dim_Pora_Doby"
        ).fetchone()[0],
    }
    expected = {"Dim_Miesiac": 12, "Dim_Pora_Doby": 30}
    for table, count in dimension_counts.items():
        if count == 0 or (table in expected and count != expected[table]):
            raise RuntimeError(
                f"Nieprzygotowany wymiar statyczny dw.{table}: {count} wierszy."
            )

    bad_hours = connection.execute(
        """
        SELECT COUNT(*)
        FROM dw.Dim_Pora_Doby
        WHERE ID_Godziny NOT BETWEEN 0 AND 29
        """
    ).fetchone()[0]
    if bad_hours:
        raise RuntimeError(
            f"dw.Dim_Pora_Doby zawiera {bad_hours} godzin poza zakresem 0–29."
        )

    station_count = connection.execute(
        """
        SELECT COUNT(DISTINCT "nazwa stacji")
        FROM stg_utk_wymiana
        WHERE "województwo" ILIKE '%Dolnośląskie%'
        """
    ).fetchone()[0]
    if station_count != 325:
        raise RuntimeError(
            "Oczekiwano 325 dolnośląskich stacji UTK, "
            f"znaleziono {station_count}."
        )


def prepare_station_mapping(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    override_values = ", ".join(
        "('" + name.replace("'", "''") + "', '" + stop_id + "')"
        for name, stop_id in MANUAL_STATION_OVERRIDES
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _station_mapping AS
        WITH utk AS (
            SELECT
                TRIM("nazwa stacji") AS station_name,
                "długość geograficzna" AS longitude,
                "szerokośc geograficzna" AS latitude,
                "województwo" AS wojewodztwo,
                "powiat" AS powiat,
                "gmina (dzielnica)" AS gmina,
                "wymiana pasażerska\n[liczba osób]" AS exchange_text
            FROM stg_utk_wymiana
            WHERE "województwo" ILIKE '%Dolnośląskie%'
        ),
        nodes AS (
            SELECT
                stop_id,
                stop_name,
                TRY_CAST(stop_lat AS DOUBLE) AS latitude,
                TRY_CAST(stop_lon AS DOUBLE) AS longitude
            FROM stg_stops
            WHERE location_type = '1'
               OR (location_type = '0' AND COALESCE(parent_station, '') = '')
        ),
        overrides(station_name, stop_id) AS (VALUES {override_values}),
        candidates AS (
            SELECT u.station_name, n.stop_id, 'MANUAL_OVERRIDE' AS method, 1 AS priority
            FROM utk u
            JOIN overrides o USING (station_name)
            JOIN nodes n USING (stop_id)

            UNION ALL

            SELECT u.station_name, n.stop_id, 'AUTO_EXACT' AS method, 2 AS priority
            FROM utk u
            JOIN nodes n
              ON LOWER(TRIM(n.stop_name)) = LOWER(TRIM(u.station_name))
            WHERE NOT EXISTS (
                SELECT 1 FROM overrides o WHERE o.station_name = u.station_name
            )

            UNION ALL

            SELECT u.station_name, n.stop_id, 'NORMALIZED' AS method, 3 AS priority
            FROM utk u
            JOIN nodes n
              ON TRANSLATE(
                    STRIP_ACCENTS(LOWER(TRIM(REGEXP_REPLACE(n.stop_name, '\\s+', ' ', 'g')))),
                    'ł', 'l'
                 )
               = TRANSLATE(
                    STRIP_ACCENTS(LOWER(TRIM(REGEXP_REPLACE(u.station_name, '\\s+', ' ', 'g')))),
                    'ł', 'l'
                 )
            WHERE NOT EXISTS (
                SELECT 1
                FROM overrides o
                WHERE o.station_name = u.station_name
            )
              AND LOWER(TRIM(n.stop_name)) <> LOWER(TRIM(u.station_name))
        ),
        best AS (
            SELECT station_name, stop_id, method
            FROM candidates
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY station_name ORDER BY priority, stop_id
            ) = 1
        )
        SELECT
            u.*,
            b.stop_id AS id_gtfs,
            b.method,
            n.stop_name AS gtfs_name,
            n.latitude AS gtfs_lat,
            n.longitude AS gtfs_lon,
            TRY_CAST(NULLIF(REGEXP_EXTRACT(u.exchange_text, '([0-9]+)', 1), '') AS INTEGER)
                AS wymiana_min,
            COALESCE(
                TRY_CAST(NULLIF(REGEXP_EXTRACT(u.exchange_text, '-\\s*([0-9]+)', 1), '') AS INTEGER),
                TRY_CAST(NULLIF(REGEXP_EXTRACT(u.exchange_text, '([0-9]+)', 1), '') AS INTEGER)
            ) AS wymiana_max
        FROM utk u
        LEFT JOIN best b USING (station_name)
        LEFT JOIN nodes n ON n.stop_id = b.stop_id
        """
    )

    missing_overrides = connection.execute(
        """
        SELECT COUNT(*)
        FROM (VALUES
            ('Wrocław Mikołajów', '58651'),
            ('Jelenia Góra Cieplice', '56093')
        ) AS o(station_name, stop_id)
        WHERE NOT EXISTS (
            SELECT 1
            FROM stg_stops s
            WHERE s.stop_id = o.stop_id AND s.location_type = '1'
        )
        """
    ).fetchone()[0]
    if missing_overrides:
        raise RuntimeError(
            f"Nie znaleziono {missing_overrides} stacji wskazanych w ręcznych "
            "nadpisaniach mapowania."
        )


def timed_load(
    connection: duckdb.DuckDBPyConnection,
    logger: logging.Logger,
    table: str,
    insert_sql: str,
) -> int:
    started = time.perf_counter()
    connection.execute(insert_sql)
    count = connection.execute(f"SELECT COUNT(*) FROM dw.{table}").fetchone()[0]
    elapsed = time.perf_counter() - started
    logger.info("dw.%s: %s wierszy, %.2f s", table, f"{count:,}", elapsed)
    return count


def load_station_dimensions(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    prepare_station_mapping(connection)
    unmatched = connection.execute(
        "SELECT COUNT(*) FROM _station_mapping WHERE id_gtfs IS NULL"
    ).fetchone()[0]
    logger.info(
        "Kontrola mapowania stacji: 325 UTK, %s dopasowanych, %s bez odpowiednika GTFS.",
        325 - unmatched,
        unmatched,
    )

    timed_load(
        connection,
        logger,
        "Mostek_Stacji",
        """
        INSERT INTO dw.Mostek_Stacji (id_GTFS, nazwa_UTK, metoda)
        WITH mapped AS (
            SELECT id_gtfs, station_name, method
            FROM _station_mapping
            WHERE id_gtfs IS NOT NULL
        ),
        bridge_rows AS (
            SELECT s.stop_id AS id_gtfs, m.station_name AS nazwa_utk, m.method
            FROM mapped m
            JOIN stg_stops s
              ON s.stop_id = m.id_gtfs OR s.parent_station = m.id_gtfs
        )
        SELECT DISTINCT id_gtfs, nazwa_utk, method
        FROM bridge_rows
        """,
    )
    manual_count = connection.execute(
        """
        SELECT COUNT(DISTINCT nazwa_UTK)
        FROM dw.Mostek_Stacji
        WHERE metoda = 'MANUAL_OVERRIDE'
        """
    ).fetchone()[0]
    if manual_count < len(MANUAL_STATION_OVERRIDES):
        raise RuntimeError(
            "Nie zapisano wszystkich ręcznych nadpisań w dw.Mostek_Stacji."
        )

    timed_load(
        connection,
        logger,
        "Dim_Stacja",
        """
        INSERT INTO dw.Dim_Stacja (
            SK_Stacja, id_GTFS, nazwa, powiat, gmina, wojewodztwo,
            lat, lon, czy_wezel, wymiana_min, wymiana_max, kategoria_wymiany
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY station_name)::INTEGER,
            id_gtfs,
            station_name,
            powiat,
            gmina,
            wojewodztwo,
            COALESCE(NULLIF(latitude, 0), gtfs_lat),
            COALESCE(NULLIF(longitude, 0), gtfs_lon),
            TRANSLATE(STRIP_ACCENTS(LOWER(station_name)), 'ł', 'l') IN (
                'wroclaw glowny',
                'legnica',
                'jelenia gora',
                'walbrzych glowny',
                'jaworzyna slaska',
                'klodzko glowne'
            ),
            wymiana_min,
            wymiana_max,
            exchange_text
        FROM _station_mapping
        """,
    )
    if connection.execute("SELECT COUNT(*) FROM dw.Dim_Stacja").fetchone()[0] != 325:
        raise RuntimeError("dw.Dim_Stacja musi zawierać dokładnie 325 stacji.")
    node_count = connection.execute(
        "SELECT COUNT(*) FROM dw.Dim_Stacja WHERE czy_wezel"
    ).fetchone()[0]
    if node_count != 6:
        raise RuntimeError(
            f"dw.Dim_Stacja musi zawierać 6 węzłów, znaleziono {node_count}."
        )


def load_carrier_dimension(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE _carrier_sources AS
        WITH utk AS (
            SELECT DISTINCT TRIM("Przewoźnik") AS utk_name
            FROM stg_utk_zatrzymania
            WHERE "Przewoźnik" IS NOT NULL AND TRIM("Przewoźnik") <> ''
        ),
        normalized_utk AS (
            SELECT
                utk_name,
                TRANSLATE(STRIP_ACCENTS(LOWER(utk_name)), 'ł', 'l') AS normalized_name
            FROM utk
        ),
        agencies AS (
            SELECT agency_id, agency_name,
                CASE
                    WHEN agency_id IN ('IC', 'LEO', 'RJ') THEN 'Dalekobieżny'
                    WHEN agency_id IN ('AR', 'KD', 'KM', 'KML', 'KS', 'KW', 'PR', 'SKM', 'SKMT', 'LKA')
                        THEN 'Regionalny'
                    ELSE 'Pozostali'
                END AS grupa
            FROM stg_agency
        ),
        utk_agency AS (
            SELECT
                u.utk_name,
                CASE
                    WHEN u.normalized_name LIKE '%pkp intercity%' THEN 'IC'
                    WHEN u.normalized_name LIKE '%koleje dolnoslaskie%' THEN 'KD'
                    WHEN u.normalized_name LIKE '%polregio%' THEN 'PR'
                    WHEN u.normalized_name LIKE '%koleje mazowieckie%' THEN 'KM'
                    WHEN u.normalized_name LIKE '%koleje malopolskie%' THEN 'KML'
                    WHEN u.normalized_name LIKE '%koleje slaskie%' THEN 'KS'
                    WHEN u.normalized_name LIKE '%koleje wielkopolskie%' THEN 'KW'
                    WHEN u.normalized_name LIKE '%arriva rp%' THEN 'AR'
                    WHEN u.normalized_name LIKE '%leo express%' THEN 'LEO'
                    WHEN u.normalized_name LIKE '%lodzka kolej aglomeracyjna%' THEN 'LKA'
                    WHEN u.normalized_name LIKE '%regiojet%' THEN 'RJ'
                    WHEN u.normalized_name LIKE '%rail cargo carrier%' THEN 'RCC'
                    WHEN u.normalized_name LIKE '%pkp szybka kolej miejska%' THEN 'SKMT'
                    WHEN u.normalized_name LIKE '%pkp skm trojmiasto%' THEN 'SKMT'
                    WHEN u.normalized_name LIKE '%szybka kolej miejska%' THEN 'SKM'
                    ELSE NULL
                END AS agency_id
            FROM normalized_utk u
        ),
        mapped_names AS (
            SELECT
                a.agency_id,
                a.agency_name,
                a.grupa,
                ua.utk_name,
                ROW_NUMBER() OVER (
                    PARTITION BY a.agency_id ORDER BY ua.utk_name
                ) AS alias_rank
            FROM agencies a
            JOIN utk_agency ua USING (agency_id)
        ),
        matched AS (
            SELECT
                a.agency_id,
                a.agency_name,
                m.utk_name,
                a.grupa
            FROM agencies a
            LEFT JOIN mapped_names m
              ON m.agency_id = a.agency_id AND m.alias_rank = 1
        ),
        aliases AS (
            SELECT
                NULL::VARCHAR AS agency_id,
                agency_name,
                utk_name,
                grupa
            FROM mapped_names
            WHERE alias_rank > 1
        ),
        unmatched AS (
            SELECT
                NULL::VARCHAR AS agency_id,
                NULL::VARCHAR AS agency_name,
                u.utk_name,
                CASE
                    WHEN u.normalized_name LIKE '%pkp intercity%' THEN 'Dalekobieżny'
                    WHEN u.normalized_name LIKE '%koleje %'
                      OR u.normalized_name LIKE '%polregio%'
                      OR u.normalized_name LIKE '%arriva%'
                      OR u.normalized_name LIKE '%szybka kolej miejska%'
                        THEN 'Regionalny'
                    ELSE 'Pozostali'
                END AS grupa
            FROM normalized_utk u
            WHERE NOT EXISTS (
                SELECT 1 FROM utk_agency ua
                WHERE ua.utk_name = u.utk_name AND ua.agency_id IS NOT NULL
            )
        )
        SELECT * FROM matched
        UNION ALL
        SELECT * FROM aliases
        UNION ALL
        SELECT * FROM unmatched
        """
    )
    timed_load(
        connection,
        logger,
        "Dim_Przewoznik",
        """
        INSERT INTO dw.Dim_Przewoznik
            (SK_Przewoznik, id_agencji, nazwa_GTFS, nazwa_UTK, grupa)
        SELECT
            ROW_NUMBER() OVER (
                ORDER BY CASE WHEN agency_id IS NULL THEN 1 ELSE 0 END,
                         agency_id NULLS LAST, utk_name NULLS LAST
            )::INTEGER,
            agency_id,
            agency_name,
            utk_name,
            grupa
        FROM _carrier_sources
        """,
    )
    unmapped = connection.execute(
        """
        SELECT COUNT(DISTINCT u."Przewoźnik")
        FROM stg_utk_zatrzymania u
        LEFT JOIN dw.Dim_Przewoznik p ON p.nazwa_UTK = u."Przewoźnik"
        WHERE p.SK_Przewoznik IS NULL
        """
    ).fetchone()[0]
    if unmapped:
        raise RuntimeError(
            f"Nie zmapowano {unmapped} nazw przewoźników z UTK do Dim_Przewoznik."
        )


def load_course_dimension(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE _course_rows AS
        WITH rail_trips AS (
            SELECT
                t.*,
                r.route_short_name,
                r.route_long_name,
                r.agency_id
            FROM stg_trips t
            JOIN stg_routes r USING (route_id)
            WHERE TRY_CAST(r.route_type AS INTEGER) = 2
        ),
        ordered_stops AS (
            SELECT
                st.trip_id,
                TRY_CAST(st.stop_sequence AS INTEGER) AS sequence_no,
                st.stop_id,
                s.stop_name,
                d.SK_Stacja,
                ROW_NUMBER() OVER (
                    PARTITION BY st.trip_id
                    ORDER BY TRY_CAST(st.stop_sequence AS INTEGER), st.stop_id
                ) AS first_rank,
                ROW_NUMBER() OVER (
                    PARTITION BY st.trip_id
                    ORDER BY TRY_CAST(st.stop_sequence AS INTEGER) DESC, st.stop_id DESC
                ) AS last_rank
            FROM stg_stop_times st
            JOIN rail_trips t USING (trip_id)
            LEFT JOIN stg_stops s USING (stop_id)
            LEFT JOIN dw.Mostek_Stacji b ON b.id_GTFS = st.stop_id
            LEFT JOIN dw.Dim_Stacja d ON d.nazwa = b.nazwa_UTK
            WHERE TRY_CAST(st.stop_sequence AS INTEGER) IS NOT NULL
        ),
        starts AS (
            SELECT trip_id, stop_name, SK_Stacja
            FROM ordered_stops
            WHERE first_rank = 1
        ),
        ends AS (
            SELECT trip_id, stop_name, SK_Stacja
            FROM ordered_stops
            WHERE last_rank = 1
        )
        SELECT
            t.trip_id,
            t.plk_train_number AS numer_pociagu,
            t.plk_category_code AS kategoria,
            COALESCE(NULLIF(t.route_short_name, ''), t.route_long_name) AS linia,
            starts.SK_Stacja AS stacja_poczatkowa,
            ends.SK_Stacja AS stacja_koncowa,
            COALESCE(starts.stop_name, t.trip_headsign, t.trip_id)
                || ' -> ' ||
            COALESCE(ends.stop_name, t.trip_headsign, t.trip_id) AS kierunek
        FROM rail_trips t
        LEFT JOIN starts USING (trip_id)
        LEFT JOIN ends USING (trip_id)
        """
    )
    missing_ids = connection.execute(
        "SELECT COUNT(*) FROM _course_rows WHERE trip_id IS NULL"
    ).fetchone()[0]
    if missing_ids:
        raise RuntimeError(f"Dim_Kurs: znaleziono {missing_ids} pustych identyfikatorów.")
    timed_load(
        connection,
        logger,
        "Dim_Kurs",
        """
        INSERT INTO dw.Dim_Kurs (
            SK_Kurs, trip_id, numer_pociagu, kategoria, linia,
            stacja_poczatkowa, stacja_koncowa, kierunek
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY trip_id)::INTEGER,
            trip_id,
            numer_pociagu,
            kategoria,
            linia,
            stacja_poczatkowa,
            stacja_koncowa,
            kierunek
        FROM _course_rows
        """,
    )


def prepare_service_stops(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        r"""
        CREATE OR REPLACE TEMP TABLE _service_stops AS
        WITH parsed AS (
            SELECT
                CAST(STRPTIME(c.date, '%Y%m%d') AS DATE) AS ID_Dnia,
                t.trip_id,
                TRY_CAST(st.stop_sequence AS INTEGER) AS nr_postoju,
                d.SK_Stacja,
                k.SK_Kurs,
                p.SK_Przewoznik,
                k.kierunek,
                st.arrival_time,
                st.departure_time,
                TRY_CAST(SPLIT_PART(st.arrival_time, ':', 1) AS INTEGER) * 3600
                    + TRY_CAST(SPLIT_PART(st.arrival_time, ':', 2) AS INTEGER) * 60
                    + TRY_CAST(SPLIT_PART(st.arrival_time, ':', 3) AS INTEGER)
                    AS arrival_seconds,
                TRY_CAST(SPLIT_PART(st.departure_time, ':', 1) AS INTEGER) * 3600
                    + TRY_CAST(SPLIT_PART(st.departure_time, ':', 2) AS INTEGER) * 60
                    + TRY_CAST(SPLIT_PART(st.departure_time, ':', 3) AS INTEGER)
                    AS departure_seconds
            FROM stg_calendar_dates c
            JOIN stg_trips t
              ON t.service_id = c.service_id
            JOIN stg_routes r
              ON r.route_id = t.route_id AND TRY_CAST(r.route_type AS INTEGER) = 2
            JOIN stg_stop_times st USING (trip_id)
            JOIN dw.Dim_Kurs k USING (trip_id)
            JOIN dw.Dim_Przewoznik p ON p.id_agencji = r.agency_id
            JOIN dw.Mostek_Stacji b ON b.id_GTFS = st.stop_id
            JOIN dw.Dim_Stacja d ON d.nazwa = b.nazwa_UTK
            JOIN dw.Dim_Czas dc ON dc.ID_Dnia = CAST(STRPTIME(c.date, '%Y%m%d') AS DATE)
            WHERE TRY_CAST(c.exception_type AS INTEGER) = 1
              AND TRY_CAST(st.stop_sequence AS INTEGER) IS NOT NULL
        ),
        with_hours AS (
            SELECT *,
                TRY_CAST(SPLIT_PART(arrival_time, ':', 1) AS INTEGER) AS arrival_hour,
                TRY_CAST(SPLIT_PART(departure_time, ':', 1) AS INTEGER) AS departure_hour,
                TRY_CAST(SPLIT_PART(arrival_time, ':', 2) AS INTEGER) AS arrival_minute,
                TRY_CAST(SPLIT_PART(departure_time, ':', 2) AS INTEGER) AS departure_minute,
                TRY_CAST(SPLIT_PART(arrival_time, ':', 3) AS INTEGER) AS arrival_second,
                TRY_CAST(SPLIT_PART(departure_time, ':', 3) AS INTEGER) AS departure_second
            FROM parsed
        )
        SELECT
            ID_Dnia,
            trip_id,
            nr_postoju,
            SK_Stacja,
            SK_Kurs,
            SK_Przewoznik,
            kierunek,
            CASE
                WHEN arrival_hour BETWEEN 0 AND 29
                 AND arrival_minute BETWEEN 0 AND 59
                 AND arrival_second BETWEEN 0 AND 59
                    THEN arrival_seconds
                ELSE NULL
            END AS arrival_seconds,
            CASE
                WHEN departure_hour BETWEEN 0 AND 29
                 AND departure_minute BETWEEN 0 AND 59
                 AND departure_second BETWEEN 0 AND 59
                    THEN departure_seconds
                ELSE NULL
            END AS departure_seconds
        FROM with_hours
        """
    )


def load_utk_fact(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    month_list = ", ".join(f'"{month}"' for month in MONTH_COLUMNS)
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _utk_unpivot AS
        SELECT
            TRIM("Stacja") AS station_name,
            TRIM("Przewoźnik") AS carrier_name,
            miesiac,
            COALESCE(TRY_CAST(liczba_zatrzyman AS INTEGER), 0) AS liczba_zatrzyman
        FROM stg_utk_zatrzymania
        UNPIVOT INCLUDE NULLS (
            liczba_zatrzyman FOR miesiac IN ({month_list})
        )
        """
    )
    timed_load(
        connection,
        logger,
        "Fakt_Zatrzymania_UTK",
        """
        INSERT INTO dw.Fakt_Zatrzymania_UTK
            (ID_Miesiaca, SK_Stacja, SK_Przewoznik, liczba_zatrzyman)
        SELECT
            dm.ID_Miesiaca,
            ds.SK_Stacja,
            dp.SK_Przewoznik,
            SUM(u.liczba_zatrzyman)::INTEGER
        FROM _utk_unpivot u
        JOIN dw.Dim_Miesiac dm
          ON dm.rok = 2025
         AND dm.numer_miesiaca = CASE u.miesiac
            WHEN 'styczeń' THEN 1 WHEN 'luty' THEN 2
            WHEN 'marzec' THEN 3 WHEN 'kwiecień' THEN 4
            WHEN 'maj' THEN 5 WHEN 'czerwiec' THEN 6
            WHEN 'lipiec' THEN 7 WHEN 'sierpień' THEN 8
            WHEN 'wrzesień' THEN 9 WHEN 'październik' THEN 10
            WHEN 'listopad' THEN 11 WHEN 'grudzień' THEN 12
         END
        JOIN dw.Dim_Stacja ds ON ds.nazwa = u.station_name
        JOIN dw.Dim_Przewoznik dp ON dp.nazwa_UTK = u.carrier_name
        GROUP BY dm.ID_Miesiaca, ds.SK_Stacja, dp.SK_Przewoznik
        """,
    )
    outside_region = connection.execute(
        """
        SELECT COUNT(*)
        FROM _utk_unpivot u
        LEFT JOIN dw.Dim_Stacja s ON s.nazwa = u.station_name
        WHERE s.SK_Stacja IS NULL
        """
    ).fetchone()[0]
    missing_carriers = connection.execute(
        """
        SELECT COUNT(*)
        FROM _utk_unpivot u
        JOIN dw.Dim_Stacja s ON s.nazwa = u.station_name
        LEFT JOIN dw.Dim_Przewoznik p ON p.nazwa_UTK = u.carrier_name
        WHERE p.SK_Przewoznik IS NULL
        """
    ).fetchone()[0]
    logger.info(
        "F3: %s wierszy poza zakresem 325 stacji; %s brakujących kluczy "
        "przewoźników w zakresie.",
        f"{outside_region:,}",
        f"{missing_carriers:,}",
    )
    if missing_carriers:
        raise RuntimeError(
            f"F3: brak klucza przewoźnika dla {missing_carriers} wierszy "
            "w obrębie 325 stacji."
        )


def load_planned_stop_fact(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    invalid_times = connection.execute(
        """
        SELECT COUNT(*)
        FROM _service_stops
        WHERE departure_seconds IS NULL
        """
    ).fetchone()[0]
    logger.info(
        "Kontrola godzin GTFS: %s odjazdów poza poprawnym zakresem 00:00–29:59 "
        "pominięto w F1.",
        f"{invalid_times:,}",
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE _f1_rows AS
        WITH departures AS (
            SELECT
                ID_Dnia,
                FLOOR(departure_seconds / 3600)::INTEGER AS ID_Godziny,
                SK_Stacja,
                SK_Kurs,
                SK_Przewoznik,
                nr_postoju,
                kierunek,
                departure_seconds
            FROM _service_stops
            WHERE departure_seconds IS NOT NULL
        ),
        metrics AS (
            SELECT *,
                LAG(departure_seconds) OVER (
                    PARTITION BY ID_Dnia, SK_Stacja, kierunek
                    ORDER BY departure_seconds, SK_Kurs, nr_postoju
                ) AS previous_departure,
                FIRST_VALUE(departure_seconds) OVER (
                    PARTITION BY ID_Dnia, SK_Kurs
                    ORDER BY nr_postoju
                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                ) AS course_start_departure
            FROM departures
        )
        SELECT
            ID_Dnia,
            ID_Godziny,
            SK_Stacja,
            SK_Kurs,
            SK_Przewoznik,
            nr_postoju,
            CASE WHEN previous_departure IS NULL THEN NULL
                 ELSE FLOOR((departure_seconds - previous_departure) / 60)::INTEGER
            END AS odstep_min,
            FLOOR((departure_seconds - course_start_departure) / 60)::INTEGER
                AS czas_od_startu_min
        FROM metrics
        """
    )
    timed_load(
        connection,
        logger,
        "Fakt_Zatrzymanie",
        """
        INSERT INTO dw.Fakt_Zatrzymanie (
            ID_Dnia, ID_Godziny, SK_Stacja, SK_Kurs, SK_Przewoznik,
            nr_postoju, liczba_odjazdow, odstep_min, czas_od_startu_min
        )
        SELECT
            ID_Dnia,
            ID_Godziny,
            SK_Stacja,
            SK_Kurs,
            SK_Przewoznik,
            nr_postoju,
            1,
            odstep_min,
            czas_od_startu_min
        FROM _f1_rows
        """,
    )


def load_transfer_fact(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE _transfer_events AS
        SELECT
            ID_Dnia,
            SK_Stacja,
            SK_Kurs,
            SK_Przewoznik,
            MIN(arrival_seconds) AS arrival_seconds,
            MAX(departure_seconds) AS departure_seconds
        FROM _service_stops
        WHERE arrival_seconds IS NOT NULL OR departure_seconds IS NOT NULL
        GROUP BY ID_Dnia, SK_Stacja, SK_Kurs, SK_Przewoznik
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE _transfer_rows AS
        WITH candidates AS (
            SELECT
                a.ID_Dnia,
                FLOOR(a.arrival_seconds / 3600)::INTEGER AS ID_Godziny_Przyj,
                a.SK_Stacja,
                a.SK_Kurs AS SK_Kurs_Przyj,
                b.SK_Kurs AS SK_Kurs_Odj,
                a.SK_Przewoznik AS SK_Przewoznik_Przyj,
                b.SK_Przewoznik AS SK_Przewoznik_Odj,
                b.departure_seconds - a.arrival_seconds AS wait_seconds
            FROM _transfer_events a
            JOIN _transfer_events b
              ON b.ID_Dnia = a.ID_Dnia
             AND b.SK_Stacja = a.SK_Stacja
             AND b.SK_Kurs <> a.SK_Kurs
            JOIN dw.Dim_Stacja s
              ON s.SK_Stacja = a.SK_Stacja AND s.czy_wezel
            WHERE a.arrival_seconds IS NOT NULL
              AND b.departure_seconds IS NOT NULL
              AND b.departure_seconds - a.arrival_seconds BETWEEN 300 AND 1800
        ),
        unique_pairs AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY ID_Dnia, SK_Stacja, SK_Kurs_Przyj, SK_Kurs_Odj
                    ORDER BY wait_seconds
                ) AS pair_rank
            FROM candidates
        )
        SELECT
            ID_Dnia,
            ID_Godziny_Przyj,
            SK_Stacja,
            SK_Kurs_Przyj,
            SK_Kurs_Odj,
            SK_Przewoznik_Przyj,
            SK_Przewoznik_Odj,
            FLOOR(wait_seconds / 60)::INTEGER AS czas_oczekiwania_min
        FROM unique_pairs
        WHERE pair_rank = 1
        """
    )
    timed_load(
        connection,
        logger,
        "Fakt_Przesiadka",
        """
        INSERT INTO dw.Fakt_Przesiadka (
            ID_Dnia, ID_Godziny_Przyj, SK_Stacja, SK_Kurs_Przyj, SK_Kurs_Odj,
            SK_Przewoznik_Przyj, SK_Przewoznik_Odj, czas_oczekiwania_min,
            liczba_par, flaga_okno_10_25
        )
        SELECT
            ID_Dnia,
            ID_Godziny_Przyj,
            SK_Stacja,
            SK_Kurs_Przyj,
            SK_Kurs_Odj,
            SK_Przewoznik_Przyj,
            SK_Przewoznik_Odj,
            czas_oczekiwania_min,
            1,
            CASE WHEN czas_oczekiwania_min BETWEEN 10 AND 25 THEN 1 ELSE 0 END
        FROM _transfer_rows
        """,
    )


def verify_facts(
    connection: duckdb.DuckDBPyConnection, logger: logging.Logger
) -> None:
    checks = (
        (
            "F1",
            """
            SELECT COUNT(*) FROM dw.Fakt_Zatrzymanie
            WHERE ID_Godziny NOT BETWEEN 0 AND 29
               OR liczba_odjazdow <> 1
               OR odstep_min < 0
               OR czas_od_startu_min < 0
            """,
        ),
        (
            "F2",
            """
            SELECT COUNT(*) FROM dw.Fakt_Przesiadka
            WHERE ID_Godziny_Przyj NOT BETWEEN 0 AND 29
               OR czas_oczekiwania_min NOT BETWEEN 5 AND 30
               OR liczba_par <> 1
               OR flaga_okno_10_25 NOT IN (0, 1)
               OR flaga_okno_10_25 <>
                    CASE WHEN czas_oczekiwania_min BETWEEN 10 AND 25 THEN 1 ELSE 0 END
            """,
        ),
        (
            "F3",
            """
            SELECT COUNT(*) FROM dw.Fakt_Zatrzymania_UTK
            WHERE ID_Miesiaca NOT BETWEEN 202501 AND 202512
               OR liczba_zatrzyman < 0
            """,
        ),
    )
    for fact, query in checks:
        invalid = connection.execute(query).fetchone()[0]
        if invalid:
            raise RuntimeError(
                f"Kontrola jakości {fact} nie powiodła się: {invalid} "
                "niepoprawnych wierszy."
            )
        logger.info("Quality gate %s: OK.", fact)


def run_pipeline() -> None:
    logger = configure_logging()
    started = time.perf_counter()
    connection = duckdb.connect(str(DATABASE))
    try:
        require_source_tables(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("DELETE FROM dw.Fakt_Zatrzymanie")
            connection.execute("DELETE FROM dw.Fakt_Przesiadka")
            connection.execute("DELETE FROM dw.Fakt_Zatrzymania_UTK")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

        # DuckDB enforces these deletes against committed FK state.
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("DELETE FROM dw.Dim_Kurs")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("DELETE FROM dw.Dim_Przewoznik")
            connection.execute("DELETE FROM dw.Dim_Stacja")
            connection.execute("DELETE FROM dw.Mostek_Stacji")

            load_station_dimensions(connection, logger)
            load_carrier_dimension(connection, logger)
            load_course_dimension(connection, logger)
            prepare_service_stops(connection)
            load_utk_fact(connection, logger)
            load_planned_stop_fact(connection, logger)
            load_transfer_fact(connection, logger)
            verify_facts(connection, logger)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        logger.info("Pipeline zakończony poprawnie w %.2f s.", time.perf_counter() - started)
    except Exception:
        logger.exception("Pipeline nie powiódł się; transakcja została wycofana.")
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    run_pipeline()
