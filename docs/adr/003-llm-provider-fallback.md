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
3. **Explicit provider routing.** Langextract's pattern-based router cannot match OpenRouter model IDs (e.g., `google/gemini-3.1-flash-lite-preview` matches neither `^gemini` for Gemini nor `^google/gemma` for Ollama). The extraction module now uses `factory.ModelConfig` with `provider="openai"` to bypass pattern routing, and maps `model_url` to `base_url` in `provider_kwargs` for the OpenAI-compatible provider. A `provider` field was added to `LangExtractConfig` (default: `"openai"`), configurable via `LANGEXTRACT_PROVIDER` env var or `repos.yaml`.

## OpenRouter Model Assignments

| Role        | Model                     | OpenRouter ID                          |
|-------------|---------------------------|----------------------------------------|
| Ingestion  | Gemini 3.1 Flash Lite     | `google/gemini-3.1-flash-lite-preview` |
| Eval        | Gemma 4 26B A4B           | `google/gemma-4-26b-a4b-it:free`       |
| Healthcheck | Qwen3 235B A22B           | `qwen/qwen3-235b-a22b:free`            |

- **Ingestion**: Gemini 3.1 Flash Lite handles constraint extraction via langextract. Chosen for speed and cost efficiency on high-volume document processing.
- **Eval**: Gemma 4 26B A4B (free tier) validates extracted constraints. The 26B parameter size provides sufficient reasoning for evaluation accuracy.
- **Healthcheck**: Qwen3 235B A22B (free tier) confirms provider connectivity before extraction runs. The MoE architecture responds quickly for lightweight health probes.

## Consequences

- ADR constraint extraction will produce valid results once OpenRouter is configured as the provider.
- Three distinct models cover ingestion, evaluation, and healthcheck roles, balancing cost and capability.