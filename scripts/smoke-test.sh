#!/usr/bin/env bash
# Smoke test for the Step 1 platform: Ollama (chat + embeddings), Postgres/pgvector, Valkey.
set -uo pipefail
cd "$(dirname "$0")/.."

env_value() { # name default
  local v=""
  [ -f .env ] && v=$(grep -E "^\s*$1=" .env | head -n1 | cut -d= -f2- | tr -d '\r')
  echo "${v:-$2}"
}

OLLAMA="http://127.0.0.1:$(env_value OLLAMA_PORT 11434)"
CHAT=$(env_value OLLAMA_CHAT_MODEL qwen3:4b)
EMBED=$(env_value OLLAMA_EMBED_MODEL bge-m3)
PG_USER=$(env_value POSTGRES_USER dba)
PG_DB=$(env_value POSTGRES_DB dba)
failed=0

ok()   { printf '\033[32m[ OK ]\033[0m %s - %s\n' "$1" "$2"; }
fail() { printf '\033[31m[FAIL]\033[0m %s - %s\n' "$1" "$2"; failed=$((failed+1)); }

tags=$(curl -fsS "$OLLAMA/api/tags" 2>&1) || tags=""
if echo "$tags" | grep -q "\"$CHAT" && echo "$tags" | grep -q "\"$EMBED"; then
  ok "Ollama models present" "$CHAT, $EMBED"
else
  fail "Ollama models present" "missing - run: docker compose logs ollama-init"
fi

r=$(curl -fsS "$OLLAMA/v1/chat/completions" -H 'Content-Type: application/json' --max-time 300 \
  -d "{\"model\":\"$CHAT\",\"max_tokens\":400,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: pong\"}]}" 2>&1)
if echo "$r" | grep -qi 'pong'; then ok "Chat completion (OpenAI-compatible /v1)" "got reply"; else fail "Chat completion" "unexpected reply"; fi

r=$(curl -fsS "$OLLAMA/v1/chat/completions" -H 'Content-Type: application/json' --max-time 300 -d "{
  \"model\":\"$CHAT\",\"max_tokens\":600,
  \"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Amsterdam? Use the tool.\"}],
  \"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Get current weather for a city\",
    \"parameters\":{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}},\"required\":[\"city\"]}}}]}" 2>&1)
if echo "$r" | grep -q 'tool_calls'; then ok "Tool calling" "model called get_weather"; else fail "Tool calling" "no tool call (try OLLAMA_CHAT_MODEL=qwen3:8b)"; fi

r=$(curl -fsS "$OLLAMA/v1/embeddings" -H 'Content-Type: application/json' --max-time 120 \
  -d "{\"model\":\"$EMBED\",\"input\":\"no internet, red LOS light\"}" 2>&1)
if echo "$r" | grep -q '"embedding"'; then ok "Embeddings" "vector returned"; else fail "Embeddings" "no embedding"; fi

ext=$(docker compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -tAc "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm','unaccent') ORDER BY 1" 2>&1)
if echo "$ext" | grep -q vector; then ok "Postgres + pgvector" "$(echo "$ext" | tr '\n' ' ')"; else fail "Postgres + pgvector" "pgvector extension not found"; fi

if docker compose exec -T valkey valkey-cli ping 2>&1 | grep -q PONG; then ok "Valkey" "PONG"; else fail "Valkey" "no PONG"; fi

if [ "$failed" -gt 0 ]; then printf '\n%s check(s) failed.\n' "$failed"; exit 1; fi
printf '\nAll checks passed - platform is ready.\n'
