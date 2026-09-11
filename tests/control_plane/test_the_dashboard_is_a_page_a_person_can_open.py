"""A05.1 said "there is no surface a customer can see" and then shipped curl.

MEASURED 2026-09-11, after the operator asked in his own words: *"What happened
with agentic os dashboard? Where we are in progress?"*

  * `src/stackowl/control_plane/` contained **zero** `text/html`, `add_static`
    or template references.
  * `map_check.py "a web page a customer opens in a browser to see the
    platform"` returned **eleven** matching items and **not one of them builds a
    page**. Every A05 item says *make X reachable*; none says *draw it*.
  * A05.1's own gap — "there is no surface through which a customer can see or
    steer the platform. Everything is a terminal command." — sits on an item
    whose every stage reads `done`.

So the record called the surface finished while what existed was a JSON API
behind a bearer token: still a terminal command, typed into `curl` instead of
into `stackowl`. That is the gap-statement failure DEBT-307 found, wearing its
other face — there the gap overstated an ABSENCE, here the completion overstated
a PRESENCE, and neither had anything checking it.

THE PAGE IS THE ONE ROUTE WITH NO CREDENTIAL, AND THE REASON IS MECHANICAL: a
browser cannot put an `Authorization` header on a top-level navigation. A page
served only to an authenticated caller could never be opened by the person it
exists for. It is safe because it carries NOTHING — a module constant with no
interpolation, reading no instance attribute — and both halves are asserted here
and in `test_every_route_handler_calls_authenticate`, which now allows an
unguarded route only when it touches no state at all.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest

from stackowl.control_plane.page import INDEX_HTML
from stackowl.control_plane.server import ControlPlaneServer


class _Req:
    def __init__(self, **headers: str) -> None:
        self.headers = dict(headers)


def _server() -> ControlPlaneServer:
    class _Cfg:
        bind_address = "127.0.0.1"
        port = 8787

    class _Settings:
        control_plane = _Cfg()

    srv = ControlPlaneServer(_Settings())  # type: ignore[arg-type]
    srv._token = "t0ken-for-tests-only"  # noqa: SLF001
    return srv


class TestThePageIsServed:
    @pytest.mark.tripwire
    def test_the_route_is_registered(self) -> None:
        """Built-but-not-wired, on the item whose whole point is reachability."""
        src = textwrap.dedent(inspect.getsource(ControlPlaneServer.run))
        paths = [
            n.args[0].value
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add_get"
            and isinstance(n.args[0], ast.Constant)
        ]
        assert "/" in paths, f"the page is not served at the root: {paths}"

    @pytest.mark.tripwire
    async def test_it_opens_with_NO_credential(self) -> None:
        """The property the whole design turns on. If this ever needs a token,
        the dashboard is unreachable by a browser and we are back to curl."""
        res = await _server()._handle_index(_Req())  # noqa: SLF001

        assert res.status == 200
        assert res.content_type == "text/html"
        assert "<title>StackOwl control plane</title>" in res.text

    @pytest.mark.tripwire
    async def test_it_renders_both_existing_surfaces_and_invents_no_endpoint(self) -> None:
        """The page must read what is ALREADY built, not imply routes that do not
        exist. A dashboard calling a 404 shows an error and reads as broken
        platform rather than as absent feature."""
        res = await _server()._handle_index(_Req())  # noqa: SLF001
        page = res.text

        for path in ("/api/v1/health", "/api/v1/schedules"):
            assert path in page, f"the page never fetches {path}"

        # Every `/api/...` string the page mentions must be a route the server
        # registers. Derived from the server, not a list written here.
        registered = {
            n.args[0].value
            for n in ast.walk(ast.parse(textwrap.dedent(
                inspect.getsource(ControlPlaneServer.run))))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("add_")
            and n.args and isinstance(n.args[0], ast.Constant)
        }
        for cited in set(re.findall(r'"(/api/[a-z0-9/_-]+)"', page)):
            assert cited in registered, (
                f"the page fetches {cited!r}, which no route serves — it would "
                f"render an error for a feature that was never built. "
                f"Registered: {sorted(registered)}"
            )


class TestThePageCannotCarryPlatformData:
    """The entire security argument for serving it unauthenticated."""

    @pytest.mark.tripwire
    def test_the_markup_is_a_constant_with_no_interpolation(self) -> None:
        """An f-string here would turn a public route into a data leak, and it
        would look like a convenience at the time."""
        from stackowl.control_plane import page as page_mod

        tree = ast.parse(inspect.getsource(page_mod))
        assigns = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign | ast.Assign)
        ]
        html = [
            n for n in assigns
            if "INDEX_HTML" in ast.unparse(n.target if isinstance(n, ast.AnnAssign)
                                           else n.targets[0])
        ]
        assert len(html) == 1, "INDEX_HTML is assigned somewhere other than once"
        value = html[0].value
        assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
            "INDEX_HTML is no longer a plain string constant — an f-string or a "
            "concatenation can carry platform state onto an unauthenticated route"
        )

    @pytest.mark.tripwire
    def test_the_handler_reads_no_instance_state(self) -> None:
        """The second half. A constant cannot leak; a handler that reads
        `self._settings` to decorate it can."""
        src = textwrap.dedent(inspect.getsource(ControlPlaneServer._handle_index))
        touched = sorted({
            n.attr for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name) and n.value.id == "self"
        })
        assert not touched, (
            f"the unauthenticated page handler reads {touched} off the server"
        )

    @pytest.mark.tripwire
    def test_the_token_is_not_put_in_a_cookie(self) -> None:
        """A cookie is sent automatically on every request to this origin, which
        is how a dashboard acquires a CSRF problem it did not need. The page
        holds the token in sessionStorage and attaches it explicitly."""
        assert "document.cookie" not in INDEX_HTML
        assert "sessionStorage" in INDEX_HTML
        assert 'Authorization": "Bearer ' in INDEX_HTML, (
            "the page no longer attaches the credential explicitly"
        )

    @pytest.mark.tripwire
    def test_untrusted_strings_are_never_written_as_markup(self) -> None:
        """`last_error` is whatever a failing job stored. It reaches the page as
        data and must reach the DOM as text.

        IT ASKS FOR AN ASSIGNMENT, NOT FOR THE WORD, and the first draft did the
        opposite: `assert "innerHTML" not in INDEX_HTML` failed on the page's own
        COMMENT — `// textContent everywhere, never innerHTML` — which is the
        line promising not to do the thing. That is the FIFTH time in one day a
        guard of mine matched prose instead of code (`hard_stop` in a docstring
        recording its deletion, `config` in an import, `minted` in a comment,
        `store_secret` in an explanation). A comment can say the word; only an
        assignment can write markup, so the guard asks about the assignment.
        """
        for writer in (r"\.innerHTML\s*=", r"\.outerHTML\s*=",
                       r"insertAdjacentHTML\s*\(", r"document\.write\s*\("):
            assert not re.search(writer, INDEX_HTML), (
                f"the page writes markup via {writer!r} — a job's stored error "
                "string would be parsed as HTML"
            )
        assert "textContent" in INDEX_HTML


class TestItWorksOnAMachineWithNoEgress:
    @pytest.mark.tripwire
    def test_nothing_is_fetched_from_another_host(self) -> None:
        """Self-hosted is a requirement, not a preference: a page that pulls a
        framework from someone else's server is blank on an air-gapped box, and
        the operator's own machine is the first one it has to work on."""
        import re

        for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', INDEX_HTML):
            raise AssertionError(f"the page loads {url} from off-host")
        assert "<style>" in INDEX_HTML and "<script>" in INDEX_HTML, (
            "the page no longer carries its own CSS and JS inline"
        )


