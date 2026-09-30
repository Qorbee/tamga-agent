# Third-party notices

Tamga's own code is under the proprietary licence in [LICENSE](LICENSE). This repository also
contains, or its builds include, the components below. Each is under its own licence, which
governs it, and nothing in LICENSE limits the rights those licences give.

## Copied into this repository

These files may have been changed for Tamga; the changes are Tamga's.

| Component | Where | Licence | Copyright |
|---|---|---|---|
| shadcn/ui components (`radix-maia` style) | `frontend/src/components/ui/` | MIT | Copyright (c) 2023 shadcn |
| assistant-ui components | `frontend/src/components/assistant-ui/` | MIT | Copyright (c) 2025 AgentbaseAI Inc. |
| Procrastinate database schema 3.9.0 | `backend/app/migrations/sql/procrastinate-3.9.0-schema.sql` | MIT | Copyright (c) 2019-2021, PeopleDoc |

## Git submodules

Each carries its licence in its own directory.

| Component | Where | Licence | Copyright |
|---|---|---|---|
| OpenZeppelin Contracts | `contracts/lib/openzeppelin-contracts` | MIT | Copyright (c) 2016-2026 Zeppelin Group Ltd |
| Forge Standard Library | `contracts/lib/forge-std` | MIT or Apache-2.0, used under MIT | Copyright Contributors to Forge Standard Library |

## Fonts served by the web app

`next/font` fetches these at build time, and the web app serves them. All are under the
[SIL Open Font License 1.1](https://openfontlicense.org/open-font-license-official-text/).

| Font | Copyright |
|---|---|
| DM Sans | Copyright The DM Sans Project Authors |
| JetBrains Mono | Copyright The JetBrains Mono Project Authors |
| Lora | Copyright The Lora Project Authors |

## Dependencies

The packages that uv installs (`backend/uv.lock`) and pnpm installs (`frontend/pnpm-lock.yaml`) are
not part of this repository. Each ships with its own licence. To list them:
- `pnpm licenses list`, from `frontend/`;
- `uvx --exclude-newer "7 days" pip-licenses --python backend/.venv/bin/python`.

## MIT License

The components above marked MIT are distributed under this text, with their copyright lines above:

```text
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
