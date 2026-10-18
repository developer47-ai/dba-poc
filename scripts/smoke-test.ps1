#!/usr/bin/env pwsh
# Smoke test for the Step 1 platform: Ollama (chat + embeddings), Postgres/pgvector, Valkey.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Env-Value($name, $default) {
    if (Test-Path .env) {
        $line = Get-Content .env | Where-Object { $_ -match "^\s*$name=" } | Select-Object -First 1
        if ($line) { return ($line -split "=", 2)[1].Trim() }
    }
    return $default
}

$ollama = "http://127.0.0.1:" + (Env-Value "OLLAMA_PORT" "11434")
$chat   = Env-Value "OLLAMA_CHAT_MODEL" "qwen3:4b-instruct-2507-q4_K_M"
$embed  = Env-Value "OLLAMA_EMBED_MODEL" "bge-m3"
$pgUser = Env-Value "POSTGRES_USER" "dba"
$pgDb   = Env-Value "POSTGRES_DB" "dba"
$failed = 0

function Check($name, [scriptblock]$test) {
    try { $detail = & $test; Write-Host ("[ OK ] {0} - {1}" -f $name, $detail) -ForegroundColor Green }
    catch { Write-Host ("[FAIL] {0} - {1}" -f $name, $_.Exception.Message) -ForegroundColor Red; $script:failed++ }
}

Check "Ollama models present" {
    $names = (Invoke-RestMethod "$ollama/api/tags").models.name
    foreach ($m in @($chat, $embed)) { if (-not ($names | Where-Object { $_ -like "$m*" })) { throw "model '$m' missing - run: docker compose logs ollama-init" } }
    $names -join ", "
}
Check "Chat completion (OpenAI-compatible /v1)" {
    $body = @{ model = $chat; messages = @(@{ role = "user"; content = "Reply with the single word: pong" }); max_tokens = 400 } | ConvertTo-Json -Depth 5
    $r = Invoke-RestMethod "$ollama/v1/chat/completions" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 300
    $text = ($r.choices[0].message.content -replace "(?s)<think>.*?</think>", "").Trim()
    if (-not $text) { throw "empty reply" }
    "reply: $text"
}
Check "Tool calling" {
    $body = @{
        model = $chat; max_tokens = 600
        messages = @(@{ role = "user"; content = "What is the weather in Amsterdam? Use the tool." })
        tools = @(@{ type = "function"; function = @{ name = "get_weather"; description = "Get current weather for a city"
            parameters = @{ type = "object"; properties = @{ city = @{ type = "string" } }; required = @("city") } } })
    } | ConvertTo-Json -Depth 10
    $r = Invoke-RestMethod "$ollama/v1/chat/completions" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 300
    $call = $r.choices[0].message.tool_calls
    if (-not $call) { throw "model did not call the tool (try OLLAMA_CHAT_MODEL=qwen3:8b)" }
    "called $($call[0].function.name)($($call[0].function.arguments))"
}
Check "Embeddings" {
    $body = @{ model = $embed; input = "no internet, red LOS light" } | ConvertTo-Json
    $r = Invoke-RestMethod "$ollama/v1/embeddings" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 120
    "$($r.data[0].embedding.Count) dimensions"
}
Check "Postgres + pgvector" {
    $out = docker compose exec -T postgres psql -U $pgUser -d $pgDb -tAc "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm','unaccent') ORDER BY 1"
    if ($LASTEXITCODE -ne 0 -or ($out -notmatch "vector")) { throw "pgvector extension not found" }
    "extensions: " + (($out | Where-Object { $_ }) -join ", ")
}
Check "Valkey" {
    $out = docker compose exec -T valkey valkey-cli ping
    if ($out -notmatch "PONG") { throw "no PONG" }
    "PONG"
}

if ($failed -gt 0) { Write-Host "`n$failed check(s) failed." -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed - platform is ready." -ForegroundColor Green
