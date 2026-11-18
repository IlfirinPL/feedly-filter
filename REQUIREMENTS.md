# feedly-filter — wymagania

Status: **v0.4 — implementacja rdzenia i GUI (sekcja 13), dopisane podgląd reguł, wymuszony przebieg i szczegóły przebiegu**. Pozycje w sekcji „Założenia" wymagają jeszcze potwierdzenia.

## 1. Cel

Narzędzie automatycznie oznacza jako przeczytane wpisy w Feedly, które:

1. pasują do reguł **czarnej listy** ustawianych per kanał (feed),
2. są **starsze niż N dni** (aging, domyślnie 7 dni), liczone od daty publikacji wpisu.

Konfiguracja jest przechowywana w katalogu `data/`. Narzędzie działa w Dockerze, a prosty interfejs webowy jest dostępny na porcie **8080**.

## 2. Zakres

### W zakresie

- Logowanie do konta Feedly loginem i hasłem, z użyciem przeglądarki headless; sesja jest wyciągana i ponownie używana przy zapytaniach do API webowego.
- Pobieranie listy subskrypcji (kanałów) i ich nieprzeczytanych wpisów.
- Reguły czarnej listy per kanał oraz reguły globalne i wyjątki (whitelist).
- Aging: oznaczanie jako przeczytane wpisów starszych niż N dni (globalnie, z możliwością nadpisania per kanał).
- Częstotliwość sprawdzania ustawiana per kanał (feedy z większą liczbą wpisów są sprawdzane częściej).
- Prosty GUI webowy (port 8080).
- Konfiguracja w `data/config.yaml`, historia w SQLite w `data/`.
- Docker + `docker-compose.yml`.
- GitHub Actions: build i publikacja obrazu Dockera do GHCR.
- Licencja MIT.

### Poza zakresem (na razie)

- Czytnik wpisów / zastępowanie interfejsu Feedly.
- Wielu użytkowników i wiele kont Feedly (jedno konto).
- Usuwanie wpisów — działamy wyłącznie przez oznaczenie jako przeczytane.
- Klasyfikacja treści (ML, NLP) — tylko dopasowanie reguł tekstowych.
- Autoryzacja do GUI — realizowana na innej warstwie (sieć zaufana, ewentualnie reverse proxy).

## 3. Wymagania funkcjonalne

### 3.1 Logowanie

- FR-1: Logowanie loginem i hasłem do feedly.com. Login i hasło z pliku `data/auth.yaml` (klucze `feedly.username`, `feedly.password`).
- FR-2: Logowanie wykonuje przeglądarka headless (Chromium przez Playwright). Po zalogowaniu narzędzie wyciąga cookies / token sesji i dalej komunikuje się z API przez zwykły klient HTTP (bez przeglądarki w każdym przebiegu).
- FR-3: Sesja jest zapisywana w `data/session.json` (plik wykluczony z gita). Po wygaśnięciu sesji (odpowiedź 401/403) narzędzie wykonuje ponowne logowanie headless i kontynuuje przebieg.
- FR-3a: Jeśli Feedly wydaje token sesji wraz z mechanizmem odświeżania (refresh token lub odnawialne cookies), narzędzie zapisuje je w `data/session.json` po pierwszym udanym logowaniu i **używa ich w kolejnych przebiegach** — bez logowania headless.
- FR-3b: Przed każdym przebiegiem narzędzie sprawdza ważność tokenu (czas wygaśnięcia lub próba zapytania); gdy token wygasa, odświeża go zapisanym mechanizmem i zapisuje nowy stan do `data/session.json`.
- FR-3c: Logowanie headless (i ewentualna interwencja z FR-5a) jest wykonywane tylko wtedy, gdy zapisany token nie istnieje lub odświeżanie zawiedzie.
- FR-3d: `data/session.json` zawiera wartości tokenów i cookies; nie są one wypisywane w logach ani w GUI (GUI pokazuje tylko status: aktywna / wygasa o / odświeżona o).
- FR-3e: Token wklejony w GUI jest sprawdzany jednym odczytem (`/profile`) przed zapisem. Odrzucony lub nieosiągalny token nie jest zapisywany.
- FR-4: Dane logowania i sesja nie trafiają do logów ani do repozytorium. Logi maskują wartości cookies.
- FR-5: Nieudane logowanie (np. zmiana formularza, CAPTCHA, 2FA) jest widoczne w GUI i w logach z czytelnym komunikatem; przebieg kończy się błędem, bez zawieszania procesu.
- FR-5a: Gdy logowanie wymaga interwencji użytkownika (CAPTCHA, kod 2FA, nieznany ekran, błąd formularza), przebieg **nie kończy się od razu** — wynik kierowany jest na stronę w GUI („Login assist”): zrzut ekranu aktualnego stanu przeglądarki, treść problemu oraz pola do reakcji (kod 2FA, wklejenie cookies sesji, przycisk „Ponów”).
- FR-5b: CAPTCHA nie jest rozwiązywana automatycznie. Strona w GUI pokazuje ją użytkownikowi; rozwiązanie wymaga jednej z dróg: zdalny podgląd przeglądarki (opcja do decyzji) albo ręczne wklejenie cookies z zalogowanej przeglądarki (fallback, zawsze dostępny).
- FR-5d (stwierdzone w eksploracji): Feedly pokazuje wyzwanie Cloudflare Turnstile („Verify you are human” / „You may be a robot”) już po kroku e-maila, przed polem hasła, przy headless Chromium. Dlatego FR-5a/5b muszą działać od pierwszego uruchomienia, nie tylko przy 2FA.
- FR-5c: Oczekiwanie na odpowiedź użytkownika ma limit czasu (np. 10 min). Po jego upływie przebieg kończy się błędem, a stan jest widoczny w GUI i w logach.

