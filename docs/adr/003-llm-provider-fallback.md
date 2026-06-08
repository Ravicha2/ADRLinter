# 3. LLM Provider Fallback for ADR Extraction

Date: 2026-06-08

## Status

Accepted

## Context

The langextract library's built-in Ollama provider does not reliably extract constraints from Ollama cloud models. Testing with `gemma4:31b-cloud` and `qwen3.5:cloud` through `http://localhost:11434` shows:

- **gemma4:31b-cloud**: Returns empty responses. Langextract's chunked extraction produces JSON parse errors (`Failed to parse JSON content: Expecting value: line 1 column 1 (char 0)`). Zero constraints extracted.
- __qwen3.5:cloud__: Returns extraction content but langextract cannot align char intervals. All extractions have `char_interval=None`, which our code filters as `malformed_extraction`. Zero valid constraints.

The `ollama` Python client itself works fine for direct chat calls with JSON format. The problem is langextract's internal chunking and response parsing, not the Ollama connectivity.

The pilot validation (GO_NO_GO.md) achieved 88.89% precision/recall using GPT-4o, not Ollama cloud models. Assumption A3 from the decision log ("langextract can extract from Ollama cloud models") is broken.

## Decision

1. **Ollama cloud models are not viable for langextract extraction production use.** The langextract Ollama provider cannot reliably parse cloud model responses into structured extractions with char intervals.
2. **OpenRouter is the fallback production provider.** OpenRouter provides OpenAI-compatible API access to multiple models (GPT-4o, Claude, etc.) with reliable structured output. The `langextract[openai]` extra already installed in `pyproject.toml` supports this path.
3. __No refactoring yet.__ The extraction module (`langextract.py`) keeps its current structure. When OpenRouter is configured, only `LangExtractConfig` values (model_id, model_url, api_key_env) need to change. No code changes required.

## Consequences

- ADR constraint extraction will produce valid results once OpenRouter is configured as the provider.