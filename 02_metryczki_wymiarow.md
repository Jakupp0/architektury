# Metryczki wymiarów hurtowni danych

Metryczki opisują kontrakt ładowania dla Agenta 3. Wymiary biznesowe oraz
`Mostek_Stacji` są tworzone jako puste tabele w `02_create_dw_schema.py`;
zasilenie ich należy do kolejnego etapu ETL. Wymiarom z kluczem zastępczym
należy nadawać stabilne wartości `SK_*` przez `ROW_NUMBER()` po stabilnym
porządku klucza naturalnego, a nie po kolejności odczytu pliku.

## `dw.Dim_Stacja`

1. **Źródło danych:** `stg_stops` (identyfikator, nazwa i współrzędne GTFS),
   `stg_utk_wymiana` (powiat, gmina, województwo i wartości wymiany UTK) oraz
   reguły biznesowe projektu dla 6 głównych węzłów.
2. **Transformacje:** Perony/obiekty podrzędne GTFS agregować do stacji
   `parent_station`, gdy taki identyfikator jest dostępny; w przeciwnym razie
   użyć identyfikatora przystanku jako klucza. Dopasować dane UTK przez
   `Mostek_Stacji`, zachować `id_GTFS`, przyciąć teksty (`TRIM`), rzutować
   współrzędne na `DECIMAL(9,6)` i pola wymiany na `INTEGER`. Przedział
   wymiany rozłożyć na minimum i maksimum zgodnie z wartością źródłową.
   `czy_wezel` ustalać z listy 6 węzłów projektu, a kategorię wymiany zachować
   jako atrybut UTK.
3. **Kontrola jakości:** `SK_Stacja` i `id_GTFS` mają być niepuste i unikalne;
   współrzędne muszą mieścić się w zakresie szerokości/długości geograficznej;
   `wymiana_min <= wymiana_max`. Stacja dolnośląska bez dopasowania UTK nie
   może być po cichu pominięta — zachować stację, pozostawić atrybuty UTK
   puste i zapisać przypadek do logu/rejestru błędów. Zweryfikować oczekiwane
   325 stacji i obecność sześciu węzłów; rozbieżność wyjaśnić przed akceptacją
   ładowania.
4. **Weryfikacja:** porównać liczbę wierszy z liczbą unikalnych
   dolnośląskich identyfikatorów stacji po agregacji parent-station; sprawdzić
   duplikaty `SK_Stacja`/`id_GTFS`, NULL-e w kluczach, zakres współrzędnych,
   poprawność przedziałów wymiany i listę sześciu węzłów.

## `dw.Dim_Przewoznik`

1. **Źródło danych:** `stg_agency` dla agencji i nazw GTFS oraz
   `stg_utk_zatrzymania` dla nazw przewoźników i klasyfikacji UTK.
2. **Transformacje:** Oczyścić nazwy przez `TRIM`, dopasować nazwy GTFS do
   UTK przez jawne mapowanie normalizowane/aliasów, zachować oryginalną nazwę
   źródłową w `nazwa_GTFS` i nazwę dopasowaną w `nazwa_UTK`. `grupa` przypisać
   według słownika projektu (`regionalny`/`dalekobieżny`). Nadać stabilny
   `SK_Przewoznik` według `id_agencji`.
3. **Kontrola jakości:** `SK_Przewoznik` unikalny i niepusty; `id_agencji`
   unikalne, gdy źródło je udostępnia. Brak lub niejednoznaczność mapowania do
   UTK zapisać do rejestru błędów i rozstrzygnąć słownikiem, a nie heurystyką
   nadpisującą nazwę źródłową. `grupa` ma należeć do uzgodnionego słownika.
4. **Weryfikacja:** porównać unikalne ID agencji i nazwy przewoźników z obu
   źródeł; sprawdzić unikalność SK/naturalnych kluczy, NULL-e, wartości grupy
   i kompletność mapowania przewoźników występujących w UTK.

