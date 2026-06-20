from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import Message

from app.bot.telegram.callbacks import CallbackCodec
from app.core.container import AppContainer
from app.services.admin_tools_service import (
    AdminPanelAccessStore,
    AdminProfileCommentStore,
    BackupService,
    BlockReasonStore,
    FaqMediaStore,
    GroupTopicsStore,
    NotificationSettingsStore,
    PaymentReviewTargetStore,
    PaymentTextStore,
    ProhibitedGoodsStore,
    StaticContentStore,
    TopicDialogStore,
)


@dataclass
class AdminContext:
    container: AppContainer
    callback_codec: CallbackCodec
    payment_store: PaymentTextStore
    payment_target_store: PaymentReviewTargetStore
    notification_settings_store: NotificationSettingsStore
    prohibited_store: ProhibitedGoodsStore
    admin_access_store: AdminPanelAccessStore
    block_reason_store: BlockReasonStore
    profile_comment_store: AdminProfileCommentStore
    faq_media_store: FaqMediaStore
    group_topics_store: GroupTopicsStore
    topic_dialog_store: TopicDialogStore
    delivery_store: StaticContentStore
    contacts_store: StaticContentStore
    backup_service: BackupService

    @classmethod
    def from_container(cls, container: AppContainer) -> AdminContext:
        return cls(
            container=container,
            callback_codec=CallbackCodec(container.callback_signer),
            payment_store=PaymentTextStore(container.settings.database.dsn),
            payment_target_store=PaymentReviewTargetStore(container.settings.database.dsn),
            notification_settings_store=NotificationSettingsStore(container.settings.database.dsn),
            prohibited_store=ProhibitedGoodsStore(container.settings.database.dsn),
            admin_access_store=AdminPanelAccessStore(container.settings.database.dsn),
            block_reason_store=BlockReasonStore(container.settings.database.dsn),
            profile_comment_store=AdminProfileCommentStore(container.settings.database.dsn),
            faq_media_store=FaqMediaStore(container.settings.database.dsn),
            group_topics_store=GroupTopicsStore(container.settings.database.dsn),
            topic_dialog_store=TopicDialogStore(container.settings.database.dsn),
            delivery_store=StaticContentStore(
                database_dsn=container.settings.database.dsn,
                key="delivery_info",
                default_text="Раздел о доставке пока не заполнен.",
            ),
            contacts_store=StaticContentStore(
                database_dsn=container.settings.database.dsn,
                key="contacts_info",
                default_text="Раздел контактов пока не заполнен.",
            ),
            backup_service=BackupService(
                database_dsn=container.settings.database.dsn,
                profile_repo=container.profile_repo,
                buyout_repo=container.buyout_repo,
            ),
        )

    async def ensure_admin(self, message: Message) -> bool:
        if not message.from_user:
            return False
        return await self.container.admin_service.is_admin(message.from_user.id)
