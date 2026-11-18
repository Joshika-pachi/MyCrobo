"""Per-rule tests: each rule must catch the planted issue and ignore look-alikes.

Fake secrets are assembled at runtime so this file itself never contains one.
"""

import textwrap

from mycrobo.engine import scan
from mycrobo.models import Severity


def run(tmp_path, files, rule=None):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"))
    findings = scan(tmp_path).findings
    return [f for f in findings if rule is None or f.rule == rule]


def lines(findings, file=None):
    return sorted(f.line_start for f in findings if file is None or f.file == file)


# --- security/hardcoded-secret -------------------------------------------------

AWS = "AKIA" + "Q7XK2M9PLR4TWZ3B"
GITHUB = "ghp_" + "a1B2c3D4" * 5
DB_PASSWORD = "S3cr3t" + "Passw0rd"
API_KEY = "q8Zr3LmT" + "0vXa9Kp2"


def test_secrets_detected_and_redacted(tmp_path):
    code = "\n".join([
        f'AWS_KEY = "{AWS}"',
        f'GITHUB = "{GITHUB}"',
        f'DATABASE_URL = "postgres://admin:{DB_PASSWORD}@db.internal:5432/app"',
        f'api_key = "{API_KEY}"',
        'password = "changeme"',
        'token = os.environ["TOKEN"]',
        'API_KEY = "YOUR_API_KEY_HERE"',
        'password_field = "user-password-input"',
        'SECRET_PATTERN = r"[A-Za-z0-9]{32}"',
        'EXAMPLE_KEY = "AKIAIOSFODNN7EXAMPLE"',
        'token_env = "GITHUB_TOKEN"',
        'URL = f"postgres://admin:{password}@db.internal/app"',
        'URL2 = "redis://:${REDIS_PASSWORD}@cache:6379"',
    ])
    found = run(tmp_path, {"config.py": code}, "security/hardcoded-secret")
    assert lines(found) == [1, 2, 3, 4]
    severities = {f.line_start: f.severity for f in found}
    assert severities[1] is Severity.CRITICAL and severities[4] is Severity.HIGH
    for f in found:
        for secret in (AWS, GITHUB, DB_PASSWORD, API_KEY):
            assert secret not in f.evidence


def test_generic_secrets_skipped_in_tests_and_docs(tmp_path):
    files = {
        "tests/test_api.py": f'api_key = "{API_KEY}"\n',
        "README.md": f'api_key = "{API_KEY}"\n',
    }
    assert run(tmp_path, files, "security/hardcoded-secret") == []


def test_committed_env_file(tmp_path):
    files = {
        ".env": f"DEBUG=true\nDB_PASSWORD={DB_PASSWORD}\n",
        ".env.example": "DB_PASSWORD=changeme\n",
    }
    found = run(tmp_path, files, "security/committed-env-file")
    assert [(f.file, f.line_start) for f in found] == [(".env", 2)]  # first real value, not DEBUG=true
    assert DB_PASSWORD not in found[0].evidence


# --- loose-ends/debug-statement --------------------------------------------------

def test_debug_statements(tmp_path):
    files = {
        "src/app.js": """
            function save(user) {
              console.log("saving", user);
              debugger;
              return db.put(user);
            }
            const msg = "console.log( is only text here";
            // console.log("commented out")
        """,
        "src/worker.py": "def run():\n    breakpoint()\n",
        "scripts/build.js": 'console.log("building");\n',
        "src/app.test.js": 'console.log("in a test");\n',
    }
    found = run(tmp_path, files, "loose-ends/debug-statement")
    assert [(f.file, f.line_start) for f in found] == [
        ("src/app.js", 3), ("src/worker.py", 2), ("src/app.js", 2),
    ]


# --- loose-ends/stub-implementation ----------------------------------------------

def test_python_stubs_skip_abstract_methods(tmp_path):
    code = '''
        from abc import ABC, abstractmethod


        class PaymentGateway(ABC):
            @abstractmethod
            def charge(self, amount):
                raise NotImplementedError


        class StripeGateway(PaymentGateway):
            def charge(self, amount) -> Receipt:
                """Charge the card."""
                pass

            def refund(self, amount):
                raise NotImplementedError("refunds")

            def status(self):
                return "ok"

            def __init__(self):
                pass

            def close(self) -> None:
                pass


        def sync_inventory():
            ...
    '''
    found = run(tmp_path, {"gateway.py": code}, "loose-ends/stub-implementation")
    assert {f.line_start: f.severity for f in found} == {
        11: Severity.MEDIUM,  # promises a Receipt, returns None
        15: Severity.LOW,  # NotImplementedError hook in a concrete class
        28: Severity.LOW,  # module-level function with no body
    }
    assert "promises `Receipt`" in found[0].message


