"""Neo4j graph store for ADG persistence."""

from __future__ import annotations

import logging

from neo4j import GraphDatabase

from services.cpt.dismissal import Dismissal
from services.fqn import FQN
from services.models import ADG, ConstraintEdge, DependencyRole, Edge, FQNKind, FQNNode, PredicateType

log = logging.getLogger(__name__)

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
        log.info("GraphStore connected to %s (db=%s)", self._uri, self._database)

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None
            log.info("GraphStore connection closed")

    def _session(self):
        if self._driver is None:
            raise RuntimeError("Call connect() before using the store")
        return self._driver.session(database=self._database)

    @staticmethod
    def _row_to_fqn_node(record) -> FQNNode:
        props = dict(record["n"])
        role_str = props.get("role", "internal")
        try:
            role = DependencyRole(role_str)
        except ValueError:
            role = DependencyRole.INTERNAL
        return FQNNode(
            fqn=FQN.from_dotted(props["fqn"]),
            kind=FQNKind(props["kind"]),
            file_path=props["file_path"],
            line_start=props["line_start"],
            line_end=props["line_end"],
            start_byte=props.get("start_byte", 0),
            end_byte=props.get("end_byte", 0),
            role=role,
        )

    def create_schema(self) -> None:
        """Create index and constraints"""
        with self._session() as session:
            session.run("CREATE CONSTRAINT fqn_unique IF NOT EXISTS FOR (n:FQNNode) REQUIRE n.fqn IS UNIQUE")
            session.run("CREATE INDEX fqn_file_path IF NOT EXISTS FOR (n:FQNNode) ON (n.file_path)")
            session.run("CREATE CONSTRAINT dismissal_identity_unique IF NOT EXISTS FOR (d:Dismissal) REQUIRE d.identity_hash IS UNIQUE")
        log.info("create_schema: indexes and constraints ensured")

    def clear_all(self) -> None:
        """Delete all nodes and relationships"""
        with self._session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        log.warning("clear_all: all nodes and relationships deleted from Neo4j")

    # --- Node CRUD ---

    def store_node(self, node: FQNNode) -> None:
        label = KIND_TO_LABEL[node.kind]
        log.debug("store_node: %s [%s] at %s:%d", node.fqn, label, node.file_path, node.line_start)
        with self._session() as session:
            session.run(
                f"MERGE (n:FQNNode:{label} {{fqn: $fqn}}) "
                "SET n.kind = $kind, n.file_path = $file_path, "
                "n.line_start = $line_start, n.line_end = $line_end, "
                "n.start_byte = $start_byte, n.end_byte = $end_byte, "
                "n.role = $role",
                fqn=str(node.fqn), kind=node.kind.value,
                file_path=node.file_path,
                line_start=node.line_start, line_end=node.line_end,
                start_byte=node.start_byte, end_byte=node.end_byte,
                role=node.role.value,
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
        log.debug("store_edge: (%s)-[:%s]->(%s)", edge.source, rel_type, edge.target)
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

    def _ensure_endpoint(self, fqn_str: str) -> None:
        """Ensure an FQNNode exists for fqn_str, creating a temp EXTERNAL node if needed.

        Uses MERGE so exact-match subjects/objects (e.g. ``app.auth.middleware``)
        find their real code node, while wildcards/orphans (e.g. ``app.api.*``)
        get a temp EXTERNAL node with sentinel values.
        """
        with self._session() as session:
            result = session.run(
                "MERGE (n:FQNNode {fqn: $fqn}) "
                "ON CREATE SET n.kind = 'external', n.file_path = '', "
                "n.line_start = -1, n.line_end = -1, "
                "n.start_byte = 0, n.end_byte = 0, n.role = 'unknown' "
                "RETURN n.kind AS kind",
                fqn=fqn_str,
            )
            record = result.single()
            if record and record["kind"] == "external":
                session.run(
                    "MATCH (n:FQNNode {fqn: $fqn, kind: 'external'}) SET n:External",
                    fqn=fqn_str,
                )

    def store_constraint_edge(self, constraint_edge: ConstraintEdge) -> None:
        predicate_str = PREDICATE_TO_REL[constraint_edge.predicate]
        log.info(
            "store_constraint_edge: [%s] (%s)-[:%s]->(%s) specificity=%.1f",
            constraint_edge.adr_id, constraint_edge.subject, predicate_str,
            constraint_edge.object, constraint_edge.specificity,
        )
        self._ensure_endpoint(constraint_edge.subject)
        self._ensure_endpoint(constraint_edge.object)
        with self._session() as session:
            session.run(
                f"MATCH (src:FQNNode {{fqn: $subject}}) "
                f"MATCH (tgt:FQNNode {{fqn: $object}}) "
                f"CREATE (src)-[r:{predicate_str} {{"
                "justification: $justification, adr_id: $adr_id, adr_path: $adr_path, "
                "specificity: $specificity"
                "}]->(tgt)",
                subject=constraint_edge.subject,
                object=constraint_edge.object,
                justification=constraint_edge.justification,
                adr_id=constraint_edge.adr_id,
                adr_path=constraint_edge.adr_path,
                specificity=constraint_edge.specificity,
            )

    @staticmethod
    def _row_to_constraint_edge(record) -> ConstraintEdge:
        predicate = REL_TO_PREDICATE[record["predicate"]]
        return ConstraintEdge(
            subject=record["subject"],
            predicate=predicate,
            object=record["object"],
            justification=record["justification"],
            adr_id=record["adr_id"],
            adr_path=record["adr_path"],
            specificity=record.get("specificity", 0.0),
        )

    def load_constraint_edges(self, adr_id: str) -> list[ConstraintEdge]:
        with self._session() as session:
            result = session.run(
                "MATCH (src:FQNNode)-[r]->(tgt:FQNNode) "
                "WHERE type(r) IN $predicate_types AND r.adr_id = $adr_id "
                "RETURN src.fqn AS subject, type(r) AS predicate, tgt.fqn AS object, "
                "r.justification AS justification, r.adr_id AS adr_id, "
                "r.adr_path AS adr_path, "
                "r.specificity AS specificity",
                adr_id=adr_id,
                predicate_types=PREDICATE_VALUES,
            )
            return [self._row_to_constraint_edge(record) for record in result]

    def load_all_constraint_edges(self) -> list[ConstraintEdge]:
        with self._session() as session:
            result = session.run(
                "MATCH (src:FQNNode)-[r]->(tgt:FQNNode) "
                "WHERE type(r) IN $predicate_types "
                "RETURN src.fqn AS subject, type(r) AS predicate, tgt.fqn AS object, "
                "r.justification AS justification, r.adr_id AS adr_id, "
                "r.adr_path AS adr_path, "
                "r.specificity AS specificity",
                predicate_types=PREDICATE_VALUES,
            )
            return [self._row_to_constraint_edge(record) for record in result]

    def delete_constraints_by_adr(self, adr_id: str) -> None:
        with self._session() as session:
            session.run(
                "MATCH (src:FQNNode)-[r]->(tgt:FQNNode) "
                "WHERE type(r) IN $predicate_types AND r.adr_id = $adr_id "
                "DELETE r",
                adr_id=adr_id,
                predicate_types=PREDICATE_VALUES,
            )
        log.info("delete_constraints_by_adr: deleted constraints for adr_id=%s", adr_id)

    # --- Dismissal CRUD ---

    def store_dismissal(self, dismissal: Dismissal) -> None:
        """Persist a Dismissal node. MERGE on identity_hash (idempotent)."""
        with self._session() as session:
            session.run(
                "MERGE (d:Dismissal {identity_hash: $identity_hash}) "
                "SET d.short_id = $short_id, "
                "d.subject = $subject, d.predicate = $predicate, "
                "d.object = $object, d.matched_fqn = $matched_fqn, "
                "d.adr_id = $adr_id, d.dismissed_at = $dismissed_at",
                identity_hash=dismissal.identity_hash,
                short_id=dismissal.short_id,
                subject=dismissal.subject,
                predicate=dismissal.predicate,
                object=dismissal.object,
                matched_fqn=dismissal.matched_fqn,
                adr_id=dismissal.adr_id,
                dismissed_at=dismissal.dismissed_at,
            )
        log.info("store_dismissal: stored dismissal short_id=%s for adr_id=%s", dismissal.short_id, dismissal.adr_id)

    @staticmethod
    def _row_to_dismissal(record) -> Dismissal:
        return Dismissal(
            short_id=record["short_id"],
            identity_hash=record["identity_hash"],
            subject=record["subject"],
            predicate=record["predicate"],
            object=record["object"],
            matched_fqn=record["matched_fqn"],
            adr_id=record["adr_id"],
            dismissed_at=record["dismissed_at"],
        )

    def load_dismissals(self) -> list[Dismissal]:
        """Load all Dismissal nodes from Neo4j."""
        with self._session() as session:
            result = session.run(
                "MATCH (d:Dismissal) "
                "RETURN d.short_id AS short_id, d.identity_hash AS identity_hash, "
                "d.subject AS subject, d.predicate AS predicate, "
                "d.object AS object, d.matched_fqn AS matched_fqn, "
                "d.adr_id AS adr_id, d.dismissed_at AS dismissed_at"
            )
            return [self._row_to_dismissal(r) for r in result]

    def delete_dismissals_by_adr(self, adr_id: str) -> int:
        """Delete all dismissals for a given adr_id. Returns count deleted."""
        with self._session() as session:
            result = session.run(
                "MATCH (d:Dismissal {adr_id: $adr_id}) DELETE d RETURN count(d) AS deleted",
                adr_id=adr_id,
            )
            record = result.single()
            deleted = record["deleted"] if record else 0
        log.info("delete_dismissals_by_adr: deleted %d dismissals for adr_id=%s", deleted, adr_id)
        return deleted

    def delete_all_dismissals(self) -> int:
        """Delete all Dismissal nodes. Used for seed rebuild."""
        with self._session() as session:
            result = session.run("MATCH (d:Dismissal) DELETE d RETURN count(d) AS deleted")
            record = result.single()
            deleted = record["deleted"] if record else 0
        log.info("delete_all_dismissals: deleted %d dismissals", deleted)
        return deleted

    # --- Full ADG ---

    def store_adg(self, adg: ADG) -> None:
        log.info("store_adg: storing %d nodes, %d edges, %d constraint_edges", len(adg.nodes), len(adg.edges), len(adg.constraint_edges))
        for node in adg.nodes:
            self.store_node(node)
        for edge in adg.edges:
            self.store_edge(edge)
        for constraint_edge in adg.constraint_edges:
            self.store_constraint_edge(constraint_edge)
        log.info("store_adg: done")

    def load_adg(self) -> ADG:
        log.info("load_adg: loading full ADG from Neo4j")
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
            log.info("load_adg: loaded %d nodes, %d edges, %d constraint_edges", len(nodes), len(edges), len(constraint_edges))
            return ADG(nodes=nodes, edges=edges, constraint_edges=constraint_edges)

    def delete_nodes_by_file(self, file_path: str) -> None:
        """Delete all FQNNode/structural-edge data for *file_path*.

        Constraint edges (ADR-derived) survive: before deleting the code
        node, any constraint relationships wired to it are saved and
        re-created with temp EXTERNAL placeholder endpoints.
        """
        affected = self._find_constraints_on_file(file_path)
        with self._session() as session:
            session.run(
                "MATCH (n:FQNNode) WHERE n.file_path = $file_path "
                "DETACH DELETE n",
                file_path=file_path,
            )
        for ce in affected:
            self.store_constraint_edge(ce)
        log.info("delete_nodes_by_file: deleted nodes for file=%s", file_path)

    def _find_constraints_on_file(self, file_path: str) -> list[ConstraintEdge]:
        """Return constraint edges connected to nodes with the given file_path."""
        with self._session() as session:
            outgoing_records = list(session.run(
                "MATCH (n:FQNNode) WHERE n.file_path = $file_path "
                "MATCH (n)-[r]->(tgt:FQNNode) WHERE type(r) IN $predicate_types "
                "RETURN n.fqn AS subject, type(r) AS predicate, tgt.fqn AS object, "
                "r.justification AS justification, r.adr_id AS adr_id, "
                "r.adr_path AS adr_path, "
                "r.specificity AS specificity",
                file_path=file_path,
                predicate_types=PREDICATE_VALUES,
            ))
            incoming_records = list(session.run(
                "MATCH (n:FQNNode) WHERE n.file_path = $file_path "
                "MATCH (src:FQNNode)-[r]->(n) WHERE type(r) IN $predicate_types "
                "RETURN src.fqn AS subject, type(r) AS predicate, n.fqn AS object, "
                "r.justification AS justification, r.adr_id AS adr_id, "
                "r.adr_path AS adr_path, "
                "r.specificity AS specificity",
                file_path=file_path,
                predicate_types=PREDICATE_VALUES,
            ))
        seen: set[tuple[str, str, str, str]] = set()
        results: list[ConstraintEdge] = []
        for record in outgoing_records + incoming_records:
            ce = self._row_to_constraint_edge(record)
            key = (ce.subject, ce.predicate.value, ce.object, ce.adr_id)
            if key not in seen:
                seen.add(key)
                results.append(ce)
        return results