from pathlib import Path

import pytest

from app.parser import TraceParseError, detect_language, is_library_path, normalize_trace, parse_trace

TRACES = Path(__file__).parent / "traces"


def load(name: str) -> str:
    return (TRACES / name).read_text(encoding="utf-8")


def user_funcs(parsed):
    return [(Path(f.file).name, f.line, f.function) for f in parsed.user_frames]


# --------------------------------------------------------------------------- demo traces


def test_demo_bug1_keyerror():
    p = parse_trace(load("demo_bug1_keyerror.txt"))
    assert p.language == "python"
    assert p.error.type == "KeyError"
    assert p.error.message == "'email'"
    assert user_funcs(p) == [
        ("profile.py", 24, "get_profile"),
        ("__main__.py", 14, "main"),
        ("__main__.py", 25, "<module>"),
    ]
    assert p.frames[0].source == '"email": mask_email(user_data["email"]),'
    # <frozen runpy> frames count as library code
    assert [f.is_user_code for f in p.frames][-2:] == [False, False]


def test_demo_bug2_js_null():
    p = parse_trace(load("demo_bug2_null_name.txt"))
    assert p.language == "javascript"
    assert p.error.type == "TypeError"
    assert p.error.message == "Cannot read properties of null (reading 'name')"
    assert user_funcs(p) == [
        ("greet.js", 6, "greeting"),
        ("greet.js", 12, "main"),
        ("greet.js", 16, "Object.<anonymous>"),
    ]
    assert p.frames[0].column == 25
    assert all(not f.is_user_code for f in p.frames[3:])
    assert p.warnings == []


def test_demo_bug3_valueerror_library_top_frame():
    p = parse_trace(load("demo_bug3_date_format.txt"))
    assert p.error.type == "ValueError"
    assert p.error.message.startswith("unconverted data remains")
    # The two innermost frames are in _strptime (stdlib); the first user frame is parse_order_date
    assert not p.frames[0].is_user_code and not p.frames[1].is_user_code
    assert user_funcs(p)[:3] == [
        ("reports.py", 10, "parse_order_date"),
        ("reports.py", 16, "daily_totals"),
        ("reports.py", 24, "build_report"),
    ]


# --------------------------------------------------------------------------- real-world traces


def test_python_chained_during_handling():
    p = parse_trace(load("py_chained_during_handling.txt"))
    assert p.error.type == "requests.exceptions.JSONDecodeError"
    assert p.error.message == "Expecting value: line 1 column 1 (char 0)"
    assert [c.type for c in p.causes] == ["json.decoder.JSONDecodeError"]
    assert user_funcs(p) == [("client.py", 31, "fetch_invoices"), ("sync.py", 58, "sync_invoices")]


def test_python_direct_cause():
    p = parse_trace(load("py_direct_cause.txt"))
    assert p.error.type == "config.errors.ConfigError"
    assert "settings.prod.toml" in p.error.message
    assert p.causes[0].type == "FileNotFoundError"
    assert user_funcs(p) == [("loader.py", 25, "load_config"), ("main.py", 9, "<module>")]
    assert p.frames[1].source == 'settings = load_config(os.environ["APP_ENV"])'


def test_python_django_library_frames():
    p = parse_trace(load("py_django_attributeerror.txt"))
    assert p.error.type == "AttributeError"
    assert p.error.message == "'NoneType' object has no attribute 'courier'"
    assert user_funcs(p) == [("services.py", 140, "build_order_context"), ("views.py", 88, "order_detail")]
    assert len(p.frames) == 4


def test_python_windows_paths_crlf():
    p = parse_trace(load("py_windows_paths.txt"))
    assert p.error.type == "TypeError"
    assert p.frames[0].file.endswith("decimal.py") and not p.frames[0].is_user_code
    assert [(f.file.split("\\")[-1], f.line) for f in p.user_frames] == [("report.py", 17), ("main.py", 41)]


def test_python_multiline_message():
    p = parse_trace(load("py_multiline_message.txt"))
    assert p.error.type == "pydantic_core._pydantic_core.ValidationError"
    assert p.error.message.splitlines()[0] == "1 validation error for UserIn"
    assert "Field required" in p.error.message
    assert user_funcs(p) == [("users.py", 33, "create_user")]


def test_js_async_esm_file_urls():
    p = parse_trace(load("js_async_esm.txt"))
    assert p.error.type == "TypeError"
    assert p.error.message == "Cannot read properties of undefined (reading 'filter')"
    assert p.frames[0].file == "/Users/sam/code/notifier/src/jobs/digest.mjs"
    assert p.frames[0].function == "buildDigest"
    # "at async Promise.all (index 2)" has no location, so it is skipped
    assert [(Path(f.file).name, f.line, f.function) for f in p.frames] == [
        ("digest.mjs", 27, "buildDigest"),
        ("index.mjs", 14, "runJobs"),
        ("main.mjs", 8, None),
    ]


def test_js_express_node_modules():
    p = parse_trace(load("js_express_reference.txt"))
    assert p.error.type == "ReferenceError"
    assert p.error.message == "currency is not defined"
    assert user_funcs(p) == [
        ("format.js", 12, "formatPrice"),
        ("products.js", 45, None),
        ("products.js", 44, "listProducts"),
    ]


def test_js_minified_warning():
    p = parse_trace(load("js_minified_bundle.txt"))
    assert p.error.type == "TypeError"
    assert any("minified" in w for w in p.warnings)
    assert p.user_frames == []


# --------------------------------------------------------------------------- helpers


def test_unrecognized_trace_raises():
    with pytest.raises(TraceParseError):
        parse_trace("something went wrong")
    with pytest.raises(TraceParseError):
        parse_trace("   \n  ")


def test_detect_language():
    assert detect_language(load("demo_bug1_keyerror.txt")) == "python"
    assert detect_language(load("demo_bug2_null_name.txt")) == "javascript"
    assert detect_language("hello") is None


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/srv/app/venv/lib/python3.11/site-packages/requests/models.py", True),
        ("<frozen runpy>", True),
        ("node:internal/modules/cjs/loader", True),
        ("/app/node_modules/express/lib/router/layer.js", True),
        ("C:\\Users\\dev\\AppData\\Local\\Programs\\Python\\Python312\\Lib\\decimal.py", True),
        ("/home/deploy/project/app/user.py", False),
        ("C:\\Users\\dev\\projects\\inventory\\app\\main.py", False),
    ],
)
def test_is_library_path(path, expected):
    assert is_library_path(path) is expected


def test_normalize_trace_strips_addresses_and_whitespace():
    a = "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\n    ^^^^\nError: at 0x7f3a2b  obj\n"
    b = "Traceback (most recent call last):\r\n    File \"x.py\",   line 1, in f\r\nError: at 0xdeadbeef obj"
    assert normalize_trace(a) == normalize_trace(b)
