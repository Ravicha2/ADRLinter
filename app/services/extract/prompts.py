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
    "Scoping:\n"
    "- Use wildcard subjects (e.g., app.services.*) when the ADR constrains an entire namespace\n"
    "- Use specific FQN subjects when the ADR constrains a single module\n"
    "- Never use bare * as a subject\n"
    "- Objects must always be specific FQNs, never wildcards\n"
    "\n"
    "Exclusion pattern — 'no module outside X':\n"
    "When an ADR says 'no module outside X shall do Y', extract TWO constraints:\n"
    "  1. app.*  <prohibits_*>  <object>   — the general prohibition across the codebase\n"
    "  2. X.*    <requires_*>   <object>   — the explicit permission/responsibility of X\n"
    "The pair resolves by specificity: the more specific subject (X.*) overrides the general one (app.*).\n"
    "\n"
    "Each constraint has: subject, predicate, object, justification (the natural language reason from the ADR text).\n"
    "\n"
    "extraction_text must be a verbatim substring from the ADR document so that character positions can be located."
)

FEW_SHOT_EXAMPLES = [
    lx.data.ExampleData(
        text="Direct MySQL connections are prohibited for services "
             "in the app.services namespace.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.services namespace",
                attributes={
                    "subject": "app.services.*",
                    "predicate": "prohibits_dependency",
                    "object": "mysql.connector",
                    "justification": "Direct MySQL connections are prohibited for services.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="All API endpoints shall implement authentication "
             "through app.auth.middleware.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.auth.middleware",
                attributes={
                    "subject": "app.api.*",
                    "predicate": "requires_dependency",
                    "object": "app.auth.middleware",
                    "justification": "All API endpoints must implement authentication through the middleware.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="All services in the app.services namespace must "
             "import app.common.logging for structured log output.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.common.logging",
                attributes={
                    "subject": "app.services.*",
                    "predicate": "requires_dependency",
                    "object": "app.common.logging",
                    "justification": "All services must import the structured logging module.",
                },
            )
        ],
    ),
    lx.data.ExampleData(
        text="No module outside app.auth shall implement "
            "authentication logic. Only app.auth.middleware is permitted "
            "to define authentication behavior.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="No module outside app.auth",
                attributes={
                    "subject": "app.*",
                    "predicate": "prohibits_implementation",
                    "object": "app.auth.middleware",
                    "justification": "No module outside app.auth shall implement authentication logic.",
                },
            ),
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="Only app.auth.middleware is permitted",
                attributes={
                    "subject": "app.auth.*",
                    "predicate": "requires_implementation",
                    "object": "app.auth.middleware",
                    "justification": "Only app.auth.middleware is permitted to define authentication behavior.",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text="We will use Black for code formatting and isort for "
             "import sorting. Line length is set to 88 characters.",
        extractions=[],
    ),
    lx.data.ExampleData(
        text="No module outside app.database shall import mysql.connector directly.",
        extractions=[
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="No module outside app.database",
                attributes={
                    "subject": "app.*",
                    "predicate": "prohibits_dependency",
                    "object": "mysql.connector",
                    "justification": "No module outside app.database shall import mysql.connector directly.",
                },
            ),
            lx.data.Extraction(
                extraction_class="adr_constraint",
                extraction_text="app.database",
                attributes={
                    "subject": "app.database.*",
                    "predicate": "requires_dependency",
                    "object": "mysql.connector",
                    "justification": "app.database is the sole permitted interface for mysql.connector imports.",
                },
            ),
        ],
    ),
]