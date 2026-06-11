"""Neo4j graph store for ADG persistence."""

from __future__ import annotations

from neo4j import GraphDatabase, ManagedTransaction

from services.fqn import FQN
from services.models import ADG, ConstraintEdge, Edge, FQNKind, FQNNode, PredicateType

KIND_TO_LABEL = {
    FQNKind.MODULE: "Module",
    FQNKind.CLASS: "Class",
    FQNKind.FUNCTION: "Function",
    FQNKind.METHOD: "Method",
    FQNKind.EXTERNAL: "External",
}

PREDICATE_TO_REL = {
    PredicateType.PROHIBITS_DEPENDENCY: "PROHIBITS_DEPENDENCY",
    PredicateType.REQUIRES_IMPLEMENTATION: "REQUIRES_IMPLEMENTATION",
    PredicateType.REQUIRES_DEPENDENCY: "REQUIRES_DEPENDENCY",
    PredicateType.PROHIBITS_IMPLEMENTATION: "PROHIBITS_IMPLEMENTATION",
}

REL_TO_PREDICATE = {val: key for key, val in PREDICATE_TO_REL.items()}

class GraphStore:
    def __init__(self, uri:str, user:str, password: str, database: str="neo4j") -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver = None

    def connector(self) -> None:
        self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def _session(self):
        return self._driver.session(database=self._database)
    
    def create_schema(self) -> None:
        """Create index and constraints"""
        with self._session as s:
            s.run("CREATE CONSTRAINT fqn_unique IF NOT EXIST FOR (n:FQNNode REQUIRE n.fqn IS UNIQUE)")
            s.run("CREATE INDEX fqn_file_path IF NOT EXISTS FOR (n:FQNNode) ON (n.file_path)")

    def clear_all(self) -> None:
        """Delete all nodes and relationships"""
        with self._session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    # --- Node CRUD ---

    def store_node(self, node: FQNNode) -> None:
        label = KIND_TO_LABEL[node.kind]
        with self._session() as s:
            s.run(
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
        with self._session() as s:
            result = s.run("MATCH (n: FQNNode {fqn: $fqn}) RETURN n", fqn=str(fqn))
            record = result.single()
            if record is None:
                return None
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

    def find_nodes_by_file(self, file_path: str) -> list[FQNNode]:
        with self._session() as s:
            result = s.run(
                "MATCH (n:FQNNode) WHERE n.file_path = $file_path RETURN n",
                file_path=file_path,
            )
            nodes = []
            for record in result:
                props = dict(record["n"])
                nodes.append(FQNNode(
                    fqn=FQN.from_dotted(props["fqn"]),
                    kind=FQNKind(props["kind"]),
                    file_path=props["file_path"],
                    line_start=props["line_start"],
                    line_end=props["line_end"],
                    start_byte=props.get("start_byte", 0),
                    end_byte=props.get("end_byte", 0),
                ))
            return nodes
    
    # --- Edge CRUD ---

    def store_edge(self, edge: Edge) -> None:
        with self._session() as s:
            s.run(
                "MATCH (src:FQNNode {fqn: $source}) "
                "MATCH (tgt:FQNNode {fqn: $target}) "
                "CREATE (src)-[:%s]->(tgt)" % edge.kind,
                source=edge.source, target=edge.target,
            )
    
    def load_edges_from(self, fqn: FQN) -> list[Edge]:
        with self._session() as s:
            edge_tgt_result = s.run(
                "MATCH (src:FQNNode {fqn: $fqn})-[r]->(tgt:FQNNode) "
                "RETURN type(r) AS kind, tgt.fqn AS target",
                fqn=str(fqn),
            )
            return [
                Edge(source=str(fqn), 
                target=edge_tgt_result["target"], 
                kind=edge_tgt_result["kind"]) 
                for r in edge_tgt_result
            ]

    # --- Constraint edge CRUD ---

    def store_constraint_edge(self, constraint_edge: ConstraintEdge) -> None:
        rel_type = PREDICATE_TO_REL[constraint_edge.predicate]
        with self._session() as s:
            s.run(
                "MATCH (src:FQNNode {fqn: $subject}) "
                "MATCH (tgt:FQNNode {fqn: $object}) "
                "CREATE (src)-[r:%s {"
                "adr_id: $adr_id, adr_path: $adr_path, "
                "justification: $justification, "
                "char_start: $char_start, char_end: $char_end, "
                "specificity: $specificity"
                "}]->(tgt)" % rel_type,
                subject=constraint_edge.subject, 
                object=constraint_edge.object,
                adr_id=constraint_edge.adr_id, 
                adr_path=constraint_edge.adr_path,
                justification=constraint_edge.justification,
                char_start=constraint_edge.char_interval[0] if constraint_edge.char_interval else None,
                char_end=constraint_edge.char_interval[1] if constraint_edge.char_interval else None,
                specificity=constraint_edge.specificity,
            )
    
    def load_constraint_edges(self, adr_id: str) -> list[ConstraintEdge]:
        with self._session() as s:
            constraint_edges = s.run(
                "MATCH (src:FQNNode)-[r]->(tgt:FQNNode) "
                "WHERE r.adr_id = $adr_id "
                "RETURN src.fqn AS subject, type(r) AS predicate, "
                "tgt.fqn AS object, r.justification AS justification, "
                "r.adr_id AS adr_id, r.adr_path AS adr_path, "
                "r.char_start AS char_start, r.char_end AS char_end, "
                "r.specificity AS specificity",
                adr_id=adr_id,
            )
            edges = []
            for constraint_edge in constraint_edges:
                predicate = REL_TO_PREDICATE[constraint_edge["predicate"]]
                char_interval = None
                if constraint_edge["char_start"] is not None:
                    char_interval = (constraint_edge["char_start"], constraint_edge["char_end"])
                    edges.append(ConstraintEdge(
                        subject=constraint_edge["subject"], 
                        predicate=predicate, 
                        object=constraint_edge["object"],
                        justification=constraint_edge["justification"],
                        adr_id=constraint_edge["adr_id"], 
                        adr_path=constraint_edge["adr_path"],
                        char_interval=char_interval, 
                        specificity=constraint_edge.get("specificity", 0.0),
                    ))
