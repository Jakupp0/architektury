# Obserwatorium kolei — Dolny Śląsk

Angular 21 + Apache ECharts. Dashboard odpowiada na Z1–Z10 z prezentacji Etapu I (slajdy 6–7). Interfejs i definicje są po polsku. Dane są prawdziwym eksportem lokalnej hurtowni, nie atrapą.

## Opublikowana wersja

Dashboard: https://kolej-dolny-slask-bi-2026.web.app/

Projekt Firebase: `kolej-dolny-slask-bi-2026` (Spark). [Konsola Hosting](https://console.firebase.google.com/project/kolej-dolny-slask-bi-2026/hosting/sites). Wdrożono statyczny build Angular z danymi JSON; kod źródłowy pozostaje w `src/`.

## Uruchomienie

Dla nowego środowiska użyj Node.js 22.12+ albo 24.x: część zależności narzędzia Firebase CLI wymaga Node.js 22 lub nowszego. Wszystkie polecenia npm wykonuj w katalogu `dashboard`.

Po sklonowaniu repozytorium zainstaluj zależności zapisane w `package-lock.json`:

```powershell
npm ci
npm start
```

Adres lokalny: http://127.0.0.1:4200 .

Podgląd wersji produkcyjnej:

```powershell
npm run build
npm run preview
```

Build powstaje w `dist/kolej/browser`. Po jego utworzeniu można też uruchomić `Uruchom.cmd` albo `node serve.mjs`. Katalog `dist/` nie jest przechowywany w Git i nie istnieje po świeżym klonowaniu.

## Wdrożenie do Firebase Hosting

Firebase CLI jest lokalną zależnością projektu; `npm ci` instaluje również wersję CLI zapisaną w lockfile. Nie trzeba instalować go globalnie.

Przy pierwszym wdrożeniu na danym komputerze zaloguj się kontem Google mającym uprawnienia do projektu `kolej-dolny-slask-bi-2026`:

```powershell
npm run firebase:login
```

Publikacja po zmianie kodu lub danych:

```powershell
npm run deploy
```

Ta komenda kolejno sprawdza TypeScript, tworzy produkcyjny build i wdraża go wyłącznie do Firebase Hosting w projekcie `kolej-dolny-slask-bi-2026`. Błąd sprawdzenia lub budowania zatrzymuje publikację. Adres po wdrożeniu: https://kolej-dolny-slask-bi-2026.web.app/ .

Identyfikator projektu jest jawnie podany w skrypcie `deploy` w `package.json` i jako domyślny w `.firebaserc`. Przy przenoszeniu aplikacji do innego projektu zmień oba miejsca. Skrypt nie przebudowuje hurtowni DuckDB; publikuje aktualne pliki z `public/data/` po włączeniu ich do buildu.

Firestore i serwer aplikacyjny nie są potrzebne do odczytu tej migawki. Aplikacja nie deklaruje logowania ani zabezpieczeń opartych na rolach. Statyczne pliki danych są dostępne dla każdego odbiorcy hostowanego serwisu.

## Co dodać do repozytorium

Dodaj katalog `dashboard/`, zachowując jego `.gitignore`. Śledzone powinny być źródła `src/`, cała migawka `public/data/`, `package.json`, `package-lock.json`, konfiguracje Angular/TypeScript/Firebase, skrypty podglądu i ten README. Pliki JSON są wymagane do zbudowania dashboardu na innym komputerze.

`.gitignore` wyklucza zależności, build, cache Firebase, logi, lokalne raporty i zrzuty ekranu oraz typowe pliki z sekretami. Nie dodawaj tokenów logowania, kluczy kont serwisowych ani plików `.env` z rzeczywistymi danymi dostępowymi. Wykluczenia nie usuwają plików już śledzonych przez Git.

`.firebaserc` i `firebase.json` można dodać: zawierają identyfikator projektu i konfigurację hostingu, bez danych logowania. Sam identyfikator nie uprawnia do publikacji. Pole `private: true` w `package.json` blokuje publikowanie pakietu do npm; nie określa widoczności repozytorium GitHub ani strony.

## Aktualizacja danych

Z katalogu nadrzędnego projektu:

```powershell
.venv\Scripts\python.exe run_pipeline.py
```

Pipeline aktualizuje staging, przebudowuje fakty, tworzy `dw.Fakt_Relacja`, eksportuje dane JSON i CSV Power BI, uruchamia testy. Po zmianie danych uruchom `npm run deploy` w katalogu dashboard, aby zbudować i opublikować nową migawkę (albo samo `npm run build` do podglądu lokalnego).

Samo ponowne przeliczenie analiz i eksportu istniejącego modelu:

```powershell
.venv\Scripts\python.exe export_dashboard.py
.venv\Scripts\python.exe test_dashboard_data.py
```

## Zmiany obliczeń

- `Fakt_Zatrzymanie` zachowuje wszystkie poprawne postoje w okresie ważności GTFS. `liczba_odjazdow` ma wartość 0 dla `pickup_type=1`, w pozostałych przypadkach 1. `COUNT(*)` nie jest liczbą odjazdów pasażerskich.
- Dodano dokładne sekundy przyjazdu i odjazdu oraz reguły wsiadania/wysiadania. Wsiadanie warunkowe (2/3) pozostaje w ofercie i jest opisane w metodologii.
- F2 wymaga dozwolonej wysiadki z pierwszego i dozwolonego wsiadania do drugiego pociągu. Oczekiwanie 5–30 min, a okno jakości 10–25 min są oceniane na dokładnych sekundach. Jedna najkrótsza kwalifikująca się para na dzień, węzeł i dwa kursy.
- `Fakt_Relacja` zawiera regionalne pary A–B i poprawny czas `arrival(B) - departure(A)`. Uwzględnia stacje pośrednie. Przy powtórnych wizytach zachowuje najkrótszą podróż danego kursu.
- Z2 liczy odstępy na rzeczywistej parze A–B w oknie 06:00–22:00 włącznie. Równoczesne odjazdy są jedną okazją podróży. Nie uwzględnia przerw na brzegach okna ani między dniami.
- Z1 uwzględnia dni bez kursów w mianowniku; Z3 jest ilorazem średnich, nie porównaniem sum 22 i 9 dni. Brak dopasowania GTFS pozostaje NULL.
- Z4 średnia po filtracji jest ważona liczbą kursów. Z5 sumuje zbiory stacji docelowych, nie liczniki z rozłącznych filtrów.
- Z6 mediana jest liczona z rozkładu par, a nie jako średnia median węzłów. Z7 mianownik obejmuje wyłącznie zmiany przewoźnika.
- Z8 używa środka przedziału UTK i jest wskaźnikiem orientacyjnym. Z9/Z10 korzystają z oddzielnej miesięcznej statystyki 2025. Z10 zawsze zachowuje pełny mianownik stacji.

## Zakres i ograniczenia

GTFS: 12.11–12.12.2026, 22 dni pn.–pt., 9 sobót/niedziel. UTK: rok 2025. Nie analizujemy całej jesieni 2026. Święta nie są odrębną kategorią. Godziny 24–29 pozostają przypisane do dnia rozpoczęcia kursu; przesiadki między różnymi dniami kursowania nie są łączone. Nie modelujemy czasu dojścia między peronami ani faktycznej realizacji połączeń.

325 stacji, 306 dopasowań GTFS, 19 bez dopasowania. 286 stacji z pasażerskimi odjazdami w migawce. UTK miesięczne obejmuje 300 stacji; brak stacji w źródle nie jest zerem. Puste komórki miesięcy w istniejącym rekordzie UTK są interpretowane zgodnie z ETL jako zero.

Pliki `dashboard_validation.json` i `dashboard_build_result.json` dokumentują walidację i aktualne liczności. Wcześniejszy PBIX/PDF w `powerbi/Kolej_Z1` zawiera poprzedni cache; odśwież PBIX po zmianie CSV. Dashboard Angular używa już poprawionych danych.

## Scenariusz demonstracji

1. Porównaj sześć węzłów: oferta w budni i weekend, procent redukcji.
2. Wybierz Wrocław Główny jako początek relacji, przełącz typ dnia, wybierz cel. Sprawdź odstępy 06–22 i min/średni/max czas podróży.
3. Otwórz Przesiadki. Zawęź przyjazd/odjazd do KD i PKP Intercity. Porównaj średnią, medianę i udział okna 10–25 minut.
4. Otwórz Popyt i przewoźnicy. Sprawdź miesięczny trend 2025 i udziały firm w węźle, następnie relację oferty do wymiany pasażerskiej.
5. Wyeksportuj tabelę CSV lub wydrukuj wybraną stronę do PDF. Definicje wszystkich pytań są na stronie Dane i metodologia.

## Układ 3–30–300

W 3 sekundy użytkownik odczytuje główne KPI. W 30 sekund porównuje stacje i węzły na wykresach. W 300 sekund korzysta z filtrów, rozwija tabele, sprawdza relację i eksportuje wyniki. Docelowy odbiorca: osoba odpowiedzialna za planowanie i rozwój regionalnej oferty kolejowej.
