from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import Platform
from app.domain.models import OutboundMessage
from app.storage.interfaces import OutboundMessageRepository


class OutboundSender:
    async def send(self, message: OutboundMessage) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class OutboundDispatcher:
    repository: OutboundMessageRepository

    async def dispatch_pending(self, platform: Platform, sender: OutboundSender, batch_size: int = 50) -> int:
        messages = await self.repository.claim_pending(platform=platform, limit=batch_size)
        sent_count = 0
        for message in messages:
            try:
                await sender.send(message)
            except Exception:
                await self.repository.mark_failed(message.id)
                continue
            await self.repository.mark_sent(message.id)
            sent_count += 1
        return sent_count
