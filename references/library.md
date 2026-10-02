# Library seat availability (read-only)

The library reservation system (`http://libyy.qfnu.edu.cn`) exposes two endpoints that need **no login**:
the campus/floor/area list and the per-seat status list. The CLI calls only those two (plus the
time-segment lookup that the seat list requires). It never books, cancels, checks in, or checks out a
seat, and it never sends a Cookie, a JWXT session, or a token: the library endpoints are queried
anonymously.

## CLI

```bash
easy-qfnu library areas
easy-qfnu library areas --date 2026-10-03
easy-qfnu library seats --area 12
easy-qfnu library seats --area 12 --date 2026-10-03 --free-only
easy-qfnu library seats --area 12 --segment 1864001
easy-qfnu library seats --area 12 --start-time 08:00 --end-time 22:00
```

| CLI option | Meaning |
|---|---|
| `--date` | `YYYY-MM-DD`. For `areas` it defaults to the system's own bookable date; for `seats` it defaults to the local today. |
| `--area` | Area id from `areas` (`area[].id`), required for `seats`. This is the system's `roomID`. |
| `--segment` | Time-segment id. Optional; when omitted the CLI reads it from `/api/Seat/date`. |
| `--start-time` / `--end-time` | Requested window, default `08:00` / `22:00`. |
| `--free-only` | Return only seats whose `status` is `1` (空闲). |

## Upstream contract

All three requests are `POST` with `Content-Type: application/json` and no credential header.

| Purpose | Path | Body |
|---|---|---|
| Campus / floor / area | `/reserve/index/quickSelect` | `{"id": "1", "date": "", "categoryIds": ["1"], "members": 0, "authorization": ""}` |
| Time segments | `/api/Seat/date` | `{"build_id": <area id>}` |
| Seat list | `/api/Seat/seat` | `{"area": <area id>, "segment": <segment id>, "day": "YYYY-MM-DD", "startTime": "08:00", "endTime": "22:00"}` |

- `quickSelect` reports success as `code` `"0"` (string, or the number `0`), which is the opposite of
  every other endpoint in this system. On success `data` holds `date` (bookable dates), `premises`
  (campus), `storey` (floor, linked by `parentId` to `premises[].id`), and `area` (linked by `parentId`
  to `storey[].id`).
- `/api/Seat/date` reports `code` `1` and `data: [{"day": "...", "times": [{"id": "..."}]}]`. The CLI
  matches `day` to the requested date and uses the first non-empty `times[].id` as the segment. A day
  whose `times` is empty (`[[]]`) has no bookable window.
- `/api/Seat/seat` reports `code` `1` and `data` as the seat array. `segment` is mandatory: without a
  usable one the system answers `code 0` with `请选择时段不能为空`.
- `total_num` / `free_num` may arrive as strings or numbers; the CLI normalizes them to integers.

Seat status codes:

| `status` | `status_name` | Meaning |
|---|---|---|
| `"1"` | 空闲 | Free |
| `"2"` | 已预约 | Reserved |
| `"3"` | 锁定 | Locked |
| `"6"` | 使用中 | In use |

## Response

`library areas` returns `ok`, `source: "library"`, `operation: "areas"`, `date` (the requested value),
`dates` (bookable dates), `campuses`, `floors`, `areas`, `area_count`, `total_num`, `free_num`
(summed over `areas`), and `url`.

`library seats` returns `ok`, `source: "library"`, `operation: "seats"`, `area`, `area_name`, `day`,
`segment`, `start_time`, `end_time`, `free_only`, `seat_count`, `total_num`, `free_num`,
`status_counts`, `items`, and `url`. Each item is compacted to `id`, `no`, `name`, `status`,
`status_name`, `area`, and `area_name`; the raw upstream object also carries map coordinates, which
the CLI drops.

Failures return `ok: false` with the upstream `msg` as `error` plus `hint`, and include `upstream`
so the raw body can be inspected without `--debug`.

## Safety

- Query only. Never call `/api/Seat/confirm`, `/api/Space/cancel`, `/api/Seat/touch_qr_books`, or
  `/api/Space/checkout`, and never construct a reservation payload (the date-keyed AES payload in the
  API notes) from this skill.
- Never attach a JWXT session, Cookie, account, or token to these requests, and do not log in to make
  a seat query work. If the endpoints start demanding a token, report the upstream message.
- The result is a live snapshot, not a reservation: a seat shown as 空闲 can be taken at any moment.
