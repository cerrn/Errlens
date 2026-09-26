from app.confidence import SignalInput, blend, compute_signals, error_line_compatible, reasons
from app.models import CodeContext, ErrorInfo, Frame, SuspectCommit


def err(t, m, lang="python"):
    return ErrorInfo(type=t, message=m, language=lang)


def test_type_match_rules():
    assert error_line_compatible(err("KeyError", "'email'"), 'x = d["email"]')[0] is True
    assert error_line_compatible(err("KeyError", "'email'"), "x = d.email")[0] is False
    js = err("TypeError", "Cannot read properties of null (reading 'name')", "javascript")
    assert error_line_compatible(js, "return `Hi ${user.name}`;")[0] is True
    assert error_line_compatible(js, "return user;")[0] is False
    assert error_line_compatible(err("AttributeError", "'NoneType' object has no attribute 'courier'"),
                                 "order.shipment.courier.name")[0] is True
    assert error_line_compatible(err("ReferenceError", "currency is not defined", "javascript"),
                                 "return fmt(price, currency)")[0] is True
    assert error_line_compatible(err("RuntimeError", "boom"), "x()") is None


def _inp(**kw):
    base = dict(
        error=err("KeyError", "'email'"),
        frames=[Frame(file="a.py", raw_file="/x/a.py", line=3, is_user_code=True, resolved=True)],
        contexts=[CodeContext(file="a.py", start_line=1, end_line=5, highlight_line=3, snippet="a\nb\nd['email']\nc\nd")],
        suspect=SuspectCommit(sha="abcdef1234", message="m", author="a", date="2026-09-24T10:00:00+00:00", url=""),
        suspect_recent=True,
        fix_valid=True,
        line_mismatch=False,
    )
    base.update(kw)
    return SignalInput(**base)


def test_all_signals_pass():
    sig = compute_signals(_inp())
    assert [s.name for s in sig] == ["user_frame", "recent_change", "type_match", "fix_valid"]
    assert all(s.score == 1 for s in sig)
    final, signal_score = blend(0.8, sig)
    assert signal_score == 1.0 and final == round(0.6 * 0.8 + 0.4, 2)


def test_failing_signals_lower_score_and_explain():
    sig = compute_signals(_inp(suspect=None, suspect_recent=False, fix_valid=False, line_mismatch=True))
    names = {s.name: s.score for s in sig}
    assert names["recent_change"] == 0 and names["fix_valid"] == 0 and names["line_match"] == 0
    final, _ = blend(0.8, sig)
    assert final < 0.7
    rs = reasons(0.8, sig)
    assert rs[0] == "The model rated its own confidence at 80%"
    assert any("do not match" in r for r in rs)


def test_library_top_frame_partial_credit():
    frames = [
        Frame(file="/usr/lib/python3.11/_strptime.py", raw_file="/usr/lib/python3.11/_strptime.py", line=1,
              is_user_code=False),
        Frame(file="a.py", raw_file="/x/a.py", line=3, is_user_code=True, resolved=True),
    ]
    sig = compute_signals(_inp(frames=frames))
    assert sig[0].score == 0.75
