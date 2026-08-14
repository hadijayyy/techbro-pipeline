"""Import pipeline-v3 and run scrape + filter only, no posting."""
import sys, os, json, time
os.chdir("/home/ubuntu/techbro")
sys.path.insert(0, ".")

# Force DRY_RUN = True
sys.argv = ["pipeline-v3.py", "--dry-run"]

import importlib.util
spec = importlib.util.spec_from_file_location("pipeline", "/home/ubuntu/techbro/pipeline-v3.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Run scraper - returns flat list
print("=== SCRAPING ===")
articles = mod.scrape_all()
total = len(articles)
print(f"Total articles scraped (after economy-title filter + per-source cap): {total}")

# Count by source
from collections import Counter
src_counts = Counter(a.get('source', '?') for a in articles)
for src, count in sorted(src_counts.items()):
    print(f"  {src}: {count}")

print(f"\n=== FILTER & SCORE ({total} candidates) ===")
passed = []
eligible = []
failed = 0
reject_reasons = {}

for a in articles:
    score, reason = mod._score_article(a)
    a['_score'] = score
    a['_reason'] = reason
    if score > 0:
        passed.append(a)
        body, image, published_ts = mod._fetch_article_body(a.get("url", ""))
        ok, final_reason = mod._is_eligible_candidate(a["title"], body, a.get("source", ""))
        a["_final_ok"] = ok
        a["_final_reason"] = final_reason
        a["_body_chars"] = len(body)
        if ok:
            eligible.append(a)
    else:
        failed += 1
        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

print(f"\nLolos filter: {len(passed)}")
print(f"Gagal: {failed}")
print(f"Body-eligible: {len(eligible)}")

if reject_reasons:
    print("\nReject reasons:")
    for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

print(f"\n=== PASSED (sorted by score) ===")
for i, p in enumerate(sorted(passed, key=lambda x: x.get('_score', 0), reverse=True)):
    title = p.get('title', '')[:90]
    score = p.get('_score', 0)
    source = p.get('source', '?')
    reason = p.get('_reason', '')
    print(f"  [{score:3d}] {source:20s} {title}")

print(f"\n=== BODY-ELIGIBLE (sorted by score) ===")
for p in sorted(eligible, key=lambda x: x.get('_score', 0), reverse=True):
    print(f"  [{p['_score']:3d}] {p.get('source', '?'):20s} {p['title'][:90]} ({p['_body_chars']} chars)")

print(f"\nCandidates >= 70: {len([p for p in passed if p.get('_score', 0) >= 70])}")
print(f"Candidates >= 80: {len([p for p in passed if p.get('_score', 0) >= 80])}")
