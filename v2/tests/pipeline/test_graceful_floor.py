from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.setup.localize import localize


def test_graceful_when_no_capability_data_and_no_error():
    # The bare-timeout contract: the caller (execute default-backstop) passes
    # error=None, so the floor is the warm slot-free message — no blank fields.
    out = synthesize_floor(goal="i liked your message style",
                           error=None, attempts=[], partial=None)
    assert out
    assert "capability that failed" not in out.lower()
    assert "budget cap reached" not in out.lower()
    assert "technical detail" not in out.lower()


def test_error_present_carries_detail_not_graceful():
    # A genuine failure WITH a real error (e.g. a provider crash) must keep the
    # error-bearing floor — the honest technical detail is never swallowed by the
    # graceful message, even when no capability/attempts/partial are available.
    out = synthesize_floor(goal="do a thing",
                           error="provider down (tool loop)",
                           attempts=[], partial=None)
    assert "provider down (tool loop)" in out


def test_capability_template_kept_when_capability_present():
    out = synthesize_floor(goal="send mail", error="smtp blocked",
                           attempts=["send_email"], partial=None, failed_capability="send_email")
    assert "send_email" in out


def test_graceful_localize_key_exists_all_langs():
    for lang in ("en", "de", "fr", "es"):
        msg = localize("self_heal_floor_graceful", lang)
        assert msg and "{" not in msg


def test_graceful_when_only_goal_present():
    out = synthesize_floor(goal="hi there", error=None, attempts=None, partial=None)
    assert "capability that failed" not in out.lower()