## `dw.Dim_Kurs`

1. **Źródło danych:** `stg_trips`, `stg_routes` oraz `stg_stop_times`.
2. **Transformacje:** Złączyć kurs z trasą po `route_id`; z `stop_times`
   pobrać pierwszy i ostatni przystanek według `stop_sequence` oraz
   `direction_id`/`trip_headsign` według dostępnych atrybutów GTFS. Atrybuty
   numeru pociągu, kategorii i linii wyprowadzić z odpowiednich pól źródłowych
   bez zmiany ich identyfikatorów tekstowych. `stacja_poczatkowa` i
   `stacja_koncowa` zapisać jako klucze `Dim_Stacja`; nadać stabilny
   `SK_Kurs` według `trip_id`. Staging wyklucza już ZKA/autobusy.
3. **Kontrola jakości:** `trip_id` i `SK_Kurs` nie mogą być NULL ani
   zduplikowane; każdy kurs musi mieć trasę i co najmniej jeden postój.
   Nieznane stacje początkowe/końcowe albo brak wymaganej trasy blokują
   rekord i są raportowane — nie tworzyć pozornych kluczy. Potwierdzić brak
   `route_type = 3` i oznaczeń ZKA.
4. **Weryfikacja:** liczba wierszy powinna odpowiadać liczbie unikalnych
   `trip_id` w `stg_trips` z kompletnym przebiegiem; wykonać testy unikalności,
   NULL-i i FK obu stacji, a także anty-złączenie potwierdzające brak kursów
   autobusowych/ZKA.

## `dw.Dim_Czas`

1. **Źródło danych:** daty z `stg_calendar_dates.date`; tabela wymiaru jest
   kalendarzem ciągłym od najmniejszej do największej poprawnej daty ze
   stagingu. W bieżącym stagingu zakres wynosi 2025-12-14–2026-12-12.
2. **Transformacje:** Parsować datę GTFS `YYYYMMDD` do `DATE`. Dla każdego
   dnia włącznie wyliczyć `ID_Dnia = data`, polską nazwę dnia tygodnia,
   `typ_dnia` (`Weekend` dla soboty/niedzieli, w pozostałych przypadkach
   `Dzień roboczy`), numer miesiąca, kwartał i rok. Dzień kursu pozostaje
   datą jego rozpoczęcia; godziny 24–29 nie przesuwają daty.
3. **Kontrola jakości:** Brak tabeli/kolumny, pusty zbiór lub data w formacie
   innym niż `YYYYMMDD` przerywają transakcję. Zakres musi być ciągły,
   `ID_Dnia = data`, a dzień tygodnia i atrybuty miesiąca/kwartału muszą być
   zgodne z kalendarzem. Wyjątki usług GTFS nie zmieniają definicji weekendu.
4. **Weryfikacja:** Liczba rekordów ma wynosić liczbę dni kalendarzowych od
   minimum do maksimum włącznie (w tym stagingu 364); sprawdzić PK, brak
   NULL-i, brak luk dat oraz zgodność wartości dnia tygodnia z datą.

## `dw.Dim_Miesiac`

1. **Źródło danych:** Statyczny kalendarz gregoriański dla miesięcznych
   danych UTK za 2025 r.
2. **Transformacje:** Generować dokładnie 12 wierszy. Ustawić
   `ID_Miesiaca = 2025 * 100 + numer_miesiaca`, polską nazwę miesiąca,
   `kwartal = ((numer_miesiaca - 1) // 3) + 1` i `rok = 2025`.
3. **Kontrola jakości:** Dopuszczalne numery miesięcy to 1–12, każdy
   dokładnie raz; klucze mają być unikalne i zgodne z rokiem oraz numerem
   miesiąca. Każda niezgodność przerywa ładowanie.
4. **Weryfikacja:** `COUNT(*) = 12`, `COUNT(DISTINCT ID_Miesiaca) = 12`;
   sprawdzić zakres numerów, nazwy, kwartały 1–4 i klucze 202501–202512.

