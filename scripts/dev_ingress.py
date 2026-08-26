#!/usr/bin/env python3
"""Send a message to the RUNNING platform as if it had arrived from a channel.

    uv run python scripts/dev_ingress.py "what are you working on right now?"
    uv run python scripts/dev_ingress.py --session 72055773 --chat 72055773 "hello"

THE ANSWER DOES NOT COME BACK HERE. It goes wherever a real message's answer
goes — the actual chat. That is deliberate: this platform's rule is that a task
is complete when its outcome reached its DESTINATION, so an injected question
whose reply landed in a script would prove nothing about delivery. What comes
back on this socket is an acknowledgement and a trace_id, which is what you
follow in the log:

    grep <trace_id> ~/.stackowl/logs/stackowl.jsonl

NEVER USE THE CORE SOCKET FOR THIS. The gateway BINDS ~/.stackowl/runtime/
core.sock and the core dials in as a client, so anything else that connects is
taken for the core reattaching and displaces the live link. That is measured, not
theoretical — it cost three and a half minutes of delivery on 2026-08-25, and
this script exists so it never has to be repeated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stackowl.paths import StackowlHome  # noqa: E402

#: The operator's Telegram handle — the same value the adapter stamps as
#: session_key, and the shape ESC-17's identity fix depends on.
DEFAULT_SESSION = "72055773"


async def send(
    text: str, *, session_key: str, channel: str, chat_id: int | None
) -> int:
    path = StackowlHome.dev_ingress_socket()
    if not path.exists():
        print(f"no dev ingress at {path}", file=sys.stderr)
        print("the gateway starts it alongside the Telegram adapter — is it up?",
              file=sys.stderr)
        return 2
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        payload = {
            "channel": channel,
            "session_key": session_key,
            "text": text,
            "chat_id": chat_id,
            "is_direct": True,
        }
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    finally:
        writer.close()

    reply = json.loads(line.decode("utf-8"))
    if not reply.get("ok"):
        print(f"REFUSED: {reply.get('error')}", file=sys.stderr)
        return 1
    print(f"accepted  trace_id={reply['trace_id']}")
    print(f"the answer goes to the real {channel} chat; follow it with:")
    print(f"  grep {reply['trace_id']} ~/.stackowl/logs/stackowl.jsonl")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", help="the message, exactly as a person would type it")
    ap.add_argument("--session", default=DEFAULT_SESSION,
                    help="session_key: the RAW channel handle")
    ap.add_argument("--channel", default="telegram")
    ap.add_argument("--chat", type=int, default=int(DEFAULT_SESSION),
                    help="chat_id the reply is delivered to")
    ns = ap.parse_args()
    return asyncio.run(send(
        ns.text, session_key=ns.session, channel=ns.channel, chat_id=ns.chat
    ))


if __name__ == "__main__":
    sys.exit(main())
