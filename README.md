# telegaNEW — Telegram для Android с усиленной маскировкой MTProxy

<img width="1916" height="821" alt="image" src="https://github.com/user-attachments/assets/0850c5cd-6d7f-4304-9347-2cc54d5ba416" />

> Экспериментальный форк официального Telegram для Android. Цель форка —
> сделать подключение к MTProxy FakeTLS (`ee`-секрет) менее похожим на
> стандартный Telegram MTProxy-трафик и ближе к обычному браузерному HTTPS.

Это не новый мессенджер и не отдельный протокол. База остаётся официальным
Telegram Android, а основные изменения сосредоточены вокруг MTProxy/FakeTLS,
отдельного WSS-транспорта и клиентских privacy/UX-настроек форка.

## Прокси по умолчанию

В **telegaNEW** уже встроены рабочие MTProto прокси, которые активны сразу после установки. Вам не нужно искать списки прокси для первого входа.

### Список встроенных прокси:
1.  **144.31.15.132:443** (основной, используется по умолчанию)
2.  **nsk.cdn.catpaws.ru:443** (FakeTLS `ee`-секрет)
3.  **nsk.cdn.catpaws.ru:443** (Legacy `dd`-секрет)

### Автопереключение (Rotation)
В приложении по умолчанию включено **автопереключение прокси**. Если текущее соединение нестабильно, клиент автоматически переключится на следующий рабочий адрес.
- Интервал проверки и переключения по умолчанию: **5 секунд**.
- Вы можете добавить свои прокси в список, и они также будут участвовать в автоматической ротации.

## Простыми словами

- **MTProxy** — прокси для Telegram.
- **FakeTLS** — режим MTProxy, где начало соединения выглядит как TLS/HTTPS.
- **WSS** — WebSocket-транспорт поверх настоящего TLS. В этом форке он живёт
  отдельно от MTProxy/FakeTLS и обычного SOCKS5.
- **DPI / ТСПУ** — оборудование провайдера, которое классифицирует и режет
  трафик по признакам на проводе.
- **JA4** — отпечаток TLS ClientHello. По нему можно отличить настоящий браузер
  от синтетического FakeTLS, если ClientHello сделан плохо.

## Технические подробности реализации

### Устойчивость endpoint'а

Поверх планировщика FakeTLS добавлен общий слой устойчивости endpoint'а. Он
работает для всех MTProxy-секретов, включая обычные `dd`/legacy, и не меняет
формат MTProto-пакетов.

У слоя два разных ключа (phase-aware endpoint keys), потому что разные фазы видят разный объём данных:
- сетевой ключ `host:port` используется для DNS, `tcp_not_connected` и
  ограничения одновременных TCP connect-попыток.
- FakeTLS-рецепт использует ключ `host:port:тип секрета:SNI`.

Что делает слой:
- запоминает последний успешно разрешённый IPv4 для доменного прокси.
- коротко склеивает одновременные холодные DNS-разрешения одного `host:port`.
- после фаз `host_resolve_failed`, `tcp_not_connected`,
  `client_hello_sent_no_server_hello` и `mtproxy_packet_sent_no_response`
  ставит короткую паузу (failure backoff).
- для FakeTLS после повторяющихся post-ClientHello сбоев мягко меняет следующий
  рецепт запуска.
- сбрасывает штраф после реального ответа прокси: `server_hello_hmac_ok` или `first_tls_app_recv`.

Java-планировщик проверок (`ProxyCheckScheduler`) хранит свой endpoint-state для фоновых и ручных
proxy-check. Активные proxy-check запросы склеиваются (exact-key proxy-check coalescing) по полному
ключу `host:port:username:password:secret`, чтобы результат проверки одного
секрета не применился к другому секрету.

Общее состояние Telegram `Connected` (generic connected-state observations) не затирает ни живые стадии
вроде `client_hello_sent`, ни terminal-ошибки вроде `host_resolve_failed` в GUI.
Явный новый старт подключения (explicit reconnect attempts) публикует `connect_start`,
но не стирает свежий usable success: в течение hold-окна после `first_tls_app_recv`
локальный `connect_start` удерживается как live telemetry.

### Архитектура проверки прокси

Проверка прокси разделена на три слоя (Java/native proxy-check lifecycle):
- `ProxyCheckScheduler` в Java принимает ручные проверки, фоновые проверки и проверки ротации.
- Native-слой `ConnectionsManager` завершает проверку через единый `finishProxyCheck`.
- `Tools/analyze_mtproxy_markers.py` сводит Java scheduler, native proxy-check,
  rotation и FakeTLS-маркеры в один отчёт (analyzer verdicts). Важный verdict:
  `connected_without_socket_connected_marker` означает, что лог дошёл до
  `on_connected`, но в этом срезе нет маркера `socket_connected`.

### Пользовательские функции форка

- экран `Приватность` с переключателями: сохранять удалённые сообщения, сохранять view-once материалы, хранить локальную историю редактирования, разрешать скриншоты и т.д.
- настройка `Скрыть истории на главном экране`.
- настройка `Всегда держать сеть в фоне`.
- раздел `Плагины` в настройках: установка Python `.plugin`-файлов.
- Android 12+ splash использует логотип **telegaNEW**.

## Сборка и диагностика

1. Получи `api_id` и `api_hash` на [my.telegram.org](https://my.telegram.org).
2. Собери через Android Studio или GitHub Actions.

GitHub Actions создаёт диагностические файлы (runtime contract artifacts):
- `mtproxy_markers.txt` — содержит live MTProxy markers (`transport_state`, `endpoint_handshake_ok`, `endpoint_data_path_success`).
- `mtproxy_analysis.txt` — показывает стадии: TCP/connect, ClientHello, ServerHello/HMAC.
- `mtproxy_runtime_contract.txt` — строгая проверка лога на соответствие контракту (data path success не может идти только с hmac_ok).

## Основа и благодарности

- [DrKLO/Telegram](https://github.com/DrKLO/Telegram) — официальный Telegram Android.
- [tsrman/tg](https://github.com/tsrman/tg) — база изменений FakeTLS и JA4.
- [telemt/tdlib-obf](https://github.com/telemt/tdlib-obf) — идеи по профилям маскировки.
