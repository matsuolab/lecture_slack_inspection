# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Slack post compliance inspection bot for the UTokyo Matsuo Lab DX Project. Five AWS Lambda functions (Python 3.12, Docker images) deployed via AWS CDK. Slack events trigger `app_inspect`, which passes the post plus the full rule set to an LLM judge; `app_alert` handles admin approval/dismiss buttons; `app_remind` and `app_batch` run on schedules.

## Commands

**Tests (from `lecture_slack_inspection/`):**
```bash
pip install -r lambda/requirements.txt
pytest tests/                                      # all tests
pytest tests/unit/test_app_inspect.py              # single test file
pytest tests/unit/test_app_inspect.py::test_name   # single test
```

**Infrastructure (from `lecture_slack_inspection/infra/`):**
```bash
pip install -r requirements.txt
cdk synth
cdk deploy
cdk diff
```

**Prompt evaluation (from `lecture_slack_inspection/evals/`):**
```bash
npm install
npx promptfoo eval
```

## Architecture

### Lambda functions

| Function | Trigger | Entry point |
|---|---|---|
| `app_inspect` | `POST /slack/events` | `app_inspect.handler.lambda_handler` |
| `app_alert` | `POST /slack/interactions` | `app_alert.handler.lambda_handler` |
| `app_remind` | EventBridge schedule | `app_remind.handler.lambda_handler` |
| `app_batch` | EventBridge schedule | `app_batch.handler.lambda_handler` |
| `app_oauth` | `GET /slack/oauth/*` | `app_oauth.handler.lambda_handler` |

All functions share a single Dockerfile (`lambda/Dockerfile`) but each image excludes the other apps' directories at CDK build time (see `infra/stacks/infra_stack.py`).

### Violation detection (`app_inspect`)

`handler.py` → `services/inspection_flow.py` → `services/moderation.py` → `services/violation_detector.py`

Detection is a **single LLM judge over all articles**. `ViolationDetector.detect()` concatenates every article in `common/data/articles.json` plus any per-workspace `extra_articles` and sends them with the post to the model. Prompt is `services/data/prompts/judge_violation.txt`, `response_format` is `judge_violation.response_format.json`; the model returns a `violation_score` 0–100 and the violation threshold is `>= 51`.

The old **RAG** (top-3 article retrieval via embeddings) and the **NG-word fast-path** (`services/data/ng_patterns.json`) were both **removed** (commit `0f1147d`) — the model context is large enough to pass the full ~24-article rule set every time. `ng_patterns.json` is dead and `text-embedding-*` is no longer used; do not reintroduce them without checking the `violation_detector.py` module docstring.

**Per-workspace local rules:** `handler._build_moderation_executor` calls `notion.query_workspace_page_id(team_id)` → `notion.query_workspace_local_rules(page_id)` to fetch workspace-specific articles from the Notion 条文マスタ DB (filtered by the `workspace` relation column + `有効=True`), and passes them as `extra_articles`. A fetch failure is logged and the base rule set is still judged.

`USE_MOCK_OPENAI=true` short-circuits all of the above with a mock that flags any text containing `違反` (handler.py, not a separate detector module).

For edited messages, `services/violation_transition.py` decides the state machine action (`create_new_violation`, `reply_still_violation`, `close_by_edit`, `no_action`) based on the existing Notion record's `対応ステータス`.

Posts by users whose Slack `real_name` contains `松尾研` are skipped (staff are not monitored).

### Secrets / config

Two config sources, distinguished in `services/config.py`:
- **Secrets** (signing secret, OpenAI key, Notion key) come from AWS SSM Parameter Store via `common/secret_manager.py`, read by name (env var `FOO_PARAM_NAME` holds the SSM parameter path); values are cached in-process.
- **Plain env vars** (set by CDK in `infra_stack.py`): `OPENAI_MODEL`, `NOTION_DB_ID`, `NOTION_ARTICLES_DB_ID` (条文マスタ), `NOTION_WS_LIST_DB_ID` (workspace list), `NOTION_TEMPLATE_DB_ID`, `MIN_SEVERITY_TO_ALERT`, `USE_MOCK_OPENAI`.

Per-workspace tokens are read at request time from `SLACK_INSTALLATION_PARAM_PREFIX/<team_id>/bot_token` and `.../<team_id>/alert_channel_id` — `load_config(team_id)` requires both or raises. The OpenAI model is **not** hardcoded; it comes from `OPENAI_MODEL`.

### Notion integration

`common/notion_client.py` owns all Notion API calls and talks to three DBs: the **violations** DB (`NOTION_DB_ID`, where records are written), the **条文マスタ / articles** DB (`NOTION_ARTICLES_DB_ID`, source of per-workspace local rules and article display names), and the **workspace list** DB (`NOTION_WS_LIST_DB_ID`, maps `team_id` → workspace page). Construct it with all three IDs (see `handler.py`) — a `NotionClient` built without `articles_db_id`/`ws_list_db_id` silently disables the workspace filter (the bug fixed in `890360f`).

Violation-record key properties: `Message_TS` (dedup key), `対応ステータス` (status lifecycle), `通知チャンネルID`/`通知メッセージTS` (admin Slack thread back-reference for edit-flow replies).

Status lifecycle: `未対応` → `警告済み` → `期限超過` → `再警告済み` → `対応終了` (or `Dismissed`).

### Test setup

`tests/conftest.py` injects all required env vars via `autouse` fixture and provides `mock_external_services` (mocks SignatureVerifier, WebClient, OpenAI, NotionClient, secret_manager). The `lambda/` directory is added to `sys.path` so handlers import directly. Contract fixtures live in `contracts/fixtures/`.

## Key files

- `lambda/app_inspect/services/violation_detector.py` — full-articles LLM judge (read its module docstring before changing detection)
- `lambda/app_inspect/services/violation_transition.py` — edit-event state machine
- `lambda/app_inspect/services/inspection_flow.py` — orchestrates new vs edited message flows
- `lambda/app_alert/services/actions.py` — approve/dismiss/rewarn/close button handlers
- `lambda/common/notion_client.py` — all Notion DB operations
- `lambda/common/secret_manager.py` — SSM Parameter Store access with in-process cache
- `lambda/common/observability.py` — structured logging and CloudWatch metrics
- `lambda/app_inspect/services/data/prompts/judge_violation.txt` — LLM judge prompt (edit to tune detection)
- `contracts/specs/notion_db_schema.md` — authoritative Notion DB schema reference
- `infra/stacks/infra_stack.py` — all CDK resources (API GW, Lambdas, IAM, EventBridge)
