import os

from fastapi import FastAPI
from neo4j import GraphDatabase

from cli.config import load_config
from services.adr_extract import LangExtractConfig

app = FastAPI(title="ADRLinter", version="0.1.0")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

_langextract_config: LangExtractConfig | None = None


def _get_langextract_config() -> LangExtractConfig:
    global _langextract_config
    if _langextract_config is None:
        _langextract_config = load_config().langextract
    return _langextract_config


def get_db():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        yield driver
    finally:
        driver.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/neo4j-health")
def neo4j_health():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            result = session.run("RETURN 1 AS n")
            record = result.single()
            return {"neo4j": "reachable", "n": record["n"]}
    except Exception as e:
        return {"neo4j": "unreachable", "error": str(e)}
    finally:
        driver.close()


@app.get("/llm-health")
def llm_health():
    import httpx

    config = _get_langextract_config()
    api_key = config.api_key
    try:
        resp = httpx.get(
            f"{config.model_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m.get("id") for m in data.get("data", [])]
        return {"llm": "reachable", "model_id": config.model_id, "available_models": model_ids}
    except Exception as e:
        return {"llm": "unreachable", "error": str(e), "model_id": config.model_id}