"""매시간 배치 1회(공통 PRD 5-2): 수집 → 작업 생성·복구 → 작업별 처리 → 개인정보 정리 → 실행 요약.

GitHub Actions 예약(UTC `7 * * * *`)이 이 함수를 호출한다. 실제 Backend·Firestore·SMTP 객체는
호출자가 PipelineDeps로 주입한다. 요약에는 수신 주소·토큰·기사 본문을 넣지 않는다.
"""

from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import time
import uuid

from engine.delivery import new_job
from engine.pipeline import DailyContext, PipelineDeps, process_job

BATCH_BUDGET = timedelta(minutes=45)       # P-06
COLLECTION_BUDGET = timedelta(minutes=15)  # P-06


def create_jobs(deps, now, errors):
    """Backend 목록으로 작업을 만든다. 이미 있으면 기존 작업을 그대로 쓴다(고정 ID)."""
    for method, mail_kind, error in (("list_due_subscriptions", "daily_briefing", "DUE_SUBSCRIPTIONS_UNAVAILABLE"),
                                     ("list_expired_subscriptions", "subscription_end",
                                      "EXPIRED_SUBSCRIPTIONS_UNAVAILABLE")):
        try:
            snapshots = getattr(deps.gateway, method)(now)
        except Exception:
            # 목록 조회 실패를 '대상 없음'으로 보지 않는다. 기존 미완료 작업 복구는 계속한다.
            errors.append(error)
            continue
        for snapshot in snapshots:
            try:
                deps.jobs.create_if_absent(new_job(snapshot, mail_kind))
            except (KeyError, ValueError, TypeError):
                errors.append("SNAPSHOT_INVALID")


def collection_summary(result) -> dict:
    if result is None:
        return {"succeeded": False, "articles": 0, "sources_ok": 0, "sources_failed": 0, "issue_counts": {}}
    return {"succeeded": result.collection_succeeded, "articles": len(result.articles),
            "sources_ok": result.successful_sources, "sources_failed": getattr(result, "failed_sources", 0),
            "issue_counts": dict(Counter(issue.code for issue in result.issues))}


def run_batch(*, deps: PipelineDeps, collect, run_id=None, privacy_cleanup=None,
              monotonic=time.monotonic, budget=BATCH_BUDGET, collection_budget=COLLECTION_BUDGET) -> dict:
    """collect(deadline=monotonic 기준 종료 시각) → LiveCollectionResult 형태의 결과."""
    run_id = run_id or uuid.uuid4().hex
    started = monotonic()
    batch_deadline = started + budget.total_seconds()
    started_at = deps.clock()
    errors: list[str] = []
    collected = None
    try:
        collected = collect(deadline=min(batch_deadline, started + collection_budget.total_seconds()))
        context = DailyContext(list(collected.articles), collected.collection_succeeded)
    except Exception:
        errors.append("COLLECTION_CRASHED")
        context = DailyContext([], False)

    create_jobs(deps, deps.clock(), errors)
    # 기한이 이른 작업부터 처리한다. 이전 실행에서 남은 pending·failed·멈춘 작업도 포함된다.
    jobs = sorted(deps.jobs.open_jobs(), key=lambda job: (job.deadline_at, job.job_id))
    reports, unprocessed, budget_exceeded = [], 0, False
    for index, job in enumerate(jobs):
        if monotonic() >= batch_deadline:
            budget_exceeded, unprocessed = True, len(jobs) - index
            errors.append("BATCH_BUDGET_EXCEEDED")
            break
        try:
            reports.append(process_job(job.job_id, context=context, deps=deps, run_id=run_id,
                                       batch_deadline=batch_deadline, monotonic=monotonic))
        except Exception:
            # 작업은 processing으로 남고 선점 만료 후 다음 실행이 다시 처리한다(SMTP 전 단계).
            errors.append("JOB_CRASHED")
            reports.append({"job_id": job.job_id, "status": "crashed", "content_kind": None,
                            "error_code": "JOB_CRASHED", "issues": []})

    cleanup = "not_connected"
    if privacy_cleanup is not None:
        try:
            privacy_cleanup(deps.clock())
            cleanup = "completed"
        except Exception:
            cleanup = "failed"
            errors.append("PRIVACY_CLEANUP_FAILED")

    duration = monotonic() - started
    if duration >= budget.total_seconds():
        budget_exceeded = True
        errors.append("BATCH_BUDGET_EXCEEDED")
    return {
        "run_id": run_id, "started_at": started_at.isoformat(), "finished_at": deps.clock().isoformat(),
        "duration_seconds": round(duration, 3), "budget_exceeded": budget_exceeded,
        "collection": collection_summary(collected),
        "jobs": {"total": len(jobs), "processed": len(reports), "unprocessed": unprocessed,
                 "by_status": dict(Counter(report["status"] for report in reports)),
                 "by_content_kind": dict(Counter(report["content_kind"] for report in reports
                                                 if report["content_kind"] and report["status"] != "skipped")),
                 "results": [{key: report[key] for key in ("job_id", "status", "content_kind", "error_code", "issues")}
                             for report in reports]},
        "privacy_cleanup": cleanup, "errors": list(dict.fromkeys(errors)),
    }


def write_summary(summary: dict, root: Path) -> Path:
    """실행 요약 JSON 저장(NFR-06). 운영에서는 Actions 아티팩트·로그로도 남긴다."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{summary['run_id']}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
