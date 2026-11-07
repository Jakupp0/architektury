# Metryczki tabel faktów — Etap 3

## `dw.Fakt_Zatrzymanie` — F1: Zatrzymanie planowane

**Ziarnistość:** jeden odjazd pociągu ze stacji GTFS w określonym dniu kursowania, na wskazanym postoju kursu.

**Źródła danych**

- `stg_stop_times`: kolejność postojów, identyfikator stacji oraz godziny przyjazdu i odjazdu.
- `stg_trips` i `stg_routes`: kurs, przewoźnik, typ trasy oraz wykluczenie komunikacji innej niż kolejowa.
- `stg_calendar_dates`: daty aktywności usługi.
- `dw.Mostek_Stacji`, `dw.Dim_Stacja`, `dw.Dim_Kurs`, `dw.Dim_Przewoznik`, `dw.Dim_Czas` i `dw.Dim_Pora_Doby`: klucze wymiarów.

**Transformacje**

1. Połącz kursy z trasami; pozostaw `route_type = 2` (ruch kolejowy, bez ZKA).
2. Rozwiń `stop_times` o daty `calendar_dates` z `exception_type = 1`. `ID_Dnia` jest datą rozpoczęcia kursu; data nie jest przesuwana dla godzin nocnych.
3. Przypisz peron do stacji nadrzędnej przez mostek stacji. Do faktu trafiają tylko stacje należące do wybranego wymiaru województwa dolnośląskiego.
4. Zamień czas GTFS na sekundy i godzinę 0–29, zachowując wartości po północy (24–29).
5. Oblicz `odstep_min` jako całkowitą liczbę minut od poprzedniego odjazdu, z `LAG()` partycjonowanym po `(ID_Dnia, SK_Stacja, kierunek)` i porządkiem czasu odjazdu.
6. Oblicz `czas_od_startu_min` względem pierwszego odjazdu kursu w tym samym dniu. Wstaw `liczba_odjazdow = 1`.

**Kontrola jakości i anomalie**

- Wymagany jest przygotowany, ciągły wymiar kalendarza oraz wymiar godzin z zakresem 0–29.
- Niepoprawne czasy, godziny spoza zakresu 0–29 lub niepoprawne minuty/sekundy nie trafiają do faktu; liczba pominiętych odjazdów jest zapisywana w logu.
- Kursy i klucze wymiarów łączone są przez relacje stagingowe i mostek; kontrola kluczy obcych jest egzekwowana przez DuckDB.
- Odstęp dla pierwszego odjazdu w partycji pozostaje `NULL`; czasy od startu nie mogą być ujemne.

**Weryfikacja**

- Quality gate sprawdza poprawność `ID_Godziny`, `liczba_odjazdow = 1` i nieujemne `czas_od_startu_min`.
- Wykonanie 2026-11-07: **323 624 wiersze**; odrzucono **192** odjazdy z godzinami poza poprawnym zakresem. Quality gate: OK.

## `dw.Fakt_Przesiadka` — F2: Przesiadka potencjalna

**Ziarnistość:** para kursów przyjeżdżającego i odjeżdżającego pociągu w tym samym dniu i jednym z sześciu węzłów.

**Źródła danych**

- `stg_stop_times`, `stg_trips`, `stg_routes` i `stg_calendar_dates`.
- `dw.Dim_Stacja` (flaga `czy_wezel`), `dw.Dim_Kurs`, `dw.Dim_Przewoznik`, `dw.Dim_Pora_Doby` oraz `dw.Mostek_Stacji`.

**Transformacje**

