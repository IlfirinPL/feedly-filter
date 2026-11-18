# Feedly web API — notatki z eksploracji

Stan: potwierdzone odczytem (GET) z tokenem zapisanym w `data/session.json`. Nie wykonano żadnego zapisu (markers POST, oznaczanie jako przeczytane).

## Uwierzytelnianie

- Nagłówek: `Authorization: Bearer <token>`.
- Token to JWE (`alg: dir`, `enc: A256GCM`), więc jego treść i czas wygaśnięcia nie są widoczne bez klucza serwera.
- Nie znaleziono w tej rozmowie mechanizmu odświeżania (refresh token). Do sprawdzenia: ważność tokenu w czasie i endpoint odświeżania.
- Endpoint wymiany tokenu: `POST https://cloud.feedly.com/v3/auth/token` (i `feedly.com/v3/auth/token`). GET daje 405. Bez ciała zwraca `400 missing grant_type`. Z `grant_type=refresh_token` zwraca `400 missing client_id`. Brakuje jeszcze `client_id` i prawdopodobnie `refresh_token`, których nie mamy. Wartości można znaleźć w ruchu sieciowym zalogowanej przeglądarki (DevTools → Network → żądanie `auth/token`), bez kopiowania tokenów do czatu.
- Logowanie headless przez `feedly.com/i/login` jest blokowane przez Cloudflare Turnstile („Verify you are human" / „You may be a robot") już po kroku e-maila.

## Endpointy sprawdzone (GET, status 200)

| Endpoint | Struktura odpowiedzi |
|---|---|
| `https://cloud.feedly.com/v3/profile` | obiekt: `email`, `client`, `created`, `entitlements` (m.in. `maxFeeds`, `maxMonthlyApiCalls`, `maxMuteFilters`), flagi połączonych kont |
| `https://cloud.feedly.com/v3/subscriptions` | lista: `id` (np. `feed/<url>`), `title`, `website`, `categories` (lista z `id`), `updated`, `topics`, `velocity`, `subscribers`, `iconUrl`, `visualUrl`, `partial` |
| `https://cloud.feedly.com/v3/categories` | lista: `id`, `label`, `created` |
| `https://cloud.feedly.com/v3/markers/counts` | obiekt: `unreadcounts` (lista: `id`, `count`, `updated`), `updated` |

Na koncie w chwili testu: 2 subskrypcje, 2 kategorie, 6 wpisów w liczbach nieprzeczytanych.

## Strumień wpisów (sprawdzone, GET 200)

`GET https://cloud.feedly.com/v3/streams/contents?streamId=<id>&count=<n>&unreadOnly=true[&continuation=<token>]`

- Odpowiedź: obiekt `id`, `title`, `updated`, `direction`, `alternate`, `continuation` (brak, gdy to koniec), `items` (lista).
- Każdy wpis w `items` ma m.in.: `id`, `title`, `published` (liczba całkowita, milisekundy), `unread` (bool), `alternate` (lista z `href`, czyli adres artykułu), `summary` (obiekt z `content`), `categories` (lista z `id`), `origin`, `originId`, `crawled`, `visual`, `engagement`.
- W próbce nie ma pola `author` ani `tags`. Aplikacja czyta `author` jako pusty tekst, dopóki nie potwierdzimy innego źródła.
- Identyfikatory: kanał `feed/<url>`, kategoria `user/<uuid>/category/<id>`. Kanał zwracany przez `/subscriptions` ma `categories` z `label`, którego aplikacja używa jako nazwy folderu.
- Zapisane na później: aplikacja zakłada tag `…/tag/global.saved` w `categories`. To nie zostało sprawdzone na zapisanym wpisie.

## Do sprawdzenia (kolejne kroki)

- `POST /v3/markers` z `action: markAsRead` — oznaczanie jako przeczytane. Zmienia stan konta, więc dopiero po zatwierdzeniu. Dry-run tego nie wykonuje. Jeden przebieg wysyła wszystkie identyfikatory z wszystkich kanałów w paczkach po 100 (`entryIds`). Aplikacja wysyła ten kształt, ale nie został zweryfikowany na web API.
- Czy wpis zapisany na później ma tag `global.saved` w `categories` i czy da się go znaleźć w strumieniu.
- Znaczenie `velocity` w `/subscriptions` (zakładane: średnia liczba wpisów dziennie).
- Ważność tokenu i mechanizm odświeżania (FR-3a). Bez `refresh_token` i `client_id` nie da się tego sprawdzić.

## Ryzyko

API webowe jest nieoficjalne i może się zmienić bez ostrzeżenia. Token daje dostęp do konta, więc nie wolno go zapisywać w repozytorium ani w logach.
