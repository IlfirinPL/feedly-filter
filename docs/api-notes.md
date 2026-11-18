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

## Do sprawdzenia (kolejne kroki)

- `GET /v3/streams/contents?streamId=...` i `GET /v3/streams/ids?streamId=...` — pobieranie wpisów kanału i daty publikacji (FR-22).
- `POST /v3/markers` z `action: markAsRead` — oznaczanie jako przeczytane. Dopiero po zatwierdzeniu, bo zmienia stan konta. Dry-run tego nie wykonuje.
- Czy wpis zapisany na później (`saved`/`starred`) jest widoczny w strumieniu i jak go rozpoznać (FR-25).
- Format `streamId` dla kategorii (`user/<id>/category/<id>`) i dla kanału (`feed/<url>`).
- Ważność tokenu i mechanizm odświeżania (FR-3a).

## Ryzyko

API webowe jest nieoficjalne i może się zmienić bez ostrzeżenia. Token daje dostęp do konta, więc nie wolno go zapisywać w repozytorium ani w logach.