### 3.2 Kanały (feedy)

- FR-6: Lista kanałów pobierana z konta (subskrypcje) z nazwą, identyfikatorem, kategorią i liczbą nieprzeczytanych wpisów.
- FR-7: Kanały mogą być filtrowane po kategorii (folderze Feedly).
- FR-8: Lista kanałów odświeżana na żądanie w GUI oraz przy każdym przebiegu.

### 3.3 Częstotliwość sprawdzania kanałów

- FR-9: Każdy kanał ma własny **interwał sprawdzania** (np. 15 min, 1 h, 6 h, 24 h). Domyślnie 1 h.
- FR-10: Interwał ustawiany z poziomu GUI (na kanale lub zbiorczo dla zaznaczonych kanałów) i zapisywany w `config.yaml`.
- FR-11: Scheduler działa w krótkim cyklu (np. co minutę) i uruchamia tylko kanały, których interwał minął od ostatniego sprawdzenia.
- FR-12: Dla pomocy w doborze interwału GUI pokazuje średnią liczbę nowych wpisów na dobę dla kanału (wyliczaną z historii przebiegów).
- FR-13: Przycisk „Uruchom teraz” działa globalnie (wszystkie kanały) lub dla pojedynczego kanału.

### 3.4 Reguły czarnej listy

- FR-14: Każdy kanał może mieć zero lub więcej reguł.
- FR-15: Reguła składa się z: **pola**, **operatora**, **wartości**, **aktywności** (włączona/wyłączona), opcjonalnie komentarza.
  - Pola: tytuł (domyślnie), treść, autor, URL.
  - Operatory: `zawiera`, `zaczyna się od`, `dokładnie`, `regex`.
  - Dopasowanie **nie uwzględnia wielkości liter** (domyślnie, bez przełącznika w pierwszej wersji).
- FR-16: Wpis pasujący do **dowolnej** aktywnej reguły danego kanału jest oznaczany jako przeczytany.
- FR-17: Reguły globalne (dotyczą wszystkich kanałów) — tak.
- FR-18: Wyjątki (whitelist): wpis pasujący do wyjątku nie jest oznaczany, nawet jeśli pasuje do reguły. Wyjątki mogą być globalne lub per kanał.
- FR-19: Reguły dodawane, edytowane, wyłączane i usuwane z GUI.
- FR-20: Walidacja w GUI (poprawność regexa, pusta wartość, duplikaty).
- FR-21: Test reguły: podgląd, które aktualnie pobrane wpisy kanału zostałyby dopasowane.

### 3.4a Poziomy reguł i dziedziczenie

