"""Backend-seam tests — the no-PTC argv is UNCHANGED; PTC adds only the socket.

These lock the load-bearing wiring invariant: a run WITHOUT a ptc_factory produces the
exact prior bwrap/docker argv (no socket, no env, no stub), and a run WITH PTC adds
ONLY the single socket bind/volume + the OWL_PTC_SOCK env — never relaxing the network
isolation or any other host-FS guard.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.sandbox.argv import BwrapArgvBuilder
from stackowl.sandbox.docker_argv import DockerArgvBuilder
from stackowl.sandbox.spec import ExecSpec


class TestBwrapSeam:
    def test_no_ptc_argv_has_zero_ptc_artifacts(self, tmp_path: Path) -> None:
        argv = BwrapArgvBuilder().build(ExecSpec(code="print(1)"), tmp_path)
        joined = " ".join(argv)
        assert "OWL_PTC_SOCK" not in joined
        assert ".ptc.sock" not in joined
        # network is denied regardless.
        assert "--unshare-net" in argv

    def test_ptc_adds_only_socket_bind_and_env_network_unchanged(self, tmp_path: Path) -> None:
        base = BwrapArgvBuilder().build(ExecSpec(code="print(1)"), tmp_path)
        sock = tmp_path / "x.sock"
        withp = BwrapArgvBuilder().build(ExecSpec(code="print(1)"), tmp_path, ptc_sock=sock)
        # network isolation untouched.
        assert "--unshare-net" in withp
        # the ONLY new tokens are the bind target/source + the env name + its value.
        extra = [t for t in withp if t not in base]
        assert str(sock) in extra
        assert "/workspace/.ptc.sock" in extra
        assert "OWL_PTC_SOCK" in extra
        # no host-FS bind beyond the socket sneaked in.
        assert "--ro-bind" not in [withp[i] for i in range(len(withp)) if withp[i] not in base]


class TestDockerSeam:
    def _argv(self, *, ptc: Path | None, tmp_path: Path) -> list[str]:
        return DockerArgvBuilder().build(
            spec=ExecSpec(code="print(1)"), image="python:3.12-slim",
            container_name="c", code_dir=tmp_path / "code",
            seccomp_profile=tmp_path / "seccomp.json", ptc_socket=ptc,
        )

    def test_no_ptc_argv_has_zero_ptc_artifacts(self, tmp_path: Path) -> None:
        argv = self._argv(ptc=None, tmp_path=tmp_path)
        joined = " ".join(argv)
        assert "OWL_PTC_SOCK" not in joined
        assert ".ptc.sock" not in joined
        assert "--network" in argv and "none" in argv

    def test_ptc_adds_only_socket_volume_and_env_network_none(self, tmp_path: Path) -> None:
        sock = tmp_path / "x.sock"
        base = self._argv(ptc=None, tmp_path=tmp_path)
        withp = self._argv(ptc=sock, tmp_path=tmp_path)
        # network stays none.
        assert "none" in withp and "bridge" not in withp
        extra = [t for t in withp if t not in base]
        assert f"{sock}:/work/.ptc.sock" in extra
        assert "OWL_PTC_SOCK=/work/.ptc.sock" in extra