def test_stub_idioms_across_languages(tmp_path):
    files = {
        "a.ts": 'export function sync() {\n  throw new Error("Not implemented");\n}\nconst s = "throw not implemented";\n',
        "b.rs": "fn parse() -> u8 {\n    todo!()\n}\n",
        "C.cs": "public int Run() {\n    throw new NotImplementedException();\n}\n",
        "d.go": 'func Pay() error {\n\tpanic("not implemented yet")\n}\n',
    }
    found = run(tmp_path, files, "loose-ends/stub-implementation")
    assert sorted((f.file, f.line_start) for f in found) == [("C.cs", 2), ("a.ts", 2), ("b.rs", 2), ("d.go", 2)]


# --- loose-ends/swallowed-error ---------------------------------------------------

def test_swallowed_errors_python(tmp_path):
    code = """
        def load(path):
            try:
                return open(path).read()
            except Exception:
                pass
            try:
                import yaml
            except ImportError:
                pass
            try:
                parse()
            except ValueError:
                # value may be missing; default below
                pass
            try:
                save()
            except:
                pass
            try:
                close()
            except OSError:
                log.warning("close failed")
    """
    found = run(tmp_path, {"io.py": code}, "loose-ends/swallowed-error")
    assert lines(found) == [4, 17]
    assert {f.line_start: f.severity for f in found}[17] is Severity.HIGH


def test_swallowed_errors_braces_and_go(tmp_path):
    files = {
        "a.js": """
            try { risky(); } catch (e) {}
            try {
              risky();
            } catch (err) {
            }
            fetchData().catch(() => {});
            try { a(); } catch (e) { // optional feature, safe to ignore
            }
            try { b(); } catch (e) { log(e); }
        """,
        "b.go": """
            if err != nil {
            }
            if err := run(); err != nil {
                return err
            }
        """,
    }
    found = run(tmp_path, files, "loose-ends/swallowed-error")
    assert sorted((f.file, f.line_start) for f in found) == [("a.js", 1), ("a.js", 4), ("a.js", 6), ("b.go", 1)]


# --- slop/placeholder-comment -----------------------------------------------------

def test_placeholder_comments(tmp_path):
    files = {
        "handler.ts": """
            export function handle(req) {
              // ... rest of the code
              // In a real app you would validate the token here
              // For now, just return a fixed user
              // set placeholder text on the input
              return { id: 1 };
            }
        """,
        "doc.py": '''
            def cached():
                """Return the value.

                In a real app you would cache this.
                """
                return 1
        ''',
    }
    found = run(tmp_path, files, "slop/placeholder-comment")
    assert [(f.file, f.line_start, f.severity) for f in found] == [
        ("handler.ts", 2, Severity.HIGH),
        ("handler.ts", 3, Severity.MEDIUM),
        ("handler.ts", 4, Severity.MEDIUM),
    ]


# --- loose-ends/mock-data ---------------------------------------------------------

def test_mock_data_in_production_paths(tmp_path):
    files = {
        "src/users.ts": """
            const mockUsers = [{ name: "Ann" }];
            const contact = "support@example.com";
            const isMock = a === b;
            if (mockUsers == other) {}
            const bio = "Lorem ipsum dolor sit amet";
        """,
        "src/__mocks__/api.ts": "const mockUsers = [];\n",
        "src/mockServer.ts": "const mockUsers = [];\n",
        "tests/users.test.ts": "const mockUsers = [];\n",
    }
    found = run(tmp_path, files, "loose-ends/mock-data")
    assert [(f.file, f.line_start) for f in found] == [("src/users.ts", 1), ("src/users.ts", 2), ("src/users.ts", 5)]


# --- slop/duplicate-block -----------------------------------------------------------

BLOCK = """
def {name}(order):
    total = 0
    for item in order.items:
        price = item.unit_price * item.quantity
        if item.discount_code:
            price = price * (1 - lookup_discount(item.discount_code) / {pct})
        total += price
    tax = total * {tax}
    return round(total + tax, 2)
"""


def test_duplicate_blocks_across_files(tmp_path):
    files = {
        "billing/invoice.py": BLOCK.format(name="invoice_total", pct=100, tax=0.2),
        "billing/quote.py": "import math\n\n" + BLOCK.format(name="quote_total", pct=100, tax=0.25),
        "billing/other.py": "def other():\n    return 1\n",
    }
    found = run(tmp_path, files, "slop/duplicate-block")
    assert len(found) == 1
    f = found[0]
    assert f.file == "billing/quote.py"
    assert "billing/invoice.py" in f.message


# --- multi-line string awareness --------------------------------------------------

def test_markers_inside_multiline_strings_are_ignored(tmp_path):
    files = {
        "a.py": '"""Module notes.\n\nSupports C# and Kotlin TODO() idioms.\n"""\nx = 1  # TODO: real\n',
        "b.js": "const tpl = `\n  // TODO not a comment\n`;\n// TODO: real\n",
    }
    found = run(tmp_path, files, "loose-ends/marker-comment")
    assert sorted((f.file, f.line_start) for f in found) == [("a.py", 5), ("b.js", 4)]
