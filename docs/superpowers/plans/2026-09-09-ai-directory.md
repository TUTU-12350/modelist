# AI 模型目录 Implementation Plan

> Execution: implement inline in this session; the user has authorized building the working website.

**Goal:** Deliver index.html with searchable AI entries, details, authenticated ratings, and publicly readable persistent reviews.

**Architecture:** A dependency-free single HTML client works in a clearly labeled local preview mode. A same-origin Python standard-library server serves the client and uses SQLite for accounts, sessions, and public reviews. No third-party account configuration is required for local shared testing.

**Tech Stack:** HTML, CSS, browser JavaScript, Python 3, SQLite.

## Global Constraints

- Chinese UI; responsive desktop and mobile layout.
- Do not fabricate community ratings or user reviews.
- Official source links and a checked date accompany catalog descriptions; distinguish products from model families.
- Public reviews must persist on the server and remain readable without login.
- User passwords are salted and hashed; users may modify only their own reviews.
- No external deployment or user-facing service publication without a configured destination.

## Files

- `index.html`: complete design, catalog data, search/filter/sort, detail and login dialogs, local preview adapter, server API adapter.
- `server.py`: explicit static route allowlist, authentication, SQLite schema, session cookies, review CRUD, validation, rate limits.
- `tests/test_server.py`: two-user persistence/public visibility/auth ownership and invalid input integration tests.
- `tests/browser.cjs`: browser flow and mobile layout verification if Playwright is available.
- `README.md`: start command, preview versus shared behavior, backup and deployment boundaries.
- `.gitignore`: exclude database and runtime files.

## Task 1: Shared API

- [x] Implement GET `/api/session` returning `{user, csrf}`; POST `/api/register`, `/api/login`, `/api/logout`.
- [x] Implement GET `/api/reviews` returning `{reviews}` and authenticated POST `/api/reviews` plus DELETE `/api/reviews/{model_id}`.
- [x] Enforce integer stars 1–5, model allowlist, text length 10–2000, one review per user/model, CSRF and Origin checks, request limits.
- [x] Test register → post → anonymous read → edit → unauthorized delete → owner delete; verify persistence after restart.

## Task 2: Single-file client

- [x] Build navigation, purple editorial hero, functional search, category rail, 3-column model cards, community sidebar and footer.
- [x] Embed curated model data with official references. Search names/aliases/vendors/tags; support saved entries and reviewed-only filter.
- [x] Build keyboard-accessible native dialogs for detail, auth, guide; rating selector and text form support edit/delete.
- [x] Select local preview only when opened with file protocol or API explicitly unavailable; show clear state on connection failures and never silently discard server writes.

## Task 3: Verification and delivery

- [x] Run `python -m unittest discover -s tests -v`; expected all tests pass.
- [x] Use browser to verify search, login, review posting and anonymous visibility, saved entries, empty state, mobile overflow and dialog interactions.
- [x] Inspect a desktop screenshot, correct layout issues, document `python server.py`, and open the completed website preview.

