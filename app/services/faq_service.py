from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.models import FaqSection
from app.storage.interfaces import FaqRepository


@dataclass(slots=True)
class FaqService:
    repository: FaqRepository

    async def list_children(self, parent_id: int | None) -> list[FaqSection]:
        return await self.repository.list_children(parent_id)

    async def get_section(self, section_id: int) -> FaqSection | None:
        return await self.repository.get_by_id(section_id)

    async def create_section(self, parent_id: int | None, title: str) -> FaqSection:
        section = FaqSection(
            id=0,
            parent_id=parent_id,
            title=title.strip(),
            created_at=datetime.utcnow(),
        )
        return await self.repository.create(section)

    async def update_section_text(self, section_id: int, text: str) -> FaqSection | None:
        section = await self.repository.get_by_id(section_id)
        if not section:
            return None
        section.content_text = text.strip()
        return await self.repository.update(section)

    async def update_section_title(self, section_id: int, title: str) -> FaqSection | None:
        section = await self.repository.get_by_id(section_id)
        if not section:
            return None
        section.title = title.strip()
        return await self.repository.update(section)

    async def collect_section_tree_ids(self, section_id: int) -> list[int]:
        ids = [section_id]
        for child in await self.list_children(section_id):
            ids.extend(await self.collect_section_tree_ids(child.id))
        return ids

    async def delete_section(self, section_id: int) -> list[int] | None:
        section = await self.repository.get_by_id(section_id)
        if not section:
            return None
        tree_ids = await self.collect_section_tree_ids(section_id)
        deleted = await self.repository.delete(section_id)
        if not deleted:
            return None
        return tree_ids

    async def breadcrumbs(self, section_id: int | None) -> str:
        if section_id is None:
            return "Вопросы"
        parts: list[str] = []
        current = await self.repository.get_by_id(section_id)
        depth_guard = 0
        while current and depth_guard < 20:
            parts.append(current.title)
            if current.parent_id is None:
                break
            current = await self.repository.get_by_id(current.parent_id)
            depth_guard += 1
        parts.reverse()
        return " / ".join(["Вопросы", *parts])
