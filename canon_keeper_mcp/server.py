"""Canon Keeper as an MCP server.

So that "I drink the potion and check the door for traps" becomes real changes
to real sheets, instead of the player typing them in.

The design decision that matters is what this is *not*. It is not a privileged
back door with its own rules -- it is a client, holding one login, and every
tool below turns into an ordinary message on the wire. A player's MCP session
can do exactly what that player could do by hand:

- ``say`` is a chat message.
- ``roll`` is rolled **on the host**; a result invented here would be ignored.
- ``update_my_character`` is a *request*. The host writes nothing on a client's
  say-so, so this returns "sent to your DM", never "done".

None of that is enforced here. It is what the host does with these messages,
which is why this file can be short and why a bug in it cannot corrupt a
campaign.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from canon_keeper_client import AgentSession
from canon_keeper_protocol import MessageType, encode

log = logging.getLogger("canonkeeper.mcp")


class CanonKeeperTools:
    """One connected session, exposed as MCP tools."""

    def __init__(self, session: AgentSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ reading

    def whats_happening(self) -> dict[str, Any]:
        table = self.session.table
        return {
            "campaign": table.campaign,
            "session": table.session,
            "you": table.me.label if table.me else "",
            "at_the_table": [m.label for m in table.members],
            "recent": table.recent[-15:],
            "autopilot": table.autopilot,
        }

    def who_and_where(self) -> list[dict[str, Any]]:
        """Everyone and everywhere this login has been told about.

        Deliberately the whole of what the host sent and nothing more. An entity
        the DM has not shared is not filtered out here -- it never arrived.
        """
        return [
            {
                "id": entity.get("id"),
                "name": entity.get("name"),
                "kind": entity.get("kind"),
                "summary": entity.get("summary", ""),
            }
            for entity in self.session.table.entities.values()
        ]

    def my_characters(self) -> list[dict[str, Any]]:
        table = self.session.table
        return [
            entity
            for entity in table.entities.values()
            if isinstance(entity.get("data"), dict) and "sheet" in entity["data"]
        ]

    # ------------------------------------------------------------------ acting

    async def say(self, text: str) -> str:
        socket = self.session._socket
        if socket is None:
            return "Not connected."
        await socket.send(encode(MessageType.CHAT, text=text))
        return f"Said: {text}"

    async def roll(self, notation: str) -> str:
        """Ask the host to roll. It rolls; we do not.

        A client that rolled its own dice and reported the number would be
        trusted by nobody at a real table, and is not trusted by the host
        either -- it ignores any result a client sends.
        """
        socket = self.session._socket
        if socket is None:
            return "Not connected."
        await socket.send(encode(MessageType.ROLL, notation=notation))
        return f"Asked the host to roll {notation}. The result appears in the chat."

    async def update_my_character(
        self, entity_id: int, changes: dict[str, Any]
    ) -> str:
        """Ask the DM for a change. This is a request, not a write.

        Everything a player changes is decided by their DM, hit points included.
        The honest return value is that it was sent.
        """
        socket = self.session._socket
        if socket is None:
            return "Not connected."
        await socket.send(
            encode(MessageType.EDIT, id=entity_id, changes=changes)
        )
        return (
            "Sent to your DM. They will approve or refuse it, and a refusal "
            "comes back with a reason."
        )


def build_server(session: AgentSession) -> MCPServer:
    tools = CanonKeeperTools(session)
    server = MCPServer(
        name="canon-keeper",
        instructions=(
            "Tools for one seat at a Dungeons & Dragons table running on Canon "
            "Keeper. You act as the person holding this login and have exactly "
            "their authority: you can say things, ask the host to roll, and "
            "request changes to their own characters. Requests go to the DM, "
            "who approves or refuses them -- never report a requested change as "
            "though it had been applied."
        ),
    )

    @server.tool(description="What is going on at the table right now.")
    def whats_happening() -> dict[str, Any]:
        return tools.whats_happening()

    @server.tool(description="Everyone and everywhere you have been told about.")
    def who_and_where() -> list[dict[str, Any]]:
        return tools.who_and_where()

    @server.tool(description="The characters this login owns, with their sheets.")
    def my_characters() -> list[dict[str, Any]]:
        return tools.my_characters()

    @server.tool(description="Say something at the table, in character.")
    async def say(text: str) -> str:
        return await tools.say(text)

    @server.tool(
        description=(
            "Ask the host to roll dice, e.g. '2d6+3', '4d6kh3', '2d20kl1'. "
            "The host rolls; you never decide the result."
        )
    )
    async def roll(notation: str) -> str:
        return await tools.roll(notation)

    @server.tool(
        description=(
            "Ask your DM to change one of your characters -- hit points, "
            "conditions, inventory, or the build. This sends a request. It is "
            "not applied until the DM approves it."
        )
    )
    async def update_my_character(entity_id: int, changes: dict[str, Any]) -> str:
        return await tools.update_my_character(entity_id, changes)

    return server