class TestThePageActuallyParses:
    """The gap every other test here shares, and it is the one this repo names.

    Everything above asks whether a STRING is present. A page with one unbalanced
    brace contains every one of those strings and renders BLANK — a fixture that
    cannot show the bug proves nothing, so these two run a real parser over it.
    """

    @pytest.mark.tripwire
    def test_the_script_is_valid_javascript(self) -> None:
        """`node --check` parses without executing. The page ships as one
        constant with no build step, so nothing else would ever catch a syntax
        error before a person opened it and saw nothing."""
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if node is None:  # pragma: no cover — present on this box and in CI
            pytest.skip("no node on PATH; the JS cannot be parsed here")

        match = re.search(r"<script>(.*?)</script>", INDEX_HTML, re.S)
        assert match, "the page has no inline script — it would render nothing"

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(match.group(1))
            path = fh.name
        result = subprocess.run(
            [node, "--check", path], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, (
            f"the page's script is not valid JavaScript:\n{result.stderr[:800]}"
        )

    @pytest.mark.tripwire
    def test_every_element_is_closed(self) -> None:
        """An unclosed `<table>` swallows everything after it. The tests above
        would all still pass."""
        from html.parser import HTMLParser

        void = {"meta", "link", "br", "hr", "img", "input", "source"}

        class _Check(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.stack: list[str] = []
                self.bad: list[str] = []

            def handle_starttag(self, tag: str, attrs: object) -> None:
                if tag not in void:
                    self.stack.append(tag)

            def handle_endtag(self, tag: str) -> None:
                if not self.stack:
                    self.bad.append(f"</{tag}> with nothing open")
                elif self.stack[-1] != tag:
                    self.bad.append(f"</{tag}> closes <{self.stack[-1]}>")
                else:
                    self.stack.pop()

        parser = _Check()
        parser.feed(INDEX_HTML)
        assert not parser.bad, f"mismatched tags: {parser.bad}"
        assert not parser.stack, f"never closed: {parser.stack}"

    @pytest.mark.tripwire
    def test_the_markup_carries_no_homoglyphs(self) -> None:
        """A CYRILLIC "be" reached a hex colour in the first draft — invisible to
        review, silently wrong, and caught only by listing every non-ASCII
        codepoint. Anything generated deserves that sweep."""
        intended = {"·", "—", "…"}
        found = {c for c in INDEX_HTML if ord(c) > 127}
        assert found <= intended, (
            f"unexpected non-ASCII in the page: {sorted(found - intended)!r} — "
            "check for a homoglyph before adding it to the intended set"
        )


@pytest.mark.tripwire
def test_every_api_route_is_rendered_somewhere_on_the_page() -> None:
    """THE RULE THIS ITEM EXISTS TO MAKE STRUCTURAL, AND ITS CAUSE.

    A05.1 shipped an endpoint, its record said the surface was done, and nothing
    rendered. Six A05 surfaces remain — agents, config, memory, live activity,
    interactions, skills — and each will land the same way unless something
    connects "a route exists" to "a person can see it".

    Prose cannot: `A05.9.md` says each surface adds its table here IN THE SAME
    CHANGE, and a rule that has to be remembered is the one that gets skipped.
    This is the pairing test — every registered `/api/` path must appear in the
    page, and `test_it_renders_both_existing_surfaces_and_invents_no_endpoint`
    asserts the other direction. Together they are a bijection: the dashboard
    shows everything the door serves, and fetches nothing it does not.

    It is NOT vacuous today (two API routes, both rendered) and it cannot rot
    into a floor, because both sides are derived from the route table rather than
    counted.
    """
    registered = {
        n.args[0].value
        for n in ast.walk(ast.parse(textwrap.dedent(
            inspect.getsource(ControlPlaneServer.run))))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr.startswith("add_")
        and n.args and isinstance(n.args[0], ast.Constant)
        and str(n.args[0].value).startswith("/api/")
    }
    assert registered, "no API routes found — this guard has gone blind"

    missing = sorted(path for path in registered if path not in INDEX_HTML)
    assert not missing, (
        f"{missing} is served by the control plane and appears nowhere on the "
        "page. An endpoint nobody can see is the state A05.1 shipped while its "
        "record said the surface was done — add its table in this change"
    )
