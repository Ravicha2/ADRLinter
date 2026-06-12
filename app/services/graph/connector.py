"""Neo4j graph store for ADG persistence."""

from __future__ import annotations

from neo4j import GraphDatabase

from services.fqn import FQN
from services.models import ADG, ConstraintEdge, Edge, FQNKind, FQNNode, PredicateType

KIND_TO_LABEL = {
    FQNKind.MODULE: "Module",
    FQNKind.CLASS: "Class",
    FQNKind.FUNCTION: "Function",
    FQNKind.METHOD: "Method",
    FQNKind.EXTERNAL: "External",
}

EDGE_KIND_TO_REL = {
    "CONTAINS": "CONTAINS",
    "CALLS": "CALLS",
    "INHERITS": "INHERITS",
    "IMPORTS": "IMPORTS",
}

PREDICATE_TO_REL = {
    PredicateType.PROHIBITS_DEPENDENCY: "PROHIBITS_DEPENDENCY",
    PredicateType.REQUIRES_IMPLEMENTATION: "REQUIRES_IMPLEMENTATION",
    PredicateType.REQUIRES_DEPENDENCY: "REQUIRES_DEPENDENCY",
    PredicateType.PROHIBITS_IMPLEMENTATION: "PROHIBITS_IMPLEMENTATION",
}

REL_TO_PREDICATE = {val: key for key, val in PREDICATE_TO_REL.items()}
CONSTRAINT_LABEL = "ConstraintEdge"
PREDICATE_VALUES = list(PREDICATE_TO_REL.values())

