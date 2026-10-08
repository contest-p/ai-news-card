"""Bounded migration/reindex for existing articles; no AI, mail or deployment."""
import argparse
from datetime import datetime, timezone
import json
import sys

from engine.embeddings import E5Encoder
from engine.firestore_article_store import FirestoreArticleRepository
from engine.firestore_connection import create_client
from engine.firestore_rag import FirestoreRagStore
from engine.live_collection import load_sources, allowed_url
from engine.selection import aware_utc


def reindex_records(repository, rag, records, sources):
    counts = {}
    for record in records:
        try:
            repository.publisher_for(record.article.article_id)
        except LookupError:
            publishers = set()
            for source in sources:
                try:
                    allowed_url(record.article.url, source)
                    publishers.add(source.name)
                except ValueError:
                    continue
            if len(publishers) != 1:
                counts['publisher_missing'] = counts.get('publisher_missing', 0) + 1
                continue
            repository.set_publisher(record, publishers.pop())
        state = rag.index(record)
        counts[state] = counts.get(state, 0) + 1
    return counts


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description='기존 기사 날짜 범위의 임베딩/출처 재처리')
    parser.add_argument('--project', required=True)
    parser.add_argument('--credentials')
    parser.add_argument('--since', type=datetime.fromisoformat, required=True, help='시간대 포함 ISO 날짜시각')
    parser.add_argument('--before', type=datetime.fromisoformat, required=True)
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--write', action='store_true', help='지정 범위 기사에만 벡터/출처 저장')
    args = parser.parse_args()
    client = None
    try:
        since, before = aware_utc(args.since), aware_utc(args.before)
        client = create_client(args.project, args.credentials)
        repository = FirestoreArticleRepository(client)
        records = repository.recent_records(since=since, before=before, limit=args.limit)
        report = {'article_count': len(records), 'database_written': False, 'mail_sent': False}
        if args.write:
            counts = reindex_records(repository, FirestoreRagStore(repository, E5Encoder()), records, load_sources())
            report.update(database_written=True, embeddings=counts)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(bool(report.get('embeddings', {}).get('failed') or report.get('embeddings', {}).get('publisher_missing')))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_code': 'REINDEX_FAILED', 'error_type': type(error).__name__}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    sys.exit(main())
