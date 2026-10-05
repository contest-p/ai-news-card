import feedparser
import json
from datetime import datetime
from sources import SOURCES

def check_rss():
    results = []
    for source in SOURCES:
        print(f"[{source['name']}] 검증 중...")
        try:
            # RSS 피드 가져오기
            feed = feedparser.parse(source['url'])
            
            # 파싱 에러나 기사가 없는 경우 실패 처리
            if feed.bozo:
                status = "실패 (열리지 않음)"
                entries_count = 0
            else:
                entries_count = len(feed.entries)
                status = "통과" if entries_count > 0 else "실패 (기사 없음)"
            
            # 첫 번째 기사 샘플로 날짜와 제목 확인
            sample_title = feed.entries[0].title if entries_count > 0 else "없음"
            sample_date = feed.entries[0].get('published', '없음') if entries_count > 0 else "없음"

            result = {
                "name": source["name"],
                "category": source["category"],
                "status": status,
                "article_count": entries_count,
                "sample_title": sample_title,
                "sample_date": sample_date
            }
            results.append(result)
            print(f" -> {status} (가져온 기사: {entries_count}개)")

        except Exception as e:
            print(f" -> 에러 발생: {e}")
            results.append({
                "name": source["name"],
                "status": f"에러 ({str(e)})"
            })

    # 결과를 results 폴더에 파일로 저장 (PRD 3번 '통과/실패 목록 전달' 목적)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"results/rss_report_{timestamp}.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 검증 완료! 결과가 {filename}에 저장되었습니다.")

if __name__ == "__main__":
    check_rss()