#!/usr/bin/env bash
# The background worker on this machine with nothing paid and nothing sent: the one way to run it
# locally. The server is where the real work happens; the local database is a stale copy, and a
# plain `python -m app.worker` here pays the model provider a second time for it, anchors the local
# log on Arc, opens vault accounts from the treasury and reads the pilot's mail and books.
#
#   scripts/offline-worker.sh
#
# backend/.env stays as it is: real environment variables win over it, and an empty one does not,
# so each connection below is pointed at something that cannot answer rather than unset.
#   - every model budget is 0, and the model key is a dummy that the provider refuses for free;
#   - Circle's key and CLI are dummies: no anchor, no release, no vault account;
#   - no mailbox, and the books login goes to a closed local port;
#   - traces go nowhere, and the chain is read through the public node, not the team's key.
# A job that needs one of these is checked on the server after a deploy, not here.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../backend"

dead="http://127.0.0.1:9"
exec env \
  MAIL_MODEL_READS_PER_DAY=0 \
  VISION_PAGES_PER_DAY=0 \
  TYPED_FILES_PER_DAY=0 \
  ADVICE_PER_DAY=0 \
  TRANSLATED_CHARS_PER_DAY=0 \
  CHAT_REQUESTS_PER_DAY=0 \
  TAMGA_REQUESTS_PER_DAY=0 \
  OPENAI_API_KEY=offline \
  CIRCLE_API_KEY=TEST_API_KEY:offline \
  CIRCLE_ENTITY_SECRET=offline \
  CIRCLE_CLI=false \
  MAILBOXES='[]' \
  BOOKS_LOGIN_URL="$dead" \
  LANGFUSE_BASE_URL="$dead" \
  ARC_RPC_URL=https://rpc.testnet.arc.network \
  uv run python -m app.worker "$@"
