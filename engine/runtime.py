"""Service assembly for daily briefings and natural expiry notices; no scheduler setup."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import time

from engine.batch import run_batch, write_summary
from engine.card_builder import GenerationCardBuilder
from engine.card_images import render_with_fallback
from engine.card_render import resolve_font_path
from engine.chat_client import CodysseyChatClient
from engine.collection import CollectionIssue
from engine.embeddings import LazyE5Encoder
from engine.firestore_article_store import FirestoreArticleRepository
from engine.firestore_rag import FirestoreRagStore
from engine.firestore_runtime_store import FirestoreGenerationStore, FirestoreJobStore, FirestoreMailArchive
from engine.http_gateway import HttpEngineGateway
from engine.live_collection import collect_live_sources
from engine.mail_assembly import WebMailLinks
from engine.pipeline import PipelineDeps, CardOutcome
from engine.settings import load_chat_settings
from engine.smtp_sender import load_smtp_account, send_message
from engine.preview import PreviewGateway, PreviewJobStore

ROOT = Path(__file__).resolve().parents[1]


def int_setting(name, default, minimum, maximum):
    value = int(os.environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(name + "_INVALID")
    return value


def run_connected(client, *, gateway=None, sender=None, smtp=None, encoder=None,
                  text_only=False, test=False, jobs_wrapper=None,
                  delivery_at=None,
                  preview_subscription_id=None,
                  clock=lambda: datetime.now(timezone.utc)):
    """Explicitly invoked by a CLI; test adapters retain recipient restrictions."""
    smtp = smtp or load_smtp_account()
    chat = load_chat_settings()
    gateway = gateway or HttpEngineGateway(os.environ.get("ENGINE_API_BASE_URL", ""),
                                           os.environ.get("ENGINE_API_TOKEN", ""))
    if preview_subscription_id:
        gateway = PreviewGateway(gateway, preview_subscription_id)
    repository = FirestoreArticleRepository(client)
    rag = FirestoreRagStore(repository, encoder or LazyE5Encoder(),
                           min_score=float(os.environ.get("ENGINE_RAG_MIN_SCORE", "0.85")), clock=clock)
    prefix = "engine_test_" if test else "engine_"
    jobs = FirestoreJobStore(client, collection_name=prefix + "delivery_jobs")
    if preview_subscription_id:
        jobs = PreviewJobStore(jobs, preview_subscription_id)
    archive = FirestoreMailArchive(client, retention=timedelta(days=int_setting(
        "ENGINE_ARCHIVE_RETENTION_DAYS", 0, 1, 30)), collection_name=prefix + "mail_archives", clock=clock)
    generation = FirestoreGenerationStore(client, collection_name=prefix + "generation_jobs", clock=clock)
    builder = GenerationCardBuilder(repository=repository, observed_at=clock(),
        publisher_for=repository.publisher_for, client=CodysseyChatClient(chat), model=chat.model,
        base_url=chat.base_url, store=generation, search_evidence=rag, record_search=rag.record_search, record_usage=rag.record_usage)
    stats = {"ready": 0, "failed": 0, "reused": 0, "stale": 0}
    # Zero processes every due job within the existing 45-minute batch budget.
    # A positive value remains an explicit operator-selected cost limit.
    generation_budget = int_setting("ENGINE_MAX_CARD_JOBS_PER_BATCH", 0, 0, 1000)
    generated = 0
    def bounded_builder(job, article):
        nonlocal generated
        if generation_budget and generated >= generation_budget:
            return CardOutcome("failed", None, True, "GENERATION_BATCH_LIMIT", None)
        generated += 1
        return builder(job, article)
    maximum = int_setting("ENGINE_MAX_ENTRIES_PER_SOURCE", 20, 1, 20)
    recovery_limit = int_setting("ENGINE_ARTICLE_RECOVERY_LIMIT", 500, 1, 5000)
    reindex_limit = int_setting("ENGINE_REINDEX_LIMIT", 50, 1, 500)

    def collect(*, deadline):
        result = collect_live_sources(max_entries=maximum, deadline=deadline)
        observed = clock()
        builder.observed_at = observed
        valid = []
        for article in result.articles:
            try:
                record = repository.save(article, observed_at=observed).record
                repository.set_publisher(record, result.article_sources[article.article_id]["publisher"])
                valid.append(record.article)
                if time.monotonic() < deadline:
                    outcome = rag.index(record)
                    stats[outcome] = stats.get(outcome, 0) + 1
            except Exception:
                result.issues.append(CollectionIssue("storage", None, "ARTICLE_STORE_FAILED"))
                result.failed_sources += 1
        result.articles = valid
        try:
            backlog = rag.reindex_pending(limit=reindex_limit, deadline=deadline)
            for key, value in backlog.items():
                stats[key] = stats.get(key, 0) + value
        except Exception:
            stats["reindex_failed"] = 1
        try:
            # Earlier schedules may be retried for three hours; retain a 27-hour range.
            stored = repository.recent_articles(since=observed - timedelta(hours=27),
                                                 before=observed, limit=recovery_limit)
            merged = {article.url: article for article in result.articles}
            for article in stored:
                merged.setdefault(article.url, article)
            result.articles = list(merged.values())
        except Exception:
            result.issues.append(CollectionIssue("storage", None, "ARTICLE_RECOVERY_FAILED"))
            result.failed_sources += 1
        return result

    render_root = ROOT / ".engine-local" / ("connected-test" if test else "batch")
    render = (lambda data, job: ((), ["TEXT_ONLY"])) if text_only else (
        lambda data, job: render_with_fallback(data, render_root / job.job_id,
            font_path=Path(os.environ["CARD_FONT_PATH"]) if os.environ.get("CARD_FONT_PATH") else resolve_font_path()))
    deps = PipelineDeps(gateway=gateway, jobs=jobs_wrapper(jobs) if jobs_wrapper else jobs,
        archive=archive, build_cards=bounded_builder, render_images=render,
        send=sender or (lambda message, recipient: send_message(smtp, message, recipient=recipient)),
        sender_email=smtp.sender, web_links=WebMailLinks(os.environ["ENGINE_WEB_BASE_URL"]), clock=clock)
    cleanup_stats = {}
    def cleanup(now):
        cleanup_stats["expired_archives_deleted"] = archive.delete_expired(now)
        # Backend remains responsible for user, subscription, feedback and Auth deletion.
        gateway.privacy_cleanup(now)
    summary = run_batch(deps=deps, collect=collect, privacy_cleanup=None if test or preview_subscription_id else cleanup,
                        delivery_at=delivery_at)
    summary.update(embedding=stats, cleanup=cleanup_stats, card_jobs_attempted=generated,
                   test_only=test, delivery_confirmed=False)
    write_summary(summary, render_root / "runs")
    return summary
