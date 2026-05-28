import os

from fastapi import FastAPI
from neo4j import GraphDatabase

app = FastAPI()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


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
