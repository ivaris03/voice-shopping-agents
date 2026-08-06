# Repository Guidelines

## Project Structure & Module Organization

This is a pnpm workspace for a voice-shopping POC. `apps/api/` contains the FastAPI and LangGraph backend; its importable code lives in `src/voice_shopping_api/` and tests in `tests/`. The Vue/Vite clients are `apps/user-web/`, `apps/merchant-web/`, and `apps/platform-web/`. Put reusable Vue components, shared API types, and styles in `packages/web-ui/src/`. Keep database schema snapshots, seed data, and ordered migrations in `sql/`; product and design documentation belongs in `docs/`, while deployment assets live in `deploy/`.

## Build, Test, and Development Commands

Run `pnpm install` and `uv sync --project apps/api` once to install dependencies. Start services in separate terminals with `pnpm dev:api`, `pnpm dev:user`, `pnpm dev:merchant`, and `pnpm dev:platform`. Use `pnpm typecheck` before submitting web changes and `pnpm build` to validate all workspace packages. For backend quality checks, run `pnpm lint:api`, `pnpm format:api`, and `pnpm test:api`. Run frontend unit tests with `pnpm --filter @voice-shopping/user-web test`.

## Coding Style & Naming Conventions

Follow `.editorconfig`: two spaces for TypeScript, Vue, JSON, and CSS; four spaces for Python; LF endings and final newlines. Use Ruff for Python formatting and linting (100-column limit) rather than manual import ordering. Preserve the existing Vue composition and TypeScript style. Name Python modules and functions in `snake_case`, classes in `PascalCase`, Vue components in `PascalCase.vue`, and tests as `test_*.py` or `*.test.ts`.

## Testing Guidelines

Add focused regression tests alongside the affected layer. API tests use pytest, with `contract/`, `service/`, and `e2e/` suites and matching markers. E2E tests require PostgreSQL/pgvector and Redis; set `VOICE_SHOPPING_TEST_DATABASE_URL` to a dedicated disposable database, then run `pnpm db:prepare-e2e` and `pnpm test:e2e`. No coverage threshold is configured, but changed behavior needs test coverage.

## Database, Configuration, and Security

Copy `.env.example` to `.env`; never commit credentials or local environment files. Create migrations as `YYYYMMDD_description.sql`, run them with `pnpm db:migrate`, and never edit an applied migration or the historical `00000000_initial_schema.sql`. Use a new migration for every correction.

## Commit & Pull Request Guidelines

Use concise Conventional Commit subjects, such as `fix: handle negated category switches` or `feat(user-web): add quick-start prompts`. Keep commits scoped. PRs should explain the user-facing or API impact, link relevant issues, list commands run, and include screenshots for visual client changes. Call out migrations, required environment variables, and deployment implications explicitly.
