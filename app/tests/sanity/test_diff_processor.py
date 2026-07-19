"""Sanity check: run process_diff through every file status with print output."""

from services.models import Diff, FileChange
from services.cpt import process_diff


def run(label: str, diff: Diff) -> None:
    print(f"=== {label} ===")
    result = process_diff(diff)
    print(f"  commit: {result.to_sha}")
    print(f"  changed_files: {[f.path for f in result.changed_files]}")
    for c in result.changed_fqns:
        enc = str(c.enclosing_class) if c.enclosing_class is not None else "-"
        print(f"    {c.change_type:10s} {str(c.fqn):45s} class={enc:30s} module={c.enclosing_module}")
    if not result.changed_fqns:
        print("    (no changed FQNs)")
    print()


# 1. Added file
run("Added file", Diff(
    to_sha="sha1",
    from_sha="sha0",
    changed_files=[FileChange(path="app/models/user.py", status="added")],
    file_contents={"app/models/user.py": b"class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n"},
    from_contents={},
))

# 2. Deleted file
run("Deleted file", Diff(
    to_sha="sha2",
    from_sha="sha1",
    changed_files=[FileChange(path="app/models/user.py", status="deleted")],
    file_contents={},
    from_contents={"app/models/user.py": b"class User:\n    def find(self):\n        pass\n"},
))

# 3. Modified file: body change + method removed + method added
run("Modified file", Diff(
    to_sha="sha3",
    from_sha="sha2",
    changed_files=[FileChange(path="app/models/user.py", status="modified")],
    file_contents={"app/models/user.py": b"class User:\n    def find(self, active=True):\n        return self.filter(active=active)\n\n    def create(self, data):\n        pass\n"},
    from_contents={"app/models/user.py": b"class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n"},
))

# 4. Renamed file
run("Renamed file", Diff(
    to_sha="sha4",
    from_sha="sha3",
    changed_files=[FileChange(path="app/services/auth_service.py", status="renamed", old_path="app/services/user_service.py")],
    file_contents={"app/services/auth_service.py": b"def get_user(uid):\n    pass\n"},
    from_contents={"app/services/user_service.py": b"def get_user(uid):\n    pass\n"},
))

# 5. First commit (no parent)
run("First commit", Diff(
    to_sha="initial",
    from_sha=None,
    changed_files=[FileChange(path="app/models/user.py", status="added")],
    file_contents={"app/models/user.py": b"class User:\n    def find(self):\n        pass\n"},
    from_contents={},
))

# 6. Non-.py file
run("Non-.py file", Diff(
    to_sha="sha5",
    from_sha="sha4",
    changed_files=[FileChange(path="README.md", status="modified")],
    file_contents={"README.md": b"# Updated\n"},
    from_contents={"README.md": b"# Old\n"},
))

# 7. Unchanged function (same body in old and new)
run("Unchanged function", Diff(
    to_sha="sha6",
    from_sha="sha5",
    changed_files=[FileChange(path="app/utils.py", status="modified")],
    file_contents={"app/utils.py": b"def helper():\n    pass\n"},
    from_contents={"app/utils.py": b"def helper():\n    pass\n"},
))

# 8. Syntax error (should raise)
print("=== Syntax error ===")
try:
    process_diff(Diff(
        to_sha="sha7",
        from_sha="sha6",
        changed_files=[FileChange(path="app/broken.py", status="added")],
        file_contents={"app/broken.py": b"def foo(:\n    pass\n"},
        from_contents={},
    ))
    print("    ERROR: should have raised")
except Exception as e:
    print(f"    Raised as expected: {type(e).__name__}: {e}")