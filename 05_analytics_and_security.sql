CREATE SCHEMA IF NOT EXISTS dw;

CREATE OR REPLACE VIEW dw.v_kpi_z1_oferta_dni AS
WITH station_days AS (
    SELECT
        s.SK_Stacja,
        s.nazwa AS stacja,
        c.ID_Dnia,
        c.typ_dnia,
        COALESCE(SUM(f.liczba_odjazdow), 0)::BIGINT AS liczba_odjazdow
    FROM dw.Dim_Stacja s
    CROSS JOIN dw.Dim_Czas c
    LEFT JOIN dw.Fakt_Zatrzymanie f
      ON f.SK_Stacja = s.SK_Stacja
     AND f.ID_Dnia = c.ID_Dnia
    GROUP BY s.SK_Stacja, s.nazwa, c.ID_Dnia, c.typ_dnia
)
SELECT
    SK_Stacja,
    stacja,
    typ_dnia,
    COUNT(*) AS liczba_dni,
    SUM(liczba_odjazdow) AS suma_odjazdow,
    AVG(liczba_odjazdow)::DOUBLE AS srednia_dzienna_liczba_odjazdow
FROM station_days
GROUP BY SK_Stacja, stacja, typ_dnia;

CREATE OR REPLACE VIEW dw.v_kpi_z6_z7_jakosc_wezlow AS
SELECT
    s.SK_Stacja,
    s.nazwa AS wezel,
    COUNT(f.SK_Stacja) AS liczba_par_przesiadkowych,
    AVG(f.czas_oczekiwania_min)::DOUBLE AS sredni_czas_oczekiwania_min,
    MEDIAN(f.czas_oczekiwania_min)::DOUBLE AS mediana_czasu_oczekiwania_min,
    COALESCE(SUM(f.liczba_par) FILTER (
        WHERE f.flaga_okno_10_25 = 1
    ), 0) AS liczba_par_w_oknie_10_25,
    CASE
        WHEN COALESCE(SUM(f.liczba_par), 0) = 0 THEN NULL
        ELSE 100.0 * COALESCE(SUM(f.liczba_par) FILTER (
            WHERE f.flaga_okno_10_25 = 1
        ), 0) / SUM(f.liczba_par)
    END AS odsetek_w_oknie_10_25_pct,
    COALESCE(SUM(f.liczba_par) FILTER (
        WHERE f.SK_Przewoznik_Przyj <> f.SK_Przewoznik_Odj
    ), 0) AS liczba_par_przy_zmianie_przewoznika,
    CASE
        WHEN COALESCE(SUM(f.liczba_par) FILTER (
            WHERE f.SK_Przewoznik_Przyj <> f.SK_Przewoznik_Odj
        ), 0) = 0 THEN NULL
        ELSE 100.0 * COALESCE(SUM(f.liczba_par) FILTER (
            WHERE f.SK_Przewoznik_Przyj <> f.SK_Przewoznik_Odj
              AND f.flaga_okno_10_25 = 1
        ), 0) / SUM(f.liczba_par) FILTER (
            WHERE f.SK_Przewoznik_Przyj <> f.SK_Przewoznik_Odj
        )
    END AS odsetek_w_oknie_przy_zmianie_przewoznika_pct
FROM dw.Dim_Stacja s
LEFT JOIN dw.Fakt_Przesiadka f ON f.SK_Stacja = s.SK_Stacja
WHERE s.czy_wezel IS TRUE
GROUP BY s.SK_Stacja, s.nazwa;

CREATE OR REPLACE VIEW dw.v_kpi_z8_intensywnosc_oferty AS
WITH station_offer AS (
    SELECT
        SK_Stacja,
        SUM(suma_odjazdow)::DOUBLE / NULLIF(SUM(liczba_dni), 0)
            AS srednia_dzienna_liczba_odjazdow
    FROM dw.v_kpi_z1_oferta_dni
    GROUP BY SK_Stacja
),
station_market AS (
    SELECT
        s.SK_Stacja,
        s.nazwa AS stacja,
        s.powiat,
        s.wymiana_min,
        s.wymiana_max,
        CASE
            WHEN s.wymiana_min IS NULL OR s.wymiana_max IS NULL THEN NULL
            ELSE (s.wymiana_min + s.wymiana_max) / 2.0
        END AS szacowana_dobowa_wymiana_pasazerska
    FROM dw.Dim_Stacja s
)
SELECT
    m.SK_Stacja,
    m.stacja,
    m.powiat,
    o.srednia_dzienna_liczba_odjazdow,
    m.wymiana_min,
    m.wymiana_max,
    m.szacowana_dobowa_wymiana_pasazerska,
    CASE
        WHEN m.szacowana_dobowa_wymiana_pasazerska > 0
            THEN 1000.0 * o.srednia_dzienna_liczba_odjazdow
                 / m.szacowana_dobowa_wymiana_pasazerska
        ELSE NULL
    END AS odjazdy_na_1000_pasazerow