class GraphStore:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver = None

    def connect(self) -> None:
        self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def _session(self):
        if self._driver is None:
            raise RuntimeError("Call connect() before using the store")
        return self._driver.session(database=self._database)

    @staticmethod
    def _row_to_fqn_node(record) -> FQNNode:
        props = dict(record["n"])
        return FQNNode(
            fqn=FQN.from_dotted(props["fqn"]),
            kind=FQNKind(props["kind"]),
            file_path=props["file_path"],
            line_start=props["line_start"],
            line_end=props["line_end"],
            start_byte=props.get("start_byte", 0),
            end_byte=props.get("end_byte", 0),
        )

    def create_schema(self) -> None:
        """Create index and constraints"""
        with self._session() as session:
            session.run("CREATE CONSTRAINT fqn_unique IF NOT EXISTS FOR (n:FQNNode) REQUIRE n.fqn IS UNIQUE")
            session.run("CREATE INDEX fqn_file_path IF NOT EXISTS FOR (n:FQNNode) ON (n.file_path)")
            session.run("CREATE INDEX constraint_adr_id IF NOT EXISTS FOR (c:ConstraintEdge) ON (c.adr_id)")

    def clear_all(self) -> None:
        """Delete all nodes and relationships"""
        with self._session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    # --- Node CRUD ---

    def store_node(self, node: FQNNode) -> None:
        label = KIND_TO_LABEL[node.kind]
        with self._session() as session:
            session.run(
                f"MERGE (n:FQNNode:{label} {{fqn: $fqn}}) "
                "SET n.kind = $kind, n.file_path = $file_path, "
                "n.line_start = $line_start, n.line_end = $line_end, "
                "n.start_byte = $start_byte, n.end_byte = $end_byte",
                fqn=str(node.fqn), kind=node.kind.value,
                file_path=node.file_path,
                line_start=node.line_start, line_end=node.line_end,
                start_byte=node.start_byte, end_byte=node.end_byte,
            )

    def load_node(self, fqn: FQN) -> FQNNode | None:
        with self._session() as session:
            result = session.run("MATCH (n:FQNNode {fqn: $fqn}) RETURN n", fqn=str(fqn))
            record = result.single()
            if record is None:
                return None
            return self._row_to_fqn_node(record)

    def find_nodes_by_file(self, file_path: str) -> list[FQNNode]:
        with self._session() as session:
            result = session.run(
                "MATCH (n:FQNNode) WHERE n.file_path = $file_path RETURN n",
                file_path=file_path,
            )
            return [self._row_to_fqn_node(record) for record in result]

    # --- Edge CRUD ---

    def store_edge(self, edge: Edge) -> None:
        rel_type = EDGE_KIND_TO_REL[edge.kind]
        with self._session() as session:
            session.run(
                f"MATCH (src:FQNNode {{fqn: $source}}) "
                f"MATCH (tgt:FQNNode {{fqn: $target}}) "
                f"CREATE (src)-[:{rel_type}]->(tgt)",
                source=edge.source, target=edge.target,
            )

    def load_edges_from(self, fqn: FQN) -> list[Edge]:
        with self._session() as session:
            result = session.run(
                "MATCH (src:FQNNode {fqn: $fqn})-[r]->(tgt:FQNNode) "
                "RETURN type(r) AS kind, tgt.fqn AS target",
                fqn=str(fqn),
            )
            return [
                Edge(source=str(fqn), target=r["target"], kind=r["kind"])
                for r in result
            ]

    # --- Constraint edge CRUD ---

    def store_constraint_edge(self, constraint_edge: ConstraintEdge) -> None:
        predicate_str = PREDICATE_TO_REL[constraint_edge.predicate]
        with self._session() as session:
            session.run(
                "CREATE (c:ConstraintEdge {"
                "subject: $subject, predicate: $predicate, object: $object, "
                "justification: $justification, adr_id: $adr_id, adr_path: $adr_path, "
                "char_start: $char_start, char_end: $char_end, "
                "specificity: $specificity"
                "})",
                subject=constraint_edge.subject,
                predicate=predicate_str,
                object=constraint_edge.object,
                adr_id=constraint_edge.adr_id,
                adr_path=constraint_edge.adr_path,
                justification=constraint_edge.justification,
                char_start=constraint_edge.char_interval[0] if constraint_edge.char_interval else None,
                char_end=constraint_edge.char_interval[1] if constraint_edge.char_interval else None,
                specificity=constraint_edge.specificity,
            )

    @staticmethod
    def _row_to_constraint_edge(record) -> ConstraintEdge:
        edge_data = dict(record["c"])
        predicate = REL_TO_PREDICATE[edge_data["predicate"]]
        char_interval = None
        if edge_data.get("char_start") is not None:
            char_interval = (edge_data["char_start"], edge_data["char_end"])
        return ConstraintEdge(
            subject=edge_data["subject"],
            predicate=predicate,
            object=edge_data["object"],
            justification=edge_data["justification"],
            adr_id=edge_data["adr_id"],
            adr_path=edge_data["adr_path"],
            char_interval=char_interval,
            specificity=edge_data.get("specificity", 0.0),
        )

    def load_constraint_edges(self, adr_id: str) -> list[ConstraintEdge]:
        with self._session() as session:
            result = session.run(
                "MATCH (c:ConstraintEdge) WHERE c.adr_id = $adr_id RETURN c",
                adr_id=adr_id,
            )
            return [self._row_to_constraint_edge(record) for record in result]

    def load_all_constraint_edges(self) -> list[ConstraintEdge]:
        with self._session() as session:
            result = session.run("MATCH (c:ConstraintEdge) RETURN c")
            return [self._row_to_constraint_edge(record) for record in result]

    def delete_constraints_by_adr(self, adr_id: str) -> None:
        with self._session() as session:
            session.run(
                "MATCH (c:ConstraintEdge) WHERE c.adr_id = $adr_id DELETE c",
                adr_id=adr_id,
            )

    # --- Full ADG ---

    def store_adg(self, adg: ADG) -> None:
        for node in adg.nodes:
            self.store_node(node)
        for edge in adg.edges:
            self.store_edge(edge)
        for constraint_edge in adg.constraint_edges:
            self.store_constraint_edge(constraint_edge)

    def load_adg(self) -> ADG:
        with self._session() as session:
            node_result = session.run("MATCH (n:FQNNode) RETURN n")
            nodes = [self._row_to_fqn_node(record) for record in node_result]

            edge_result = session.run(
                "MATCH (src:FQNNode)-[r]->(tgt:FQNNode) "
                "WHERE type(r) IN $rel_types "
                "RETURN src.fqn AS source, type(r) AS kind, tgt.fqn AS target",
                rel_types=list(EDGE_KIND_TO_REL.values()),
            )
            edges = [Edge(source=r["source"], target=r["target"], kind=r["kind"]) for r in edge_result]

            constraint_edges = self.load_all_constraint_edges()
            return ADG(nodes=nodes, edges=edges, constraint_edges=constraint_edges)

    def delete_nodes_by_file(self, file_path: str) -> None:
        with self._session() as session:
            session.run(
                "MATCH (n:FQNNode) WHERE n.file_path = $file_path "
                "DETACH DELETE n",
                file_path=file_path,
            )