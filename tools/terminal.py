from __future__ import annotations

import asyncio


async def execute(command: str, timeout: int = 30) -> str:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"Command timed out after {timeout}s"

    output = b"".join(part for part in (stdout, stderr) if part)
    text = output.decode(errors="replace").strip()
    if not text:
        text = f"Command exited with code {proc.returncode}"
    return text[:12000]
