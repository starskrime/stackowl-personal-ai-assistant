"""Advertise the install's `.local` host name via the OS's own mDNS responder tool (AD-13).

Linux uses `avahi-publish-service -a` (address-publish mode); macOS uses
`dns-sd -P` (proxy-record mode). Neither tool is a dependency of the kit —
this module only shells out to whichever one the running OS already has
(Avahi ships with most Linux desktop/server distros; `dns-sd` ships with
macOS). If the tool is missing or fails to start, the exact command is
printed so the owner can run it (or the platform's equivalent) by hand, per
the story's "never crash on missing tooling" rule.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("bridge_spike.mdns")

SERVICE_TYPE = "_https._tcp"


class UnsupportedPlatformError(RuntimeError):
    """Raised only by `build_command` when the OS has no known mDNS tool mapping."""


def build_command(install_name: str, port: int, address: str, system: str | None = None) -> list[str]:
    """The exact subprocess argv that publishes `install_name` -> `address`
    as a resolvable mDNS address record on `system` (default: detected OS).

    This deliberately uses each tool's ADDRESS-publishing mode, not its
    default bare service-registration mode. A bare `avahi-publish-service
    <name> <type> <port>` or `dns-sd -R <name> <type> <domain> <port>`
    registers a *discoverable service* under the host's own existing mDNS
    hostname — it does NOT make `<install_name>` itself resolve to an IP.
    Verified directly on a Linux build host: after publishing that way,
    `avahi-resolve -n4 <install_name>` returns nothing, and `avahi-browse`
    shows the service's `hostname` field as the machine's real hostname, not
    the install name.
    """
    system = system or platform.system()
    if system == "Linux":
        # -a: publish an address record for `install_name`, not a service.
        # -R: skip the reverse (PTR) record -- without it, avahi-daemon
        # reports "Local name collision", because the existing PTR for this
        # host's own real hostname already claims this same IP (verified
        # empirically on this host).
        return ["avahi-publish-service", "-a", "-R", install_name, address]
    if system == "Darwin":
        # -P: register a "proxy" record set (SRV + an address record for the
        # given Host/IP), the documented dns-sd mechanism for advertising a
        # custom host name. Plain `-R` can only advertise a service under the
        # machine's own existing hostname and has no way to target a custom
        # one. (Not verified on real macOS hardware -- based on dns-sd's
        # documented -P proxy-record option; Story 1.6 covers real-device
        # verification.)
        return ["dns-sd", "-P", install_name, SERVICE_TYPE, "local", str(port), install_name, address]
    raise UnsupportedPlatformError(f"No known mDNS publisher for platform {system!r}")


@dataclass
class Advertisement:
    """A handle on the background mDNS-publishing subprocess (if one started)."""

    command: list[str]
    process: subprocess.Popen[bytes] | None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def start_advertising(install_name: str, port: int, address: str | None, system: str | None = None) -> Advertisement:
    """Start advertising `<install_name>` -> `address` via the OS's mDNS tool.

    Never raises: if `address` could not be discovered, or the mDNS tool is
    absent (`FileNotFoundError`) or fails to launch (`OSError`), this logs
    and prints the exact remedy instead, and returns an `Advertisement` with
    `process=None` so callers can proceed without mDNS rather than crashing
    the kit.
    """
    if address is None:
        message = (
            f"Could not discover a local IPv4 address to advertise {install_name!r} on. "
            "Find this host's LAN IPv4 address yourself, then run (Linux example):\n"
            f"  avahi-publish-service -a -R {install_name} <host-ip>"
        )
        logger.warning(message)
        print(message)
        return Advertisement(command=[], process=None)

    command = build_command(install_name, port, address, system=system)
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError) as exc:
        message = (
            f"Could not run the mDNS publisher automatically ({exc}). "
            f"Run this command yourself to advertise {install_name!r}:\n  {' '.join(command)}"
        )
        logger.warning(message)
        print(message)
        return Advertisement(command=command, process=None)
    return Advertisement(command=command, process=process)
