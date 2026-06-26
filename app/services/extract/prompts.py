"""Prompt domain knowledge for ADR constraint extraction.

This module imports langextract to define few-shot examples as
lx.data.ExampleData objects, which the engine passes directly
to lx.extract().
"""
from __future__ import annotations

import langextract as lx

PROMPT_DESCRIPTION = (
    "Extract architectural constraints from ADR documents.\n"
    "\n"
    "Predicates:\n"
    "- prohibits_dependency: the subject module must NOT import or call the object module\n"
    "- requires_dependency: the subject module MUST import or call the object module\n"
    "- prohibits_implementation: the subject module must NOT define the logic described by the object\n"
    "- requires_implementation: the subject module MUST define the logic described by the object\n"
    "\n"
    "Fields per extraction:\n"
    "- subject_role_general: the module or namespace the constraint applies to (e.g., 'app.services'). "
    "MUST be picked from the 'Codebase packages' list if provided. If no list, infer from context.\n"
    "- subject_role_specific: the ADR's natural-language term for the subject role (e.g., 'endpoint', 'service'). "
    "Short noun or noun phrase from the ADR text.\n"
    "- object_role_general: the module or namespace the constraint targets (e.g., 'app.auth', 'mysql'). "
    "MUST be picked from the 'Codebase packages' list if provided. For external dependencies, use the library name.\n"
    "- object_role_specific: the ADR's natural-language term for the object role (e.g., 'authentication logic', 'MySQL connector'). "
    "Short noun or noun phrase from the ADR text.\n"
    "- predicate: one of prohibits_dependency, requires_dependency, prohibits_implementation, requires_implementation\n"
    "- justification: concise reason for the constraint, from the ADR\n"
    "- extraction_text: verbatim substring from the ADR that motivates this constraint\n"
    "\n"
    "Scoping:\n"
    "- Use the root package as subject_role_general for codebase-wide constraints (e.g., 'we will use X' tech-choice ADRs)\n"
    "- Do NOT use wildcards in role_general fields. Wildcards are implied by kind filter + CONTAINS walk during resolution.\n"
    "- subject_role_specific and object_role_specific are natural-language terms, NOT code identifiers\n"
    "\n"
    "Extraction rules:\n"
    "1. MULTIPLE PREDICATES: emit more than one constraint when a single sentence constrains "
    "both what a module must do (implementation layer) and how (dependency layer). "
    "Example: 'All B must implement Y using X package' → "
    "subject_role_general=B, object_role_general=X, predicate=requires_implementation + "
    "subject_role_general=B, object_role_general=X, predicate=requires_dependency.\n"
    "\n"
    "2. EXCLUSION PATTERN — 'no module outside X shall do Y': extract TWO constraints:\n"
    "   a. subject_role_general=codebase_root, predicate=prohibits_*, object_role_general=Y  — general prohibition\n"
    "   b. subject_role_general=X, predicate=requires_*, object_role_general=Y  — explicit responsibility of X\n"
    "\n"
    "3. LAYER DISAMBIGUATION: parse verb and object as a unit.\n"
    "Verbs carry polarity (required vs prohibited). Objects carry layer (dependency vs implementation). Neither is sufficient alone.\n"
    "Polarity from verb:\n"
    "\n"
    "must / shall / owns / is responsible for → required\n"
    "must not / shall not / may not → prohibited\n"
    "\n"
    "Layer from object:\n"
    "Looks like a module/class/library reference → *_dependency\n"
    "Describes a behaviour, pattern, or logic → *_implementation\n"
)

FEW_SHOT_EXAMPLES = [
    lx.data.ExampleData(
        text="Direct MySQL connections are prohibited for services "
             "in the app.services namespace.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="Direct MySQL connections are prohibited for services",
                attributes={
                    "subject_role_general": "app.services",
                    "subject_role_specific": "service",
                    "object_role_general": "mysql",
                    "object_role_specific": "MySQL connector",
                    "predicate": "prohibits_dependency",
                    "justification": "Direct MySQL connections are prohibited for services.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="All API endpoints shall implement authentication "
             "through middleware.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="All API endpoints shall implement authentication",
                attributes={
                    "subject_role_general": "app.api",
                    "subject_role_specific": "endpoint",
                    "object_role_general": "app.auth",
                    "object_role_specific": "middleware",
                    "predicate": "requires_implementation",
                    "justification": "All API endpoints must implement authentication through the middleware.",
                },
            ),
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="through middleware",
                attributes={
                    "subject_role_general": "app.api",
                    "subject_role_specific": "endpoint",
                    "object_role_general": "app.auth",
                    "object_role_specific": "middleware",
                    "predicate": "requires_dependency",
                    "justification": "All API endpoints must use middleware for authentication.",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text="All services must import internal logging module for structured log output.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="must import internal logging module",
                attributes={
                    "subject_role_general": "app.services",
                    "subject_role_specific": "service",
                    "object_role_general": "app.logging",
                    "object_role_specific": "logging module",
                    "predicate": "requires_dependency",
                    "justification": "All services must import the structured logging module.",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text="No module outside auth module shall implement "
            "authentication logic. Only middleware module is permitted "
            "to define authentication behavior.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="No module outside app.auth",
                attributes={
                    "subject_role_general": "app",
                    "subject_role_specific": "module",
                    "object_role_general": "app.auth",
                    "object_role_specific": "authentication logic",
                    "predicate": "prohibits_implementation",
                    "justification": "No module outside auth shall implement authentication logic.",
                },
            ),
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="Only app.auth.middleware is permitted",
                attributes={
                    "subject_role_general": "app.auth",
                    "subject_role_specific": "auth middleware",
                    "object_role_general": "app.auth",
                    "object_role_specific": "authentication behavior",
                    "predicate": "requires_implementation",
                    "justification": "Only middleware is permitted to define authentication behavior.",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text="We will use Flask. Server should be simple - pretty much just "
             "with a GraphQL endpoint and GraphfixQL.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="We will use Flask",
                attributes={
                    "subject_role_general": "app",
                    "subject_role_specific": "server",
                    "object_role_general": "flask",
                    "object_role_specific": "Flask web framework",
                    "predicate": "requires_dependency",
                    "justification": "The server will use Flask as its web framework.",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text="No module outside app.database shall import mysql.connector directly.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="No module outside app.database",
                attributes={
                    "subject_role_general": "app",
                    "subject_role_specific": "module",
                    "object_role_general": "mysql",
                    "object_role_specific": "MySQL connector",
                    "predicate": "prohibits_dependency",
                    "justification": "No module outside app.database shall import mysql.connector directly.",
                },
            ),
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.database",
                attributes={
                    "subject_role_general": "app.database",
                    "subject_role_specific": "database module",
                    "object_role_general": "mysql",
                    "object_role_specific": "MySQL connector",
                    "predicate": "requires_dependency",
                    "justification": "app.database is the sole permitted interface for mysql.connector imports.",
                },
            ),
        ],
    ),
]