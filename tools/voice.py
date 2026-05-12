from __future__ import annotations


async def join_channel(guild, channel):
    raise NotImplementedError("Voice not yet implemented")


async def speak(session, text: str) -> None:
    raise NotImplementedError("Voice not yet implemented")


async def leave_channel(session) -> None:
    raise NotImplementedError("Voice not yet implemented")


async def receive_audio(session):
    raise NotImplementedError("Voice not yet implemented")