1. Zbuduj zdarzenia kursu w stacji nadrzędnej; połącz odjazd/przyjazd z usługą i konkretną datą kursowania.
2. Ogranicz do stacji z `czy_wezel = TRUE` oraz poprawnych czasów w zakresie obsługiwanym przez wymiar godzin.
3. Wykonaj autozłączenie zdarzeń przyjazdu i odjazdu dla tego samego dnia i węzła, z różnymi kursami.
4. Zachowaj pary, dla których różnica czasu w sekundach wynosi 300–1800.
5. Przypisz `ID_Godziny_Przyj` z godziny przyjazdu kursu A. Wylicz `czas_oczekiwania_min` jako pełne minuty (zaokrąglenie w dół), `liczba_par = 1` i `flaga_okno_10_25` dla czasu oczekiwania 10–25 minut.
6. Jeżeli wiele postojów peronowych reprezentuje tę samą parę kursów w tym samym węźle, zachowaj jeden rekord z najkrótszym kwalifikującym się oczekiwaniem, zgodnie z ziarnistością klucza głównego.

**Kontrola jakości i anomalie**

- Węzły pochodzą z kontrolowanej listy sześciu stacji; nieważne godziny są pomijane.
- Warunek 5–30 minut jest sprawdzany w sekundach przed przeliczeniem na minuty, zatem granice 5 i 30 minut są włączone.
- Ten sam kurs nie może wystąpić jako oba człony przesiadki. Klucz główny zapobiega powieleniu par.
- Klucze `ID_Godziny_Przyj`, kursów, stacji i przewoźników są sprawdzane przez więzy integralności DuckDB.

**Weryfikacja**

- Quality gate sprawdza zakres godziny 0–29, oczekiwanie 5–30 minut, `liczba_par = 1` oraz flagę binarną.
- Wykonanie 2026-11-07: **292 554 wiersze**; Quality gate: OK.

## `dw.Fakt_Zatrzymania_UTK` — F3: Zatrzymania zrealizowane

**Ziarnistość:** stacja, przewoźnik i miesiąc roku 2025.

**Źródła danych**

- `stg_utk_zatrzymania`: stacja, przewoźnik i 12 kolumn miesięcznych.
- `dw.Dim_Miesiac`, `dw.Dim_Stacja` i `dw.Dim_Przewoznik`.

**Transformacje**

1. Wykonaj `UNPIVOT INCLUDE NULLS` na kolumnach styczeń–grudzień; kolumna `Suma końcowa` nie jest miarą miesięczną i nie jest używana.
2. Zamień `NULL` w miesięcznej liczbie zatrzymań na zero.
3. Ogranicz dane do stacji obecnych w `Dim_Stacja` (325 stacji Dolnego Śląska), dopasuj przewoźników po nazwie UTK i miesiące do kluczy `ID_Miesiaca` 202501–202512.
4. Zagreguj rekordy do ziarna faktu i wstaw `SUM(liczba_zatrzyman)`.

**Kontrola jakości i anomalie**

- Nieznane nazwy stacji spoza regionalnego zakresu wymiaru są pomijane — to zamierzone ograniczenie zakresu, a ich liczba jest raportowana.
- Brak klucza przewoźnika dla stacji w zakresie 325 stacji przerywa ładowanie; nie jest zamieniany na pozornie poprawną wartość.
- Miesiąc musi należeć do 2025 r., a miara nie może być ujemna. Klucz główny zapobiega duplikatom ziarna.

**Weryfikacja**

- Quality gate sprawdza zakres kluczy miesięcy i nieujemność miary; FK sprawdzają kompletność kluczy wymiarów.
- Wykonanie 2026-11-07: **5 676 wierszy**; **53 520** wierszy miesięcznych poza regionalnym zakresem stacji oraz **0** brakujących kluczy przewoźników wewnątrz tego zakresu. Quality gate: OK.

## Powtarzalność i log wykonania

`03_load_dw.py` przebudowuje wymiary biznesowe typu SCD 1 i fakty w transakcyjnej kolejności zgodnej z zależnościami kluczy obcych. W DuckDB czyszczenie tabel podrzędnych i wymiaru kursu jest zatwierdzane przed przebudową wymiarów; docelowe ładowanie wymiarów i faktów pozostaje transakcyjne. Ponowne uruchomienie skryptu odtwarza te same wymiary i ziarna faktów.

Szczegółowy log liczebności, czasu wykonania, kontroli jakości i błędów jest zapisywany do [`03_load_dw.log`](./03_load_dw.log).
