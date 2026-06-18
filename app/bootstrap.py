from __future__ import annotations

from app.core.config import load_settings
from app.core.container import build_container
from app.core.container import AppContainer
from app.storage.memory.repositories import (
    InMemoryAdminRepository,
    InMemoryBuyoutOrderRepository,
    InMemoryCodeReserveRepository,
    InMemoryFaqRepository,
    InMemoryOutboundMessageRepository,
    InMemorySessionRepository,
    InMemorySyncRepository,
    InMemoryUserProfileRepository,
)


async def build_container_from_env() -> AppContainer:
    settings = load_settings()
    use_memory = settings.database.dsn == "memory"

    if use_memory:
        profile_repo = InMemoryUserProfileRepository()
        session_repo = InMemorySessionRepository()
        sync_repo = InMemorySyncRepository()
        code_reserve_repo = InMemoryCodeReserveRepository(profile_repo)
        buyout_repo = InMemoryBuyoutOrderRepository()
        outbound_repo = InMemoryOutboundMessageRepository()
        admin_repo = InMemoryAdminRepository()
        faq_repo = InMemoryFaqRepository()
        return build_container(
            settings,
            profile_repo,
            session_repo,
            sync_repo,
            code_reserve_repo,
            buyout_repo,
            outbound_repo,
            admin_repo,
            faq_repo,
        )

    from app.storage.postgres.admin_repo import PostgresAdminRepository
    from app.storage.postgres.buyout_repo import PostgresBuyoutOrderRepository
    from app.storage.postgres.code_reserve_repo import PostgresCodeReserveRepository
    from app.storage.postgres.faq_repo import PostgresFaqRepository
    from app.storage.postgres.outbound_repo import PostgresOutboundMessageRepository
    from app.storage.postgres.pool import create_pool, init_schema
    from app.storage.postgres.profile_repo import PostgresSessionRepository, PostgresUserProfileRepository
    from app.storage.postgres.sync_repo import PostgresSyncRepository

    pool = await create_pool(settings.database)
    await init_schema(pool)
    profile_repo = PostgresUserProfileRepository(pool)
    session_repo = PostgresSessionRepository(pool)
    sync_repo = PostgresSyncRepository(pool)
    code_reserve_repo = PostgresCodeReserveRepository(pool)
    buyout_repo = PostgresBuyoutOrderRepository(pool)
    outbound_repo = PostgresOutboundMessageRepository(pool)
    admin_repo = PostgresAdminRepository(pool)
    faq_repo = PostgresFaqRepository(pool)
    return build_container(
        settings,
        profile_repo,
        session_repo,
        sync_repo,
        code_reserve_repo,
        buyout_repo,
        outbound_repo,
        admin_repo,
        faq_repo,
    )
