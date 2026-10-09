"""구독 직후 실행은 지정된 구독의 미리보기 작업만 처리한다."""

from uuid import UUID


class PreviewGateway:
    def __init__(self, gateway, subscription_id):
        if str(UUID(subscription_id)) != subscription_id:
            raise ValueError("PREVIEW_SUBSCRIPTION_ID_INVALID")
        self.gateway, self.subscription_id = gateway, subscription_id

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def list_due_subscriptions(self, now):
        return []

    def list_expired_subscriptions(self, now):
        return []

    def list_preview_subscriptions(self, now):
        rows = self.gateway.list_preview_subscriptions(now, subscription_id=self.subscription_id)
        if len(rows) > 1 or any(row["subscription_id"] != self.subscription_id for row in rows):
            raise ValueError("PREVIEW_SUBSCRIPTION_MISMATCH")
        return rows


class PreviewJobStore:
    def __init__(self, store, subscription_id):
        self.store, self.subscription_id = store, subscription_id

    def __getattr__(self, name):
        return getattr(self.store, name)

    def create_if_absent(self, job):
        if job.mail_kind != "subscription_preview" or job.subscription_id != self.subscription_id:
            raise ValueError("PREVIEW_JOB_MISMATCH")
        return self.store.create_if_absent(job)

    def open_jobs(self):
        return [job for job in self.store.open_jobs()
                if job.mail_kind == "subscription_preview" and job.subscription_id == self.subscription_id]