- FR-14a: Reguły i wyjątki można definiować na trzech poziomach: **globalnym**, **folderu** (kategorii Feedly) i **kanału** (feedu).
- FR-14b: Kanał należy do jednego folderu (założenie, patrz sekcja 12). Jeśli feed nie ma w ogóle zdefiniowanych reguł, używane są reguły jego folderu.
- FR-14c: Jeśli kanał ma własne reguły, **zastępują** reguły folderu (bez łączenia). Pusta lista reguł na kanale oznacza „dziedzicz z folderu”, a nie „brak reguł”. Żeby wyłączyć filtrowanie na kanale mimo reguł folderu, potrzebna jest jawna opcja „Nie dziedzicz” z pustą listą.
- FR-14d: Reguły globalne stosują się zawsze, niezależnie od folderu i kanału (dopisane do reguł wynikających z dziedziczenia).
- FR-14e: Ta sama zasada dziedziczenia obowiązuje dla wyjątków (whitelist) i dla `aging_days`: kanał → folder → globalnie.
- FR-14f: GUI pokazuje przy każdym kanale, skąd pochodzą aktywne reguły (globalne / folder „X” / własne), i pozwala edytować je na poziomie folderu lub kanału.
- FR-14g: Zmiana reguły folderu od razu wpływa na wszystkie kanały, które dziedziczą; kanały z własnymi regułami są nietknięte.

### 3.5 Aging

- FR-22: Wpisy starsze niż `N` dni (od daty publikacji) są oznaczane jako przeczytane. Domyślne `N = 7`.
- FR-23: `N` ustawiane globalnie w GUI oraz opcjonalnie nadpisywane per kanał.
- FR-24: Aging można wyłączyć (przełącznik lub N = 0).
- FR-25: Wpisy oznaczone jako „zapisane na później” / gwiazdka **nie są** oznaczane przez aging ani reguły (założenie, patrz sekcja 12).

### 3.6 Przebiegi i dry-run

