from __future__ import annotations

from dataclasses import dataclass

from app.core.config import AppSettings
from app.security.callback_signer import CallbackSigner
from app.services.admin_service import AdminService
from app.services.faq_service import FaqService
from app.services.flows.buyout_flow import BuyoutFlowService
from app.services.flows.profile_flow import ProfileFlowService
from app.services.order_admin_service import OrderAdminService
from app.services.profile_code_service import ProfileCodeService
from app.services.rate_limit_service import RateLimitService
from app.services.stats_service import StatsService
from app.services.sync_service import SyncService
from app.services.user_preferences_store import UserPreferencesStore
from app.storage.interfaces import (
    CodeReserveRepository,
    AdminRepository,
    BuyoutOrderRepository,
    FaqRepository,
    OutboundMessageRepository,
    SessionRepository,
    SyncRepository,
    UserProfileRepository,
)


@dataclass(slots=True)
class AppContainer:
    settings: AppSettings
    callback_signer: CallbackSigner
    rate_limiter: RateLimitService
    profile_repo: UserProfileRepository
    session_repo: SessionRepository
    sync_repo: SyncRepository
    code_reserve_repo: CodeReserveRepository
    buyout_repo: BuyoutOrderRepository
    outbound_repo: OutboundMessageRepository
    admin_repo: AdminRepository
    faq_repo: FaqRepository
    profile_code_service: ProfileCodeService
    sync_service: SyncService
    admin_service: AdminService
    faq_service: FaqService
    order_admin_service: OrderAdminService
    stats_service: StatsService
    profile_flow: ProfileFlowService
    buyout_flow: BuyoutFlowService


def build_container(
    settings: AppSettings,
    profile_repo: UserProfileRepository,
    session_repo: SessionRepository,
    sync_repo: SyncRepository,
    code_reserve_repo: CodeReserveRepository,
    buyout_repo: BuyoutOrderRepository,
    outbound_repo: OutboundMessageRepository,
    admin_repo: AdminRepository,
    faq_repo: FaqRepository,
) -> AppContainer:
    callback_signer = CallbackSigner(settings.security.callback_secret)
    rate_limiter = RateLimitService(settings.rate_limits)
    profile_code_service = ProfileCodeService(profile_repo)
    sync_service = SyncService(sync_repo, settings.security)
    admin_service = AdminService(
        main_admin_id=settings.telegram.main_admin_id,
        admin_repo=admin_repo,
        profile_repo=profile_repo,
    )
    faq_service = FaqService(repository=faq_repo)
    order_admin_service = OrderAdminService(repository=buyout_repo, profile_repo=profile_repo)
    stats_service = StatsService(profile_repo=profile_repo, buyout_repo=buyout_repo)
    profile_flow = ProfileFlowService(profile_repo, session_repo, profile_code_service, sync_service)
    buyout_flow = BuyoutFlowService(
        profile_repo,
        session_repo,
        buyout_repo,
        preferences_store=UserPreferencesStore(settings.database.dsn),
    )

    return AppContainer(
        settings=settings,
        callback_signer=callback_signer,
        rate_limiter=rate_limiter,
        profile_repo=profile_repo,
        session_repo=session_repo,
        sync_repo=sync_repo,
        code_reserve_repo=code_reserve_repo,
        buyout_repo=buyout_repo,
        outbound_repo=outbound_repo,
        admin_repo=admin_repo,
        faq_repo=faq_repo,
        profile_code_service=profile_code_service,
        sync_service=sync_service,
        admin_service=admin_service,
        faq_service=faq_service,
        order_admin_service=order_admin_service,
        stats_service=stats_service,
        profile_flow=profile_flow,
        buyout_flow=buyout_flow,
    )
