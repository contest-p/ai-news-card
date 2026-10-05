import feedparser
import json
from datetime import datetime
import os
from sources import SOURCES

def check_rss():
    results = []
    for source in SOURCES:
        print(f"[{source['name']}] 검증 중...")
        try:
            feed = feedparser.parse(source['url'])
            
            # RSS 자체가 잘못되었거나 차단된 경우
            if feed.bozo: 
                print(" -> RSS 파싱 실패 (봇 차단이거나 잘못된 주소)")
                results.append({"name": source['name'], "status": "실패", "reason": "파싱 실패"})
                continue
                
            entries = feed.entries
            if not entries:
                print(" -> 기사가 없습니다. (봇 차단 의심)")
                results.append({"name": source['name'], "status": "실패", "reason": "기사 없음"})
                continue
                
            # 첫 번째 기사 확인
            first_entry = entries[0]
            title = first_entry.get('title', '제목 없음')
            
            # 핵심 수정 부분: category가 없으면 '분류 없음'으로 처리!
            category = first_entry.get('category', '분류 없음') 
            
            print(f" -> 성공! 기사 {len(entries)}개 수집됨. (분야: {category})")
            results.append({
                "name": source['name'], 
                "status": "통과", 
                "article_count": len(entries),
                "sample_category": category
            })
            
        except Exception as e:
            print(f" -> 에러 발생: {e}")
            results.append({"name": source['name'], "status": "실패", "reason": str(e)})

    # 결과 저장
    os.makedirs('results', exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"results/rss_report_{timestamp}.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print(f"\n✅ 검증 완료! 결과가 {filename}에 저장되었습니다.")

if __name__ == "__main__":
    check_rss()