FROM station_market m
LEFT JOIN station_offer o ON o.SK_Stacja = m.SK_Stacja;

CREATE OR REPLACE VIEW dw.v_role_planista_rozkladu AS
SELECT
    f.ID_Dnia,
    c.typ_dnia,
    f.ID_Godziny,
    f.SK_Stacja,
    f.SK_Kurs,
    k.numer_pociagu,
    k.kategoria,
    k.linia,
    k.stacja_poczatkowa,
    k.stacja_koncowa,
    k.kierunek,
    f.nr_postoju,
    f.liczba_odjazdow,
    f.odstep_min,
    f.czas_od_startu_min
FROM dw.Fakt_Zatrzymanie f
JOIN dw.Dim_Kurs k ON k.SK_Kurs = f.SK_Kurs
JOIN dw.Dim_Czas c ON c.ID_Dnia = f.ID_Dnia;

CREATE OR REPLACE VIEW dw.v_role_analityk_wezlow AS
SELECT
    f.ID_Dnia,
    s.SK_Stacja,
    s.nazwa AS wezel,
    f.ID_Godziny_Przyj,
    f.SK_Kurs_Przyj,
    f.SK_Kurs_Odj,
    f.SK_Przewoznik_Przyj,
    f.SK_Przewoznik_Odj,
    f.czas_oczekiwania_min,
    f.liczba_par,
    f.flaga_okno_10_25
FROM dw.Fakt_Przesiadka f
JOIN dw.Dim_Stacja s ON s.SK_Stacja = f.SK_Stacja
WHERE s.czy_wezel IS TRUE;

CREATE OR REPLACE VIEW dw.v_role_kierownik_rozwoju_utk AS
SELECT
    m.ID_Miesiaca,
    m.rok,
    m.numer_miesiaca,
    m.nazwa_miesiaca,
    s.SK_Stacja,
    s.nazwa AS stacja,
    p.SK_Przewoznik,
    p.nazwa_UTK AS przewoznik,
    f.liczba_zatrzyman
FROM dw.Fakt_Zatrzymania_UTK f
JOIN dw.Dim_Miesiac m ON m.ID_Miesiaca = f.ID_Miesiaca
JOIN dw.Dim_Stacja s ON s.SK_Stacja = f.SK_Stacja
JOIN dw.Dim_Przewoznik p ON p.SK_Przewoznik = f.SK_Przewoznik;

CREATE OR REPLACE VIEW dw.v_role_kierownik_rozwoju_intensywnosc AS
SELECT
    stacja,
    powiat,
    srednia_dzienna_liczba_odjazdow,
    wymiana_min,
    wymiana_max,
    szacowana_dobowa_wymiana_pasazerska,
    odjazdy_na_1000_pasazerow
FROM dw.v_kpi_z8_intensywnosc_oferty;

-- DuckDB does not implement CREATE ROLE or GRANT. These entries describe
-- restricted views for authorization by the application or a SQL proxy.
CREATE TABLE IF NOT EXISTS dw.Security_Perspectives (
    profil VARCHAR PRIMARY KEY,
    widok VARCHAR NOT NULL,
    opis VARCHAR NOT NULL
);

DELETE FROM dw.Security_Perspectives;

INSERT INTO dw.Security_Perspectives (profil, widok, opis) VALUES
    (
        'Planista_Rozkladu',
        'dw.v_role_planista_rozkladu',
        'Oferta, takt i relacje; projekcja faktu zatrzyman, kursow i czasu.'
    ),
    (
        'Analityk_Wezlow',
        'dw.v_role_analityk_wezlow',
        'Przesiadki ograniczone do stacji oznaczonych jako wezly.'
    ),
    (
        'Kierownik_Rozwoju',
        'dw.v_role_kierownik_rozwoju_utk; dw.v_role_kierownik_rozwoju_intensywnosc',
        'Miesieczne statystyki UTK i zagregowany wskaznik intensywnosci oferty.'
    );