## `dw.Dim_Pora_Doby`

1. **Źródło danych:** Deterministyczny słownik godzin rozkładowych GTFS.
2. **Transformacje:** Generować `ID_Godziny` od 0 do 29 i etykietę
   `HH:00`, zachowując wartości 24–29 bez normalizacji do kolejnej doby.
   Przedziały są lewostronnie domknięte i prawostronnie otwarte:
   00–05 oraz 22–29 `Noc`, 06–08 `Szczyt poranny`, 09–13 `Międzyszczyt`,
   14–17 `Szczyt popołudniowy`, 18–21 `Wieczór`. Flaga po północy jest
   prawdziwa dokładnie dla godzin 24–29.
3. **Kontrola jakości:** Wymagane są wszystkie i tylko klucze 0–29, unikalne
   etykiety, niepuste kategorie oraz zgodność flagi z `ID_Godziny >= 24`.
   Odchylenie przerywa ładowanie.
4. **Weryfikacja:** `COUNT(*) = 30`, `COUNT(DISTINCT ID_Godziny) = 30`;
   sprawdzić etykiety 00:00–29:00, liczbę sześciu godzin z flagą po północy
   i zgodność klasyfikacji wszystkich granic przedziałów.

## `dw.Mostek_Stacji`

1. **Źródło danych:** `stg_stops` i `stg_utk_wymiana`; dopasowania
   automatyczne oraz jawne wyjątki zatwierdzone biznesowo dla nazw stacji.
2. **Transformacje:** Wykonać kolejno dopasowanie dokładne po
   znormalizowanej nazwie, dopasowanie po kontrolowanej normalizacji (trim,
   małe litery, ujednolicenie białych znaków i polskich znaków według
   zatwierdzonego słownika), a następnie ręczne nadpisania zatwierdzone w
   mapowaniu. Zapisać `id_GTFS`, nazwę UTK i metodę
   (`AUTO_EXACT`, `NORMALIZED` lub `MANUAL_OVERRIDE`). Nie przyjmować
   niezweryfikowanego dopasowania przybliżonego.
3. **Kontrola jakości:** Jeden `id_GTFS` może wskazywać najwyżej jedną
   nazwę UTK; nazwy niejednoznaczne lub niedopasowane kierować do kolejki
   ręcznej i logować. Wartość `metoda` musi być jedną z trzech powyższych.
   Akceptacja mapowania wymaga braku kolizji wielu do jednego, chyba że jest
   zatwierdzona jako alias stacji.
4. **Weryfikacja:** `id_GTFS` ma być unikalny i niepusty; sprawdzić
   dozwolone metody, NULL-e, liczbę nierozstrzygniętych nazw oraz ręcznie
   przejrzeć rekordy `NORMALIZED` i `MANUAL_OVERRIDE`.

## Raport weryfikacji wymiarów statycznych

Weryfikację wykonano na `kolej.duckdb` po uruchomieniu
`02_create_dw_schema.py`. Wszystkie trzy asercje skryptu przeszły: prawidłowe
liczności, poprawność atrybutów daty/miesiąca/godziny oraz ciągłość kalendarza.

```sql
SELECT count(*) FROM dw.Dim_Czas;      -- 364
SELECT count(*) FROM dw.Dim_Miesiac;   -- 12
SELECT count(*) FROM dw.Dim_Pora_Doby; -- 30
```

`Dim_Czas` obejmuje ciągły zakres `2025-12-14`–`2026-12-12` (364 dni).
`Dim_Miesiac` zawiera klucze `202501`–`202512`; `Dim_Pora_Doby` zawiera
klucze 0–29, w tym 6 wierszy z `flaga_po_polnocy = TRUE`. Potwierdzono
utworzenie 10 tabel w schemacie `dw` (7 wymiarów/mostek i 3 fakty);
wymiary biznesowe, mostek oraz fakty są puste i gotowe do zasilenia przez
Agenta 3.