- FR-26: Tryb **dry-run**: przebieg nie zmienia stanu w Feedly. Wynik to lista: kanał, tytuł wpisu, data publikacji, powód (reguła / aging / wyjątek). Dostępny z GUI oraz jako zmienna środowiskowa/ustawienie w `config.yaml`.
- FR-27: Dry-run jest **domyślnie włączony** przy pierwszym uruchomieniu; wyłączenie wymaga świadomej zmiany w GUI lub w configu. Przełącznik „Dry run: ON/OFF” na dashboardzie zmienia to samo ustawienie co strona Settings. Przełączenie na OFF wymaga potwierdzenia.
- FR-28: Nie więcej niż jeden przebieg naraz dla tego samego kanału (blokada).
- FR-29: Każdy przebieg zapisuje podsumowanie: czas, kanał, liczba przetworzonych wpisów, liczba oznaczonych wg reguły, wg agingu, liczba błędów.
- FR-30: Oznaczanie jest idempotentne — ponowny przebieg nie oznacza ponownie tych samych wpisów.
- FR-30a: Wymuszony przebieg („Force run") stosuje oznaczenia od razu, nawet gdy dry-run jest włączony. Wymaga potwierdzenia w GUI.
- FR-30b: Podgląd reguły („Preview matches") przed zapisem pokazuje, ile aktualnie nieprzeczytanych wpisów reguła oznaczyłaby, z przykładowymi tytułami. Zakres liczy się jak przy przebiegu (wpisy zapisane i wyjątki pomijane, reguła folderu nie dotyczy kanałów z własnymi regułami). Aging nie jest wliczany. Podgląd niczego nie zmienia.
- FR-30c: Powód oznaczenia zawiera regułę (pole, operator, wartość) i jej komentarz, jeśli został podany.

### 3.7 Historia i logi

- FR-31: Historia (przebiegi i oznaczone wpisy: tytuł, kanał, powód, czas) przechowywana w **SQLite** w `data/history.db`.
- FR-32: **Limit rozmiaru bazy: 100 MB.** Po przekroczeniu najstarsze rekordy są usuwane (przycinanie) i wykonywany jest `VACUUM`. Przycinanie sprawdzane po każdym przebiegu.
- FR-33: Historia dostępna w GUI (filtr po kanale i dacie).
- FR-33a: Liczba oznaczonych w przebiegu na dashboardzie i w historii jest linkiem do listy wpisów tego przebiegu. Każdy wpis pokazuje regułę, która go oznaczyła. Wpisy oznaczone przez aging są na liście szare, żeby odróżnić je od oznaczeń regułami.
- FR-33b: Statystyki żądań: liczba wykonanych żądań do Feedly z podziałem na typ (profil, kanały, liczniki, wpisy kanału, oznaczanie) i na wynik (2xx, 4xx, 5xx/429, błąd sieci, inne). Liczenie dotyczy każdej próby HTTP, także ponowień. Liczniki są zapisywane w SQLite per dzień UTC, więc przetrwają restart.
- FR-33c: Strona Stats pokazuje wykres słupkowy liczby żądań dziennie (SVG, bez zewnętrznych bibliotek) z podpowiedzią po najechaniu, tabelę tych samych wartości oraz tabelę typ × wynik. Okno: 7, 14, 30 lub 90 dni.
- FR-34: Logi techniczne na stdout (widoczne w `docker logs`).

## 4. Eksploracja API Feedly (krok 0)

Oficjalne API (developers.feedly.com) nie jest używane — decyzja: logowanie na konto użytkownika i API webowe.

- E-1: Przez headless Chromium (Playwright) zalogować się i przechwycić ruch sieciowy: endpointy logowania, nazwy cookies/tokenów, nagłówki autoryzacji, odświeżanie sesji.
- E-2: Zidentyfikować endpointy: lista subskrypcji, pobieranie wpisów (streams/contents, ids), liczba nieprzeczytanych, oznaczanie jako przeczytane (markers), paginacja, limity.
- E-3: Potwierdzić, czy oznaczanie działa dla wpisów pobranych ze streamu kanału oraz czy da się oznaczyć wpisy starsze niż timestamp.
- E-4: Udokumentować w `docs/api-notes.md`. Nie zapisywać prawdziwych danych logowania ani cookies w dokumentacji.
- E-5: Spisać ryzyka: API nieoficjalne może się zmienić; automatyzacja może być niezgodna z regulaminem Feedly; logowanie headless może wymagać obsługi CAPTCHA/2FA (do sprawdzenia na koncie).

Endpointy wymienione w literaturze (np. `cloud.feedly.com/v3/markers`) są **hipotezami** do weryfikacji w E-1…E-3.

## 5. Konfiguracja i dane (`data/`)

Katalog `data/` montowany jako wolumen do kontenera (`./data:/data`).

```
data/
  config.yaml       # ustawienia globalne + reguły + interwały kanałów + wyjątki (edytowalne ręcznie i z GUI)
  history.db        # SQLite: przebiegi i oznaczone wpisy (limit 100 MB)
  session.json      # sesja Feedly (cookies/token) — wykluczone z gita
  auth.yaml         # login i hasło do Feedly — wykluczone z gita
```

- D-1: `config.yaml` jest czytelny i edytowalny ręcznie. Zmiany z GUI aktualizują ten plik.
- D-2: Zapis pliku atomowy (plik tymczasowy + rename), żeby nie uszkodzić konfiguracji przy przerwaniu.
- D-3: Brak `data/` lub `config.yaml` — narzędzie tworzy domyślną konfigurację przy starcie (dry-run włączony).
- D-4: Pole `version` w `config.yaml` do przyszłych migracji.
- D-5: Ręczna edycja pliku jest wykrywana (przeładowanie przy następnym przebiegu lub przycisku w GUI); walidacja z czytelnym błędem przy niepoprawnym YAML.

Przykład struktury `config.yaml` (poglądowo):

```yaml
version: 1
timezone: Europe/Warsaw
dry_run: true
aging:
  enabled: true
  days: 7
global_rules:
  - field: title
    op: contains
    value: "sponsored"
    enabled: true
global_whitelist: []
feeds:
  - id: "feed/https://example.com/rss"
    name: "Example"
    folder: "Tech"
    interval_minutes: 60
    aging_days: null        # null = dziedziczy z folderu, potem z globalnego
    rules: []               # pusta lista = dziedzicz regułę folderu "Tech"
    whitelist: []
folders:
  - name: "Tech"
    aging_days: 3           # nadpisuje globalne 7 dni dla całego folderu
    rules:
      - field: title
        op: regex
        value: "^\\[AD\\]"
        enabled: true
    whitelist: []
```

## 6. GUI (port 8080)

Prosty interfejs serwerowy (bez ciężkiego frontendu). Język domyślny: **angielski**; polski jako opcjonalne tłumaczenie (i18n, bez wpływu na pierwszą wersję).

Ekrany:

1. **Dashboard** — status (połączenie/sesja Feedly, ostatni przebieg, najbliższe sprawdzenia), przełącznik „Dry run: ON/OFF” (z potwierdzeniem przy wyłączeniu), przyciski „Run now (follows dry-run setting)” i „Force run: apply marks now” (z potwierdzeniem), informacja, gdy przebieg trwa.
2. **Feeds** — tabela: nazwa, folder, nieprzeczytane, średnio nowych wpisów/dobę, interwał, źródło reguł (własne / folder / globalne), nadpisany aging. Zbiorowa zmiana interwału. Kliknięcie → szczegóły kanału.
3. **Feed detail** — reguły kanału (pole, operator, wartość, aktywna), tryb „dziedzicz z folderu” lub własne, wyjątki, interwał, aging. Przy dodawaniu reguły przycisk „Preview matches” pokazuje liczbę wpisów, które by oznaczyła (FR-30b).
4. **Folders** — lista folderów (kategorii Feedly): reguły folderu, wyjątki, `aging_days`. Edycja reguł folderu dziedziczona przez kanały bez własnych reguł.
5. **Global rules & whitelist** — reguły globalne i wyjątki globalne.
6. **Aging** — globalne N dni, przełącznik.
7. **Settings** — strefa czasowa, dry-run, limit historii (informacyjnie: 100 MB).
8. **History** — przebiegi i oznaczone wpisy (filtr po kanale i dacie), link z liczby oznaczonych do listy wpisów przebiegu z regułą, wykorzystanie miejsca w bazie.
9. **Login assist** — pokazuje się tylko, gdy logowanie wymaga interwencji (FR-5a): zrzut ekranu, opis problemu, pole na kod 2FA lub cookies, przycisk „Retry”. Dashboard pokazuje link do tej strony, gdy jest aktywny. Strona „Login” przyjmuje token i cookies, sprawdza je jednym odczytem (`/profile`) i zapisuje tylko, gdy działają (FR-3e).
10. **Stats** — liczba żądań do Feedly per typ i wynik, wykres dzienny i tabela (FR-33b, FR-33c).

Wymagania GUI:

- G-1: Serwer nasłuchuje na `0.0.0.0:8080` w kontenerze; w compose mapowanie `8080:8080`.
- G-2: Brak uwierzytelniania w GUI — narzędzie działa w zaufanej sieci; ochrona na innej warstwie (sieć, reverse proxy). Dokumentacja musi to jasno opisywać.
- G-3: Zasoby statyczne lokalnie (bez CDN), działa offline.
- G-4: Responsywny układ (podstawowy).
- G-5: Endpoint `/healthz` do healthchecka Dockera.
- G-6: Strona „Login assist” (FR-5a–5c) działa bez przeładowania całej aplikacji (odświeżanie stanu co kilka sekund lub SSE); zrzut ekranu serwowany z `data/` i nie trafia do logów.

## 7. Docker i docker-compose

- DK-1: `Dockerfile` wieloetapowy, obraz `linux/amd64`, uruchamiany jako nie-root użytkownik. Obraz zawiera Chromium (dla logowania headless), co czyni go większym — przyjęte.
- DK-2: `docker-compose.yml`:
  - usługa `feedly-filter`,
  - `image: ghcr.io/<owner>/feedly-filter:latest` (lub `build: .` do developmentu),
  - `ports: ["8080:8080"]`,
  - `volumes: ["./data:/data"]`,
  - `environment`: `TZ`,
  - `restart: unless-stopped`,
  - `healthcheck` na `/healthz`.
- DK-3: `.dockerignore` (bez `data/`, `.git/`, sekretów).
- DK-4: `.gitignore`: `data/*` z wyjątkiem `data/config.example.yaml`, w tym `auth.yaml`, `session.json`, `history.db`.
- DK-5: Przy pierwszym starcie `data/` jest tworzone automatycznie.

## 8. CI/CD — GitHub Actions

- CI-1: Workflow `ci.yml` na pull requesty: lint, testy jednostkowe (reguły, aging, interwały, parsowanie configu, przycinanie historii), build obrazu bez publikacji.
- CI-2: Workflow `docker-publish.yml`:
  - trigger: push na `main` oraz tagi `v*.*.*`,
  - build przez `docker/build-push-action`, logowanie do GHCR przez `GITHUB_TOKEN`,
  - tagi: `latest` (main), `sha-<short>`, `vX.Y.Z` / `X.Y` (tagi release),
  - platforma: `linux/amd64`,
  - cache buildów (GitHub Actions cache).
- CI-3: Brak sekretów w repo — publikacja wyłącznie przez `GITHUB_TOKEN` (uprawnienie `packages: write`).
- CI-4: Opcjonalnie: skan obrazu (Trivy) przed publikacją.

## 9. Wymagania niefunkcjonalne

- NF-1: Bezpieczeństwo: sekrety, cookies i tokeny nie w logach, nie w obrazie, nie w repo.
- NF-2: Odporność: błędy sieci/API nie kończą procesu; retry z backoffem (np. 3 próby); błąd jednego kanału nie blokuje innych.
- NF-3: Limity API: przetwarzanie w paczkach, pauza między żądaniami, reakcja na 429.
- NF-4: Strefa czasowa z konfiguracji (`TZ` / `timezone`), domyślnie `Europe/Warsaw`.
- NF-5: Język kodu, komentarzy i nazw technicznych: angielski. Język GUI: angielski.
- NF-6: Dokumentacja: `README.md` (uruchomienie compose, konfiguracja, przykłady reguł, zastrzeżenie o zaufanej sieci), `docs/api-notes.md`.
- NF-7: Licencja MIT (plik `LICENSE`).

## 10. Proponowany stack

- Backend: **Python 3.12**, **FastAPI** + Jinja2 / HTMX dla GUI bez budowania frontendu.
- Logowanie: **Playwright** (Chromium headless), potem sesja w `httpx`.
- Konfiguracja: YAML (`pyyaml`) + walidacja modelami (`pydantic`).
- Harmonogram: `APScheduler` lub pętla `asyncio` z cyklem co minutę.
- Historia: SQLite (`sqlite3` / SQLAlchemy) z przycinaniem do 100 MB.
- Testy: `pytest`, `respx` (mock HTTP), testy reguł i agingu na danych bez sieci.

## 11. Kolejne kroki

1. Potwierdzić założenia z sekcji 12.
2. Zweryfikować na żywym koncie endpointy zapisu i pól wpisów (sekcja 13.3, `docs/api-notes.md`). Dotyczy oznaczania jako przeczytane, zapisanych wpisów i autora.
3. Automatyczne logowanie headless (sekcja 4) — do zrobienia po rozwiązaniu problemu z Cloudflare. Do tego czasu działa wklejanie tokenu w GUI.
4. Uruchomić workflow `docker-publish.yml` na GitHubie i sprawdzić pierwszą publikację do GHCR; zbudować obraz lokalnie, gdy Docker będzie dostępny.
5. Uzupełnić `OWNER` w `docker-compose.yml` (nazwa użytkownika lub organizacji na GHCR).
6. Pierwszy release `v0.1.0`.

## 12. Założenia do potwierdzenia

Te punkty nie były wprost rozstrzygnięte w odpowiedziach, przyjąłem je domyślnie:

1. Wpisy oznaczone jako „zapisane na później” / gwiazdka są chronione przed agingiem i regułami (FR-25).
2. Dopasowanie reguł domyślnie na polu tytuł, bez względu na wielkość liter (FR-15).
3. Dry-run domyślnie włączony przy pierwszym uruchomieniu (FR-27).
4. Repozytorium publiczne (przy licencji MIT); w razie potrzeby zmienimy.
5. Domyślne interwały kanałów: 1 h; propozycje w GUI: 15 min, 1 h, 6 h, 24 h.
6. Kanał należy do jednego folderu. Feedly pozwala na kilka kategorii przy jednym feedzie — przyjmuję jeden (pierwszy), do potwierdzenia.
7. Reguły kanału **zastępują** reguły folderu (bez łączenia), reguły globalne zawsze dochodzą (FR-14c, FR-14d).
8. `aging_days` i wyjątki dziedziczą tak samo: kanał → folder → globalnie (FR-14e).

## 13. Stan implementacji (v0.4)

### 13.1 Zrobione i przetestowane (52 testy, `pytest`)

- Konfiguracja YAML z walidacją (pydantic), zapis atomowy, przeładowanie ręcznych zmian (sekcja 5).
- Reguły: contains / starts_with / equals / regex, bez wielkości liter, walidacja regexa, wyjątki, dziedziczenie global → folder → kanał (3.4a), aging po dacie publikacji, wpisy zapisane na później pomijane.
- Klient Feedly: nagłówki `Authorization` i `Cookie` z `data/session.json`, ponawianie przy 429/5xx, `AuthError` przy 401/403, błędy sieci/TLS jako `FeedlyError`, paginacja `continuation`.
- Silnik: przebiegi per kanał, dry-run (`would_mark`), wymuszony przebieg, blokada równoległych przebiegów, wybór kanałów wg interwału, brak sesji → błąd bez zapytania do sieci. Oznaczanie zbiorcze: jeden przebieg = jedna seria żądań `markAsRead` dla wszystkich kanałów; błąd serii zapisuje się jako błąd, nie jako oznaczone.
- Historia SQLite: przebiegi, oznaczone wpisy z regułą, czas ostatniego przebiegu, przycinanie do limitu (`VACUUM`).
- Scheduler: co minutę uruchamia kanały, których interwał minął.
- Statystyki żądań (FR-33b, FR-33c) i szare wiersze agingu w historii (FR-33a).
- GUI na porcie 8080: dashboard, kanały (zbiorowy interwał), reguły kanału/folderu/globalne z podglądem, ustawienia, historia z listą wpisów przebiegu, logowanie tokenem z weryfikacją.
- `truststore` — certyfikaty systemowe (firmowe proxy).
- Dockerfile (Python 3.12-slim, użytkownik uid 10001, healthcheck na `/healthz`), `docker-compose.yml` (`./data` → `/data`, port 8080), `.dockerignore`, `data/config.example.yaml`, `LICENSE` (MIT), `README.md`.
- Workflow `ci.yml` (testy `pytest` na push do `main` i na pull requestach) oraz `docker-publish.yml` (build, smoke test `/healthz`, publikacja do GHCR z tagami `latest`, `sha-…`, `X.Y.Z`, `X.Y`).

### 13.2 Jeszcze nie zrobione

- Automatyczne logowanie headless — w pakiecie nie ma modułu logowania; skrypt eksploracji jest w `scripts/explore_login.py`. Blokuje Cloudflare.
- Odświeżanie tokenu (FR-3a) — mechanizm nieznany, patrz `docs/api-notes.md`.
- Publikacja obrazu nie została jeszcze uruchomiona na GitHubie — workflow jest gotowy, ale nie testowany na prawdziwym repozytorium.
- Obraz Dockera nie był budowany lokalnie (brak Dockera na maszynie deweloperskiej); sprawdzono tylko składnię workflowów i zawartość wheel.

### 13.3 Założenia do zweryfikowania na żywym koncie

- Oznaczanie jako przeczytane: `POST /v3/markers` z `action: markAsRead` (kształt z publicznej dokumentacji, nie z web API). Jeden przebieg wysyła wszystkie identyfikatory z wszystkich kanałów w paczkach po 100, nie osobne żądanie na kanał.
- Pola wpisów: autor nie występuje w próbce strumienia; tag „zapisane na później" zakładany w `categories` (`…/tag/global.saved`).
- `velocity` w kanałach traktowany jako średnia liczba wpisów dziennie.
- Odczyty działają z samym tokenem (potwierdzone: `probe_api.py` wysyłał tylko `Authorization` i dostał 200). Nie sprawdzono, czy zapisy wymagają cookies.
