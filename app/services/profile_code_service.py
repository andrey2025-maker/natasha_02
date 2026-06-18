from __future__ import annotations

from dataclasses import dataclass

from app.storage.interfaces import UserProfileRepository


class ReservedCodeError(ValueError):
    pass


class UnknownProfileCodeError(ValueError):
    pass


@dataclass(slots=True)
class ProfileCodeService:
    repository: UserProfileRepository

    async def allocate_for_new_user(self) -> str:
        return await self.repository.next_available_code()

    async def validate_manual_code(self, code: str) -> None:
        if await self.repository.is_code_taken(code):
            return
        if await self.repository.is_code_reserved(code):
            return
        raise UnknownProfileCodeError("Code is not found")
