"""Backfill retention and initial settings. Read-only unless --write; no PII output."""
import argparse
from datetime import date, datetime, timedelta, timezone
from google.cloud import firestore
try:
    from backend.engine_api import SubscriptionSettings, instant, utc
except ImportError:
    raise SystemExit("Run from repository root: python -m backend.tools.migrate_lifecycle")


def retention_fields(data, document_id):
    patch = {}
    identity = data.get("subscription_id", document_id)
    if not data.get("subscription_id"):
        patch["subscription_id"] = identity
    if "cleanup_after" not in data:
        ended = data.get("cancelled_at") or data.get("canceled_at") or data.get("expires_at")
        if not ended and (data.get("end_date_exclusive") or data.get("end_date")):
            exclusive = data.get("end_date_exclusive")
            day = date.fromisoformat(exclusive or data["end_date"])
            ended = instant(day if exclusive else day + timedelta(days=1))
        ended = ended or data.get("created_at")
        if isinstance(ended, str):
            ended = datetime.fromisoformat(ended.replace("Z", "+00:00"))
        if ended is None:
            raise ValueError("RETENTION_DATE_MISSING")
        patch["cleanup_after"] = utc(ended) + timedelta(days=29)
    if "settings_versions" not in data and data.get("start_date"):
        try:
            settings = SubscriptionSettings.model_validate(data)
        except ValueError:
            pass  # Do not fabricate missing settings or consent.
        else:
            patch.update(settings_version=data.get("settings_version",1), effective_date=data["start_date"],
                settings_versions=[{**settings.model_dump(),"settings_version":data.get("settings_version",1),
                                    "effective_date":data["start_date"]}])
    return patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write",action="store_true")
    parser.add_argument("--limit",type=int,default=500)
    args=parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be 1..1000")
    from backend.main import db
    count,invalid=0,0
    for name in ("subscriptions","subscription_history"):
        docs=list(db.collection(name).limit(args.limit+1).stream(timeout=20))
        if len(docs)>args.limit:
            raise SystemExit("COLLECTION_LIMIT_EXCEEDED: increase limit or implement paginated migration")
        for row in docs:
            try:
                changes=retention_fields(row.to_dict(),row.id)
            except (KeyError,ValueError,TypeError):
                invalid+=1
                continue
            if changes:
                count+=1
                if args.write:
                    @firestore.transactional
                    def save(tx):
                        current=row.reference.get(transaction=tx)
                        if not current.exists:
                            return
                        patch=retention_fields(current.to_dict(),row.id)
                        tx.set(row.reference,{**current.to_dict(),**patch})
                    save(db.transaction())
    print({"write":args.write,"changed_or_planned":count,"invalid_requires_review":invalid})
    if invalid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
