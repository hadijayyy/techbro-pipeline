#!/usr/bin/env python3
"""
Techbro v3 — EKONOMI NASIONAL + POV PRIBADI + 6 Script Hack Elements
Article-based: scrape economy RSS/HTML → 6 threads with personal POV.
"""

import contextlib, errno, fcntl, html, httpx, json, logging, os, random, re, struct, sys, tempfile, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))
from email.utils import parsedate_to_datetime
from pathlib import Path
from bs4 import BeautifulSoup

# ── CLI Flags ────────────────────────────────────────────────────────────────

IMAGE_URL = None
for i, a in enumerate(sys.argv):
    if a == "--image-url" and i + 1 < len(sys.argv):
        IMAGE_URL = sys.argv[i + 1]
        break
DRY_RUN = "--dry-run" in sys.argv
CANDIDATE_POOL_LIMIT = 10
DISCOVERY_POOL_LIMIT = 25
SCRAPE_ARTICLE_LIMIT = 100
HOT_TOPIC_LIMIT = CANDIDATE_POOL_LIMIT
LLM_REQUEST_BUDGET = 4  # writer/verifier plus one revision/verifier; transport retries disabled.

# ── Paths ────────────────────────────────────────────────────────────────────

BASE = Path(__file__).parent
POSTED_FILE = BASE / "posted_topics_v2.json"
KEYWORDS_FILE = BASE / "keywords.json"
SOURCES_FILE = BASE / "sources.json"
INFLIGHT_FILE = BASE / "inflight_chain.json"
POST_LOCK_FILE = Path("/tmp/techbro-post-url.lock")

class LedgerStateError(RuntimeError):
    pass

@contextlib.contextmanager
def post_url_lock():
    fd = os.open(POST_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

# ── Env ───────────────────────────────────────────────────────────────────────

GRAPH = "https://graph.threads.net/v1.0"
THREADS_TOKEN = None
try:
    from dotenv import load_dotenv
    # Reuse Hermes custom-provider credential for local 9router LLM calls.
    load_dotenv(Path.home() / ".hermes" / ".env", override=False)
    # Cron/Hermes may inherit stale secrets; project .env is source of truth.
    load_dotenv(BASE / ".env", override=True)
    THREADS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")
except Exception:
    pass

SZEJAY_BOT_TOKEN = os.getenv("SZEJAY_BOT_TOKEN")
# Report destination: Hadijayyy Telegram DM, not bot's own ID.
SZEJAY_CHAT_ID = "1022032312"

log = logging.getLogger("techbro-v3")
THREADS_USER_ID = None
METRICS_TOKEN_OK = False
if THREADS_TOKEN and not DRY_RUN:
    try:
        r = httpx.get(f"{GRAPH}/me?access_token={THREADS_TOKEN}", timeout=10)
        if r.status_code == 200:
            THREADS_USER_ID = r.json().get("id")
            log.info(f"Token: POST OK | user_id={THREADS_USER_ID}")
            mr = httpx.get(f"{GRAPH}/{THREADS_USER_ID}/threads?access_token={THREADS_TOKEN}&limit=1", timeout=10)
            if mr.status_code == 200:
                METRICS_TOKEN_OK = True
                log.info("Token: METRICS OK")
            else:
                log.warning(f"Token: METRICS HTTP {mr.status_code} — engagement tracking may fail")
        else:
            log.warning(f"Token: POST HTTP {r.status_code} — check THREADS_ACCESS_TOKEN")
    except Exception as e:
        log.warning(f"Token check failed: {e}")

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S")
# httpx logs full request URLs at INFO, which leaks access_token in query strings.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Keyword Loader ───────────────────────────────────────────────────────────

def load_keywords():
    """Load scoring keywords from keywords.json. Returns dict with all keyword lists."""
    defaults = {
        "score_categories": [],
        "entity_boost": {},
        "number_bonus": [],
        "editorial_selection": {},
        "hard_reject": [],
        "soft_reject": [],
        "named_blacklist": [],
        "score_thresholds": {"reject": 45, "backup": 60, "process": 60, "priority": 75},
    }
    try:
        with open(KEYWORDS_FILE) as f:
            kw = json.load(f)
            for k in defaults:
                defaults[k] = kw.get(k, defaults[k])
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning(f"keywords.json not found ({e}) — using built-in defaults")
    return defaults


KW = load_keywords()

# ── Economy Sources ──────────────────────────────────────────────────────────

def load_sources():
    """Load only maintained sources; malformed config fails closed."""
    try:
        data = json.loads(SOURCES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    required = {"url", "score", "type", "domain"}
    return {
        name: cfg for name, cfg in data.items()
        if isinstance(name, str) and isinstance(cfg, dict)
        and required <= set(cfg) and cfg["type"] in {"rss", "html"}
    }


SOURCES = load_sources()
MAX_ARTICLES_PER_SOURCE = 6
SOURCE_ARTICLE_CAPS = {
    "cnn_ekonomi": 15,
    "detik_finance": 15,
    "cnbc_market": 15,
    "cnbc_entrepreneur": 15,
    "antara_ekonomi": 15,
    "bi_release": 15,
    "kemenkeu_release": 15,
    "esdm_news": 15,
    "dailysocial": 15,
    "cnbc_global": 15,
    "bbc_business": 15,
    "tempo_bisnis": 15,
    "republika_ekonomi": 15,
    "katadata_ekonomi": 15,
}
SOURCE_TIERS = {
    "bi_release": ("primary_official", 12),
    "kemenkeu_release": ("primary_official", 12),
    "esdm_news": ("primary_official", 10),
    "cnn_ekonomi": ("secondary_media", 5),
    "detik_finance": ("secondary_media", 5),
    "cnbc_market": ("secondary_media", 5),
    "cnbc_entrepreneur": ("secondary_media", 5),
    "antara_ekonomi": ("secondary_media", 6),
    "cnbc_global": ("secondary_media", 5),
    "bbc_business": ("secondary_media", 6),
    "dailysocial": ("secondary_media", 3),
    "tempo_bisnis": ("secondary_media", 7),
    "republika_ekonomi": ("secondary_media", 6),
    "katadata_ekonomi": ("secondary_media", 8),
}
SOURCE_DISPLAY_NAMES = {
    "cnn_ekonomi": "CNN Indonesia",
    "detik_finance": "Detik Finance",
    "cnbc_market": "CNBC Indonesia",
    "cnbc_entrepreneur": "CNBC Indonesia",
    "antara_ekonomi": "Antara News",
    "bi_release": "Bank Indonesia",
    "kemenkeu_release": "Kementerian Keuangan",
    "esdm_news": "Kementerian ESDM",
    "dailysocial": "DailySocial",
    "cnbc_global": "CNBC International",
    "bbc_business": "BBC",
    "tempo_bisnis": "Tempo Bisnis",
    "republika_ekonomi": "Republika",
    "katadata_ekonomi": "Katadata",
}
CURRENT_COHORT = "techbro_v3_current"
LEGACY_COHORT = "legacy"

# ── Scoring Configuration (loaded from keywords.json) ──────────────────────────

SCORE_CATEGORIES = KW["score_categories"]
ENTITY_BOOST = KW["entity_boost"]
NUMBER_BONUS = KW["number_bonus"]
EDITORIAL_SELECTION = KW["editorial_selection"]
HARD_REJECT = KW["hard_reject"]
SOFT_REJECT = KW["soft_reject"]
NAMED_BLACKLIST = KW["named_blacklist"]
SCORE_THRESHOLDS = KW["score_thresholds"]
# Forensik winner/loser @ryanhadiii — non-event hard/soft gates
NON_EVENT_HARD = KW.get("non_event_hard", [])
NON_EVENT_SOFT = KW.get("non_event_soft", [])
DECISION_MARKERS = KW.get("decision_markers", [])

# ── Number Parsing ──────────────────────────────────────────────────────────────

def _parse_number_in_title(title):
    """Extract largest Rp amount from title for bonus calculation."""
    big_matches = re.findall(r'rp\s*(\d[\d,.]*)\s*(triliun|miliar|juta)', title.lower())
    if not big_matches:
        big_matches = re.findall(r'(\d[\d,.]*)\s*(triliun|miliar|juta)\s*rupiah', title.lower())
    multipliers = {"triliun": 1e12, "miliar": 1e9, "juta": 1e6}
    max_val = 0
    for val_str, unit in big_matches:
        val = float(val_str.replace('.', '').replace(',', '.'))
        max_val = max(max_val, val * multipliers.get(unit, 1))
    return max_val

# ── Data Persistence ─────────────────────────────────────────────────────────

def load_data():
    try:
        data = json.loads(POSTED_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerStateError(f"posted ledger unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        raise LedgerStateError("posted ledger schema invalid: topics")
    if not isinstance(data.get("recent_content", {}), dict):
        raise LedgerStateError("posted ledger schema invalid: recent_content")
    if any(not isinstance(topic, dict) for topic in data["topics"]):
        raise LedgerStateError("posted ledger schema invalid: topic row")
    return data


def normalize_topic_cohorts(data):
    """Label pre-existing ledger rows legacy; new rows set current explicitly."""
    changed = False
    for topic in data.get("topics", []):
        if "cohort" not in topic:
            topic["cohort"] = topic_cohort(topic)
            changed = True
    return changed

def save_data(data):
    if DRY_RUN:
        return False
    _atomic_write_json(POSTED_FILE, data)
    return True


def _atomic_write_json(path, data):
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def topic_cohort(topic):
    """Keep current 7-slide data separate from legacy ledgers."""
    if topic.get("cohort"):
        return topic["cohort"]
    slides = topic.get("slides") or {}
    if (topic.get("article_source") and topic.get("pattern") and topic.get("hook_pattern")
            and all(slides.get(f"post_{i}") for i in range(1, 8))):
        return CURRENT_COHORT
    if topic.get("article_source") or topic.get("pattern") or topic.get("slides"):
        return CURRENT_COHORT
    return LEGACY_COHORT


def _is_current_topic(topic):
    """Analytics accepts explicit current rows and unlabelled test/runtime rows."""
    return topic.get("cohort") != LEGACY_COHORT

def load_inflight():
    if not INFLIGHT_FILE.exists():
        return None
    try:
        data = json.loads(INFLIGHT_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerStateError(f"inflight journal unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("posts"), dict):
        raise LedgerStateError("inflight journal schema invalid")
    return data

def save_inflight(data):
    if DRY_RUN:
        return False
    _atomic_write_json(INFLIGHT_FILE, data)
    return True


def _publish_complete(pub, posts):
    """Only verified complete seven-post chain may enter dedup/analytics state."""
    expected = 7
    complete_posts = all(posts.get(f"post_{i}", "").strip() for i in range(1, expected + 1))
    return bool(pub and not pub.get("error") and pub.get("root_verified") and complete_posts
                and len(pub.get("post_ids", [])) == expected)


def _topic_canonical_urls(topic):
    """Collect every URL-bearing ledger field under one canonicalizer."""
    urls = [topic.get("article_url"), topic.get("canonical_url"), topic.get("source_url")]
    slides = topic.get("slides") or {}
    urls.extend(re.findall(r"https?://[^\s<>\"']+", str(slides.get("post_7", ""))))
    return {_canonical_url(url) for url in urls if _canonical_url(url)}


def posted_canonical_urls(data):
    return {url for topic in data.get("topics", []) for url in _topic_canonical_urls(topic)}


def duplicate_ledger_match(data, article_url):
    canonical = _canonical_url(article_url)
    return canonical if canonical and canonical in posted_canonical_urls(data) else None


def _title_tokens(title):
    """Lowercase alnum tokens; drops leading section tags and filler words."""
    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    stop = {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "pada", "ini",
            "itu", "juga", "akan", "sudah", "masih", "saat", "karena", "agar",
            "para", "terus", "bisa", "bakal", "mau", "tapi"}
    return [t for t in tokens if t not in stop and len(t) > 1]


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def duplicate_title_match(data, title, min_sim=0.55, hours=24):
    """Block near-identical titles posted within `hours` (anti-spam audience guard).

    Uses token Jaccard on normalized titles; exact URL dedup remains the strict gate.
    Returns matching posted title string or None.
    """
    if not title:
        return None
    now_ts = datetime.now(WIB)
    tokens = _title_tokens(title)
    for topic in data.get("topics", []):
        if not topic.get("title"):
            continue
        ts = topic.get("timestamp") or topic.get("posted") or ""
        try:
            ts = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=WIB)
        if (now_ts - ts).total_seconds() > hours * 3600:
            continue
        if _jaccard(tokens, _title_tokens(topic["title"])) >= min_sim:
            return topic["title"]
    return None


def _mark_inflight(inflight, **updates):
    inflight.update(updates)
    save_inflight(inflight)


def _verify_published_root(post_id):
    """Require Graph proof that root is image media and has permalink."""
    if not post_id or not THREADS_TOKEN:
        return None
    try:
        response = httpx.get(
            f"{GRAPH}/{post_id}",
            params={"fields": "id,media_type,permalink", "access_token": THREADS_TOKEN},
            timeout=15,
        )
        if response.status_code != 200:
            log.warning("Root verification HTTP %s for %s", response.status_code, post_id)
            return None
        payload = response.json()
        media_type = str(payload.get("media_type", "")).upper()
        permalink = payload.get("permalink")
        if media_type not in {"IMAGE", "IMAGE_POST"} or not permalink:
            log.warning("Root verification failed for %s: media_type=%s permalink=%s", post_id, media_type, bool(permalink))
            return None
        return {"media_type": media_type, "permalink": permalink}
    except (httpx.RequestError, ValueError, TypeError) as exc:
        log.warning("Root verification failed for %s: %s", post_id, exc)
        return None


def send_success_report(title, pattern, elapsed, permalink):
    """Best-effort Telegram report after a complete live Threads chain."""
    if not SZEJAY_BOT_TOKEN:
        log.warning("Success report skipped: SZEJAY_BOT_TOKEN missing")
        return False
    text = (f"✅ v3 Posted @ {datetime.now(WIB):%H:%M} WIB\n{title}\n"
            f"Pattern: {pattern} | {elapsed:.1f}s\n{permalink}")
    payload = json.dumps({"chat_id": SZEJAY_CHAT_ID, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{SZEJAY_BOT_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
        if result.get("ok"):
            log.info("Success report sent to @szejay_bot")
            return True
        log.warning("Success report rejected by Telegram: %s", result.get("description"))
    except Exception as e:
        log.warning(f"Success report failed: {e}")
    return False


# ── Performance Metrics (feedback loop) ─────────────────────────────────────

def _fetch_engagement_metrics(post_id, token=None, timeout=15):
    """Fetch per-post Threads insights. Returns dict of ints or None on failure.

    Mirrors budakorporat evaluation: /{post_id}/insights?metric=views,likes,...
    """
    if not post_id:
        return None
    token = token or THREADS_TOKEN
    if not token:
        return None
    metric_names = ("views", "likes", "replies", "reposts", "quotes")
    try:
        r = httpx.get(f"{GRAPH}/{post_id}/insights",
                      params={"metric": ",".join(metric_names), "access_token": token},
                      timeout=timeout)
        if r.status_code != 200:
            log.warning("Metrics HTTP %s for %s", r.status_code, post_id)
            return None
        result = {}
        for item in r.json().get("data", []):
            values = item.get("values") or [{}]
            result[item.get("name")] = values[0].get("value")
        return {name: result.get(name) for name in metric_names}
    except (httpx.RequestError, ValueError, TypeError) as exc:
        log.warning("Metrics fetch failed for %s: %s", post_id, exc)
        return None


def sync_ledger_metrics(data, max_fetch=40):
    """Backfill missing engagement metrics into posted ledger (oldest gaps last).

    Returns (updated, fetched_total, failed). Bounded per run to keep Graph API
    cost sane; rows keep their None until a later run fills them.
    """
    if DRY_RUN or not THREADS_TOKEN:
        return 0, 0, 0
    candidates = [t for t in data.get("topics", [])
                  if t.get("post_id") and t.get("views") is None and t.get("posted")]
    # Records without a posted timestamp are legacy rows whose Threads posts
    # are gone (Graph API 400 object-not-exist); skip them so each run does not
    # burn API calls on permanently-unfetchable metrics.
    # Fill newest gaps first so the freshest feedback enters scoring soonest.
    candidates.sort(key=lambda t: t.get("timestamp") or "", reverse=True)
    updated = fetched_total = failed = 0
    for topic in candidates[:max_fetch]:
        metrics = _fetch_engagement_metrics(topic.get("post_id"))
        fetched_total += 1
        if metrics is None:
            failed += 1
            continue
        changed = False
        for key in ("views", "likes", "replies", "reposts", "quotes"):
            if metrics.get(key) is not None and topic.get(key) is None:
                topic[key] = metrics[key]
                changed = True
        if changed:
            updated += 1
    if updated:
        save_data(data)
    return updated, fetched_total, failed


def performance_medians(data):
    """Compute median views per pattern/lane from measured rows. Empty dicts on no data."""
    stats = {"pattern_avg": {}, "lane_avg": {}}
    pattern_buckets = {}
    lane_buckets = {}
    for topic in data.get("topics", []):
        views = topic.get("views")
        if views is None:
            continue
        pattern_buckets.setdefault(topic.get("pattern") or "unknown", []).append(views)
        lane_buckets.setdefault(topic.get("lane") or "unknown", []).append(views)
    for target, buckets in (("pattern_avg", pattern_buckets), ("lane_avg", lane_buckets)):
        for key, values in buckets.items():
            values.sort()
            mid = len(values) // 2
            stats[target][key] = (values[mid] if len(values) % 2
                                  else (values[mid - 1] + values[mid]) / 2)
    return stats


def _international_indonesia_penalty(title, body):
    """Strong selection demotion for the international_indonesia lane.
    Ledger 2026-08-19: median 153 views (n=5, max 739) vs national 568 (max 41K).
    Keeps lane eligible (gates unchanged) but ranks it below national policy stories."""
    return -30 if _story_lane(title, body) == "international_indonesia" else 0


def _performance_bias(article, stats, max_bonus=10):
    """Small selection bias toward historically strong pattern/lane. Never overrides gates."""
    if not stats:
        return 0
    bonuses = []
    pattern_avg = (stats.get("pattern_avg") or {}).get(article.get("pattern") or "unknown")
    lane_avg = (stats.get("lane_avg") or {}).get(article.get("lane") or "unknown")
    if pattern_avg is not None:
        bonuses.append(max(-max_bonus, min(max_bonus, pattern_avg / 200)))
    if lane_avg is not None:
        bonuses.append(max(-max_bonus, min(max_bonus, lane_avg / 200)))
    return int(round(max(-max_bonus, min(max_bonus, sum(bonuses))))) if bonuses else 0


def threads_permalink(post_id):
    """Resolve canonical Threads URL without blocking successful state persistence."""
    try:
        r = httpx.get(f"{GRAPH}/{post_id}", params={"fields": "permalink", "access_token": THREADS_TOKEN}, timeout=10)
        if r.status_code == 200 and r.json().get("permalink"):
            return r.json()["permalink"]
    except Exception as e:
        log.warning(f"Permalink lookup failed: {e}")
    return f"https://www.threads.com/@ryanhadiii/post/{post_id}"


def _topic_entities(title):
    """Extract named economy entities; generic market terms are not repeat keys."""
    text = title.lower()
    aliases = {
        "mbg": ("mbg", "makan bergizi gratis"), "mk": ("putusan mk", "mahkamah konstitusi"),
        "pertamina": ("pertamina",), "danantara": ("danantara", "bpi danantara"),
        "bumn": ("bumn", "badan usaha milik negara"), "pppk": ("pppk",),
        "purbaya": ("purbaya",), "prabowo": ("prabowo",), "airlangga": ("airlangga",),
        "ojk": ("ojk", "otoritas jasa keuangan"), "bei": ("bei", "bursa efek indonesia"),
        "apbn": ("apbn",), "spbu": ("spbu",), "bi": ("bi rate", "bank indonesia"),
        "anggaran-pendidikan": ("anggaran pendidikan", "dana pendidikan"),
    }
    return {name for name, words in aliases.items() if any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words)}


def _title_words(title):
    stop = {"yang", "dan", "dari", "untuk", "dengan", "soal", "ini", "itu", "di", "ke", "pada", "buat", "akan", "sudah", "baru", "buka", "suara"}
    return {word for word in re.findall(r"[a-z0-9]{4,}", title.lower()) if word not in stop}


ISSUE_TERMS = (
    "subsidi", "mbg", "bansos", "hilirisasi", "apbn", "apbd", "bumn", "danantara",
    "pajak", "tarif", "utang", "anggaran", "dividen", "akuisisi", "merger", "ipo",
    "rights issue", "phk", "upah", "gaji", "pangan", "bbm", "rups", "kredit",
)

ISSUE_TERM_ALIASES = {
    "subsidi": ("subsidi",), "mbg": ("mbg", "makan bergizi gratis"),
    "bansos": ("bansos", "bantuan sosial"), "hilirisasi": ("hilirisasi",),
    "apbn": ("apbn",), "apbd": ("apbd",), "bumn": ("bumn", "badan usaha milik negara"),
    "danantara": ("danantara", "bpi danantara"), "pajak": ("pajak",), "tarif": ("tarif",),
    "utang": ("utang", "hutang"), "anggaran": ("anggaran",), "dividen": ("dividen",),
    "akuisisi": ("akuisisi",), "merger": ("merger",), "ipo": ("ipo",),
    "rights_issue": ("rights issue",), "phk": ("phk", "pemutusan hubungan kerja"),
    "upah": ("upah",), "gaji": ("gaji",), "pangan": ("pangan",), "bbm": ("bbm",),
    "rups": ("rups",), "kredit": ("kredit",),
}


def _issue_terms(title):
    text = title.lower()
    return {
        issue for issue, aliases in ISSUE_TERM_ALIASES.items()
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) for alias in aliases)
    }


EVENT_ALIASES = {
    "announce": ("umumkan", "mengumumkan", "ungkap", "mengungkap", "tegas", "tegaskan", "target", "bidik", "rencana"),
    "change": ("ubah", "mengubah", "revisi", "naikkan", "turunkan", "menaikkan", "menurunkan", "penyesuaian"),
    "enforce": ("kejar", "mengejar", "tagih", "menagih", "tertib", "penertiban", "larang", "melarang"),
    "approve": ("setujui", "menyetujui", "sahkan", "mengesahkan", "tetapkan", "menetapkan"),
    "hold": ("tahan", "menahan", "pertahankan", "mempertahankan", "tetap"),
    "fund": ("salurkan", "menyalurkan", "alokasikan", "mengalokasikan", "siapkan", "menyiapkan"),
    "acquire": ("akuisisi", "mengakuisisi", "merger", "menggabungkan"),
}


def _event_terms(title):
    text = title.lower()
    return {
        event for event, aliases in EVENT_ALIASES.items()
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) for alias in aliases)
    }


def _title_numbers(title):
    return set(re.findall(r"\d+(?:[.,]\d+)?", title.lower()))


# ── HTTP Helpers ─────────────────────────────────────────────────────────────

UA = "Mozilla/5.0"

def _http_get(url, timeout=12):
    """GET with one immediate fallback retry for flaky official pages."""
    for attempt in range(2):
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
            if r.status_code == 200:
                return r.status_code, r.text
        except Exception:
            pass
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return r.status, r.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt == 1:
                return 0, ""
    return 0, ""


def _canonical_url(url):
    """Drop tracking parameters so one article has one posted-state key."""
    if not isinstance(url, str):
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))

# ── RSS Scraping ─────────────────────────────────────────────────────────────

def _title_is_junk(title):
    """Reject non-article titles: tickers, video prefixes, short codes, navigation."""
    t = title.strip()
    if len(t) < 20:
        return True
    # Market widgets / tickers
    if re.match(r'^[A-Z]{2,5}\d[\d,.]+\s*[-+]\d', t):
        return True
    if any(kw in t.lower() for kw in ("video:", "foto:", "live:", "infografis:", "live report")):
        return True
    # CNBC junk patterns
    if re.search(r'Market\d+\s+(jam|menit|detik)\s+yang\s+lalu', t, re.I):
        return True
    # Pure number-only titles
    if re.match(r'^[\d,.\s%+\-]+$', t):
        return True
    return False


def _scrape_rss(url, source, base_score):
    """Parse RSS feed → article dicts."""
    articles = []
    try:
        code, text = _http_get(url)
        if code != 200:
            return articles
        # Some feeds contain stray control bytes; discard only invalid XML chars.
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        root = ET.fromstring(text)
        ns = {"media": "http://search.yahoo.com/mrss/",
              "content": "http://purl.org/rss/1.0/modules/content/"}
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            link = _canonical_url((item.findtext("link") or "").strip())
            pub_str = item.findtext("pubDate") or ""
            pub_ts = 0
            if pub_str:
                try:
                    pub_ts = parsedate_to_datetime(pub_str).timestamp()
                except Exception:
                    pub_ts = time.time()
            # og:image from RSS media:content or media:thumbnail
            og_image = None
            mc = item.find("media:content", ns)
            if mc is None or not mc.get("url"):
                mc = item.find("media:thumbnail", ns)
            if mc is not None:
                og_image = mc.get("url")
            if not title or not link or "/live/" in link or "/liveblog/" in link:
                continue
            if _title_is_junk(title):
                continue
            articles.append({
                "title": title, "url": link, "source": source,
                "score": base_score, "ts": pub_ts, "og_image": og_image,
            })
    except Exception as e:
        log.warning(f"RSS {source}: {e}")
    return articles

# ── HTML Scraping ────────────────────────────────────────────────────────────

def _scrape_html(url, source, base_score, domain):
    """Scrape front page HTML for article links."""
    articles = []
    try:
        code, text = _http_get(url)
        if code != 200:
            return articles
        soup = BeautifulSoup(text, "html.parser")
        seen = set()
        for a_tag in soup.find_all("a", href=True):
            href = _canonical_url(urllib.parse.urljoin(url, str(a_tag["href"]).strip()))
            if domain not in href:
                continue
            if "/live/" in href or "/liveblog/" in href:
                continue
            title = a_tag.get_text(strip=True)
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 25 or _title_is_junk(title):
                continue
            if href in seen:
                continue
            seen.add(href)
            articles.append({
                "title": title, "url": href, "source": source,
                "score": base_score, "ts": time.time(), "og_image": None,
            })
    except Exception as e:
        log.warning(f"HTML {source}: {e}")
    # Dedup by title
    seen_titles = set()
    deduped = []
    for a in articles:
        sig = a["title"][:40].lower().strip()
        if sig not in seen_titles:
            seen_titles.add(sig)
            deduped.append(a)
    return deduped[:15]

# ── Scrape All Sources ───────────────────────────────────────────────────────

def scrape_all():
    """Scrape all sources in parallel. Returns sorted article list."""
    articles = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut_map = {}
        for name, cfg in SOURCES.items():
            if cfg["type"] == "rss":
                f = ex.submit(_scrape_rss, cfg["url"], name, cfg["score"])
            else:
                f = ex.submit(_scrape_html, cfg["url"], name, cfg["score"], cfg["domain"])
            fut_map[f] = name
        for f in as_completed(fut_map):
            try:
                articles.extend(f.result())
            except Exception as e:
                log.warning(f"Scrape {fut_map[f]}: {e}")
    # Filter mixed/homepage and source-specific noise before the source cap.
    articles = [a for a in articles
                if (_has_economy_title_signal(a["title"])
                    or a["source"] in {"cnbc_global", "bbc_business"})
                and _has_source_title_signal(a["title"], a["source"])]
    from collections import defaultdict
    by_source = defaultdict(list)
    for a in articles:
        by_source[a["source"]].append(a)
    deduped = []
    for source, src_articles in by_source.items():
        src_articles.sort(key=lambda a: (a["ts"], len(a["title"])), reverse=True)
        cap = SOURCE_ARTICLE_CAPS.get(source, MAX_ARTICLES_PER_SOURCE)
        deduped.extend(src_articles[:cap])
    deduped.sort(key=lambda a: (a["ts"], len(a["title"])), reverse=True)
    log.info(f"  Articles: {len(deduped[:SCRAPE_ARTICLE_LIMIT])} economy-title candidates after scrape cap")
    return deduped[:SCRAPE_ARTICLE_LIMIT]

# ── Economy Relevance Scoring ────────────────────────────────────────────────

def _matches_keyword(text, keyword):
    """Match whole terms; prevent substring false positives (e.g. artis/partisipasi)."""
    keyword = str(keyword).strip().lower()
    return bool(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text.lower()))


def _score_article(article):
    """Score article using new category-based system.
    Returns (score, reason) tuple where reason is short description.
    """
    title = article.get("title", "")
    tl = title.lower()
    score = 0
    signals = 0
    categories_hit = 0

    # Hard reject (instant fail)
    for kw in HARD_REJECT:
        if _matches_keyword(tl, kw):
            return (0, "hard_reject:" + kw.strip())
    for name in NAMED_BLACKLIST:
        if _matches_keyword(tl, name):
            return (0, "blacklist:" + name)
    # Video reject — skip before body fetch/LLM
    if tl.startswith("video:") or "/video-" in article.get("url", ""):
        return (0, "video_article")
    if (not _has_economy_title_signal(title)
            and article.get("source") not in {"cnbc_global", "bbc_business"}):
        return (0, "out_of_scope")
    # Category scoring — one keyword counted once per category
    for cat_entry in SCORE_CATEGORIES:
        cat_name = cat_entry.get("name", "") if isinstance(cat_entry, dict) else cat_entry[0]
        cat_max = cat_entry.get("max_score", 0) if isinstance(cat_entry, dict) else cat_entry[1]
        cat_kws = cat_entry.get("keywords", []) if isinstance(cat_entry, dict) else cat_entry[2]
        cat_score = 0
        for kw in cat_kws:
            if _matches_keyword(tl, kw):
                cat_score += cat_max  # flat score per category, not per keyword
                signals += 1
                categories_hit += 1
                break
        score += min(cat_score, cat_max)

    # Entity boost
    tl_full = tl
    for tier_name, tier_data in ENTITY_BOOST.items():
        boost_val = tier_data.get("score", 0) if isinstance(tier_data, dict) else tier_data[0]
        entities = tier_data.get("names", []) if isinstance(tier_data, dict) else tier_data[1]
        for ent in entities:
            if _matches_keyword(tl_full, ent):
                score += boost_val
                signals += 1
                break

    # Bonus angka besar
    max_val = _parse_number_in_title(title)
    if max_val > 0:
        for bonus_entry in NUMBER_BONUS:
            lo = bonus_entry.get("min", 0) if isinstance(bonus_entry, dict) else bonus_entry[0]
            hi = bonus_entry.get("max", float('inf')) if isinstance(bonus_entry, dict) else bonus_entry[1]
            bonus = bonus_entry.get("score", 0) if isinstance(bonus_entry, dict) else bonus_entry[2]
            if lo <= max_val <= (hi or float('inf')):
                score += bonus
                break

    # Percentage bonus
    if '%' in title:
        score += 3

    # ponytail: dynamic keyword overlay retired; add back only with a maintained source and regression coverage.
    dynamic_hits = 0


    # Daily market moves are low-value unless title also signals policy or public impact.
    technical = any(_matches_keyword(tl, kw) for kw in ("rupiah", "ihsg", "saham", "harga emas", "harga minyak"))
    public_angle = any(_matches_keyword(tl, kw) for kw in ("kebijakan", "bi", "bank indonesia", "apbn", "pajak", "subsidi", "anggaran", "berlaku", "ditetapkan", "kenapa", "penyebab", "alasannya"))
    if technical and not public_angle:
        score -= 30

    # Routine SPBU price lists are utility updates, not Techbro analysis topics.
    # Keep structural fuel-policy stories such as B50 or subsidy changes eligible.
    routine_bbm = "bbm" in tl and "spbu" in tl
    fuel_policy = any(_matches_keyword(tl, kw) for kw in ("b50", "subsidi", "kebijakan", "aturan", "kuota", "alokasi", "apbn"))
    if routine_bbm and not fuel_policy:
        score -= 100

    # Kontras harga vs daya beli: high-performer pattern (UMR vs Greenland = 18K views).
    # Boost stories that contrast a price/cost figure against purchasing power.
    price_signal = any(_matches_keyword(tl, kw) for kw in ("harga", "biaya", "tarif", "rupiah"))
    wallet_signal = any(_matches_keyword(tl, kw) for kw in ("gaji", "upah", "umr", "ump", "daya beli", "pendapatan"))
    if price_signal and wallet_signal:
        score += 15  # price-vs-income contrast = proven virality catalyst

    # Concrete decision-maker + number + public consequence = strongest local hook.
    actor = bool(re.search(r"\b(prabowo|jokowi|menteri|gubernur|kemenkeu|ojk|danantara|bumn|pemerintah)\b", tl))
    consequence = any(_matches_keyword(tl, kw) for kw in ("beban", "gaji", "upah", "subsidi", "pajak", "apbn", "daya beli", "phk", "investasi"))
    if actor and max_val > 0 and consequence:
        score += 10

    # ── Editorial mix: amplify concrete numbers/household impact, demote explainer ──
    # number_shock: explicit price figure carrying a number = shock catalyst.
    if max_val > 0 and price_signal:
        score += 8
    # household_impact: income-side pressure (gaji/upah/umr) without a price word.
    if wallet_signal and not price_signal:
        score += 6
    # finance_practical demotion: how-to/tips/procedural headlines (weak hook, med 361 views).
    explainer = any(_matches_keyword(tl, kw) for kw in (
        "cara mudah", "tips", "trik", "kenali", "simak", "panduan", "apa itu",
        "tutorial", "begini cara", "yuk", "strategi investasi"))
    if explainer and signals < 2:
        score -= 25

    # Soft reject penalty (cancelled by sufficient economy signals)
    if signals >= 2:
        pass  # strong signals override soft reject
    else:
        for kw in SOFT_REJECT:
            if _matches_keyword(tl, kw):
                score -= 60
                break

    return (score, f"cats={categories_hit} sig={signals} dyn={dynamic_hits}")

def _source_diversity_penalty(data, source):
    """Bound recent source repetition; never override body/editorial gates."""
    recent = [topic for topic in (data or {}).get("topics", [])[:10]
              if _is_current_topic(topic)]
    count = sum(topic.get("article_source") == source for topic in recent)
    return -min(count * 3, 12)


def _hot_topic_cluster(title, pattern):
    """Cluster same entity + issue/event; different decisions stay available."""
    entities = sorted(_topic_entities(title))
    issues = sorted(_issue_terms(title))
    events = sorted(_event_terms(title))
    numbers = sorted(_title_numbers(title))
    if entities and (issues or events or numbers):
        return "/".join(entities + issues + events + numbers)
    if entities:
        return "/".join(entities)
    words = sorted(_title_words(title))[:4]
    return "/".join(words) or (pattern or "other").lower()


def _indonesia_topic_relevance(title, body):
    """Classify domestic relevance; material global economy stories may stand alone."""
    text = f"{title} {body}".lower()
    global_story = _is_global_event(title, body)
    global_finance = _is_global_finance_story(title, body)
    impact_channel = _international_impact_channel(title, body)
    indonesia = bool(re.search(r"\b(indonesia|ri|rupiah|apbn|bank indonesia|bi|kemenkeu|ojk|ikn|ibu kota nusantara)\b", f"{title} {body}", re.I))
    national_actor = bool(re.search(
        r"\b(pemerintah|menteri|kementerian|dpr|bpk|bumn|apbd|gubernur|"
        r"presiden|mahkamah konstitusi|kpk|dprd|ikn|ibu kota nusantara)\b", f"{title} {body}", re.I,
    ))
    if global_story and global_finance:
        if indonesia and impact_channel:
            return "global_indonesia_impact"
        return "international"
    return "national" if indonesia or national_actor else None


GLOBAL_EVENT_RE = re.compile(
    r"\b(federal reserve|the fed|ecb|bank of japan|boj|pboc|opec|minyak dunia|"
    r"tarif dagang|perang dagang|sanksi ekonomi|resesi global|ekonomi global|"
    r"perdagangan global|selat hormuz|hormuz|iran|timur tengah|donald trump|trump|"
    r"amerika serikat|united states|u\.s\.|us|american|america|global|international|"
    r"foreign|overseas|china|tiongkok|jepang|eropa|wall street|"
    r"pasar global|global market|investor global|global stocks|us stocks|oil prices|"
    r"interest rates|trade war|earnings|revenue|profit|acquisition|merger|ipo|"
    r"investment|investor|holding|stake|stocks|stock market|bond yield)\b", re.I,
)
INTERNATIONAL_CHANNELS = {
    "energy": r"bbm|minyak|energi|fuel|oil|harga energi",
    "trade": r"ekspor|impor|tarif|bea masuk|perdagangan|industri|export|import|trade",
    "monetary": r"rupiah|inflasi|suku bunga|biaya pembiayaan|financing costs|daya beli",
    "fiscal": r"apbn|subsidi|penerimaan negara|belanja negara|defisit",
    "investment": r"investasi|arus modal|pasar keuangan|investment|capital flow",
}
IMPACT_RELATION_RE = re.compile(
    r"\b(dampak|berdampak|berisiko|risiko|menekan|tekanan|mendorong|memicu|"
    r"mengerek|menaikkan|menurunkan|akibat|karena|sehingga|pengaruh|terdampak|"
    r"impact|affecting|affected|risk|could affect|would affect)\b", re.I,
)


def _is_global_event(title, body=""):
    return bool(GLOBAL_EVENT_RE.search(f"{title} {body}"))


def _international_impact_channel(title, body):
    """Return source-backed Indonesia impact lane; no event/channel = no global story."""
    if not _is_global_event(title, body):
        return None
    id_re = re.compile(r"\b(indonesia|indonesian|ri|rupiah|apbn|bank indonesia|"
                       r"kemenkeu|pemerintah indonesia|industri dalam negeri)\b", re.I)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body or "") if s.strip()]
    for sentence in sentences:
        if not id_re.search(sentence) or not IMPACT_RELATION_RE.search(sentence):
            continue
        for channel, terms in INTERNATIONAL_CHANNELS.items():
            if re.search(rf"\b(?:{terms})\b", sentence, re.I):
                return channel
    for index, sentence in enumerate(sentences):
        window = " ".join(sentences[index:index + 2])
        if id_re.search(window) and IMPACT_RELATION_RE.search(window) and GLOBAL_EVENT_RE.search(window):
            for channel, terms in INTERNATIONAL_CHANNELS.items():
                if re.search(rf"\b(?:{terms})\b", window, re.I):
                    return channel
    return None


def _story_lane(title, body=""):
    if _international_impact_channel(title, body):
        return "international_indonesia"
    return "international" if _is_global_finance_story(title, body) else "national"


def _is_administrative_distribution_story(title, body):
    """Reject subsidy logistics unless article contains material economic change."""
    text = f"{title} {body}".lower()
    operational = bool(re.search(
        r"\b(penyaluran|pendataan|verifikasi|validasi|tepat sasaran|"
        r"data penerima|identitas kependudukan|nik|distribusi|penertiban)\b", text,
    ))
    subsidy_context = bool(re.search(r"\b(subsidi|elpiji|lpg|bansos|bantuan sosial)\b", text))
    material_change = bool(re.search(
        r"\b(naik|turun|ubah|diubah|menetapkan harga|harga eceran tertinggi|"
        r"anggaran|kuota|nilai bantuan|nominal bantuan|syarat penerima|"
        r"kelompok penerima|biaya rumah tangga|daya beli|tarif|aturan baru|"
        r"beban rumah tangga|pengeluaran rumah tangga)\b", text,
    ))
    return operational and subsidy_context and not material_change


def _verify_one(candidate, now, data=None):
    """Fetch body and rank candidate; editorial gate is metadata, not discovery filter."""
    title, url, source = candidate.get("title", ""), candidate.get("url", ""), candidate.get("source", "")
    if not title or not url or not source:
        return None
    title_lower = title.lower()
    if re.search(r"\b(perang|konflik|serangan|militer|rudal|gencatan senjata|memanas)\b", title_lower) and not any(
        term in title_lower for term in ("tarif", "sanksi", "minyak", "energi", "dagang", "ekonomi", "inflasi", "pangan", "ekspor", "impor", "pasar", "harga", "investasi", "bursa")
    ):
        return None
    if re.search(r"\b(stock picks?|dividend stocks?|analysts? like|steady income|unloved stock)\b", title_lower):
        return None
    body, image, article_ts = _fetch_article_body(url)
    if not body or len(body) < 500:
        return None
    published_ts, timestamp_source, _ = _resolve_published_timestamp(article_ts, candidate.get("ts", 0), now)
    if not published_ts:
        return None
    eligible, reason = _is_eligible_candidate(title, body, source)
    indonesia_relevance = _indonesia_topic_relevance(title, body)
    pattern, confidence = _classify_pattern(title, body)
    topic_score, economy_score, impact_score = _topic_score(title, body)
    source_quality = SOURCES.get(source, {}).get("score", candidate.get("score", 0))
    freshness = max(0.0, 24 - ((now - published_ts) / 3600)) / 24
    _, arc, hook = _content_metadata(title, body)
    lane = _story_lane(title, body)
    lens = _editorial_lens(title, body)
    story_selection = _story_selection_bonus(title, body)
    # Rank body-backed editorially valid economy stories first. Title-only score
    # previously let corporate/event noise consume the finite discovery pool.
    quality = 100 if eligible else -40
    material = 20 if has_material_economic_signal(title, body) else -20
    hot_score = round(quality + material + topic_score * 10 + confidence * 10 + freshness * 10 + source_quality
                      + _engagement_priority_bonus(title, body) + story_selection
                      + _international_indonesia_penalty(title, body), 3)
    image_provenance = _image_provenance(url, image, declared_on_page=bool(image))
    _IMAGE_PROVENANCE_CACHE[_canonical_url(url)] = image_provenance
    return {
        "cluster": _hot_topic_cluster(title, pattern), "title": title,
        "canonical_url": _canonical_url(url), "source": source,
        "published_ts": published_ts, "timestamp_source": timestamp_source,
        "pattern": pattern, "pattern_confidence": round(confidence, 3),
        "arc": arc, "hook_pattern": hook, "lane": lane, "editorial_lens": lens,
        "topic_score": topic_score, "economy_score": economy_score, "impact_score": impact_score,
        "story_selection_score": story_selection,
        "hot_score": hot_score, "body_verified": True, "image_available": bool(image),
        "image_provenance": image_provenance,
        "indonesia_relevance": indonesia_relevance, "reason": reason,
        "editorial_eligible": eligible, "editorial_reason": reason,
        "has_material_economic_signal": has_material_economic_signal(title, body),
        "impact_channel": _international_impact_channel(title, body),
        "global_event": _is_global_event(title, body),
        "_body": body, "_image": image,
    }

def scout_hot_topics(articles, now=None, limit=HOT_TOPIC_LIMIT, per_source_limit=2, data=None,
                     allow_cluster_repeats=False):
    """Body-first ranked discovery; editorial gate runs at selection boundary."""
    now = time.time() if now is None else now
    verified = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut_map = {}
        for candidate in articles:
            title, url, source = candidate.get("title", ""), candidate.get("url", ""), candidate.get("source", "")
            if not title or not url or not source:
                continue
            fut_map[ex.submit(_verify_one, candidate, now, data)] = candidate
        for f in as_completed(fut_map):
            try:
                item = f.result()
                if item:
                    verified.append(item)
            except Exception as e:
                log.warning(f"Body verify failed: {e}")
    # Futures finish nondeterministically; preserve feed order on equal scores.
    feed_order = {_canonical_url(a.get("url", "")): i for i, a in enumerate(articles)}
    verified.sort(key=lambda item: (-item["hot_score"], feed_order.get(item["canonical_url"], len(articles))))
    selected, sources, clusters = [], {}, set()
    for item in verified:
        if item["source"] in sources and sources[item["source"]] >= per_source_limit:
            continue
        if not allow_cluster_repeats and item["cluster"] in clusters:
            continue
        sources[item["source"]] = sources.get(item["source"], 0) + 1
        clusters.add(item["cluster"])
        item["rank"] = len(selected) + 1
        selected.append(item)
        if len(selected) == limit:
            break
    return selected



def _ranked_candidate_pool(articles, topics, limit=CANDIDATE_POOL_LIMIT):
    """Keep top-ranked topics; preserve rank and copy verified bodies."""
    by_url = {_canonical_url(article.get("url", "")): article for article in articles}
    selected, seen = [], set()
    for topic in topics:
        url = _canonical_url(topic.get("canonical_url", ""))
        if not url or url in seen or url not in by_url:
            continue
        article = by_url[url]
        if topic.get("_body"):
            article["body"] = topic["_body"]
        article["candidate_rank"] = len(selected) + 1
        article["hot_score"] = topic.get("hot_score")
        selected.append(article)
        seen.add(url)
        if len(selected) == limit:
            break
    return selected


def _count_exact_posted_candidates(urls, posted_urls):
    """Count ranked discovery URLs already present in posted ledger."""
    posted = {_canonical_url(url) for url in posted_urls}
    return sum(_canonical_url(url) in posted for url in urls)


def _pick_article(articles, posted_urls, data=None, ranked_urls=None):
    """Pick next ranked unused article; learning adjusts only unbounded pools."""
    now = time.time()
    posted_canonical = {_canonical_url(url) for url in posted_urls}
    rank_order = {_canonical_url(url): i for i, url in enumerate(ranked_urls or ())}
    candidates = [
        a for a in articles
        if _canonical_url(a.get("url", "")) not in posted_canonical
        and (not ranked_urls or _canonical_url(a.get("url", "")) in rank_order)
    ]
    if not candidates:
        return None

    # Clean title
    for a in candidates:
        a["title"] = re.sub(r'^\d+', '', a["title"]).strip()
        a["title"] = re.sub(r'(Energi|Ekbis|Bisnis|Keuangan|Finance|Ekonomi|Nasional|Market)\d{2}/\d{2}/\d{4}$', '', a["title"]).strip()
        a["title"] = re.sub(r'(Energi|Ekbis|Bisnis|Keuangan|Finance|Ekonomi|Nasional)$', '', a["title"]).strip()
    # Pressbox pattern: rank body-verified discovery candidates first. Full
    # editorial eligibility is checked by main() before generation.
    perf_stats = performance_medians(data) if data else {}
    for a in candidates:
        eco_score, reason = _score_article(a)
        a["eco_score"] = eco_score
        a["_reason"] = reason
        # Freshness: 24h = +15, 25-48h = +10, 3-7d = +5, >7d = 0, republish = -30
        age_hours = (now - a["ts"]) / 3600
        if age_hours <= 24:
            freshness = 15
        elif age_hours <= 48:
            freshness = 10
        elif age_hours <= 168:  # 7 days
            freshness = 5
        else:
            freshness = 0
        # Indonesia relevance: default +10 for local sources
        relevance = 10
        # Source quality: base from SOURCES config
        src_cfg = SOURCES.get(a["source"], {})
        source_quality = src_cfg.get("score", 5)
        _, arc, hook = _content_metadata(a.get("title", ""), a.get("body", ""))
        a["arc"] = a.get("arc") or arc
        a["hook_pattern"] = a.get("hook_pattern") or hook
        a["lane"] = _story_lane(a.get("title", ""), a.get("body", ""))
        a["editorial_lens"] = _editorial_lens(a.get("title", ""), a.get("body", ""))
        a["impact_channel"] = _international_impact_channel(a.get("title", ""), a.get("body", ""))
        a["story_selection_score"] = _story_selection_bonus(a.get("title", ""), a.get("body", ""))
        a["_weight"] = (eco_score + freshness + relevance + source_quality
                         + _engagement_priority_bonus(a.get("title", ""), a.get("body", ""))
                         + a["story_selection_score"]
                         + _international_indonesia_penalty(a.get("title", ""), a.get("body", ""))
                         + _source_diversity_penalty(data, a["source"])
                         + _performance_bias(a, perf_stats))
    if ranked_urls:
        candidates.sort(key=lambda a: rank_order[_canonical_url(a["url"])])
    else:
        candidates.sort(key=lambda a: a["_weight"], reverse=True)
    log.debug("Top 5:")
    for i, a in enumerate(candidates[:5]):
        log.debug(f"  {i+1}. [s={a['eco_score']}] {a['title'][:60]} (w={a['_weight']})")
    # ponytail: title ranks only. Body/editorial/image gates decide publishability.
    return candidates[0]

# ── Article Body + Image ─────────────────────────────────────────────────────

def _hd_image_url(url):
    """Request a 1200px rendition from known CDN URLs."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    if any(host in parts.netloc for host in ("akcdn.detik.net.id", "awsimages.detik.net.id", "cdn.detik.net.id")):
        query["w"] = "1200"
        query["q"] = "90"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    urllib.parse.urlencode(query), parts.fragment))


def _image_size(data):
    """Return JPEG/PNG dimensions without adding an image dependency."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        return None
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xff:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            continue
        size = struct.unpack(">H", data[pos:pos + 2])[0]
        if 0xc0 <= marker <= 0xc3 or 0xc5 <= marker <= 0xc7 or 0xc9 <= marker <= 0xcb or 0xcd <= marker <= 0xcf:
            return struct.unpack(">HH", data[pos + 3:pos + 7])[::-1]
        pos += size
    return None


def _image_provenance(article_url, image_url, declared_on_page=False):
    """Record image provenance; page-declared image beats CDN host heuristics."""
    if not image_url:
        return {"status": "missing", "source_page": article_url or "", "image_url": ""}
    article_host = urllib.parse.urlsplit(article_url or "").netloc.lower().removeprefix("www.")
    image_host = urllib.parse.urlsplit(image_url).netloc.lower().removeprefix("www.")
    same_publisher = bool(article_host and (image_host == article_host or image_host.endswith("." + article_host)))
    status = "page_declared" if declared_on_page else ("same_publisher" if same_publisher else "foreign_host")
    return {
        "status": status,
        "source_page": article_url or "",
        "image_url": image_url,
        "same_publisher": same_publisher,
        "declared_on_page": bool(declared_on_page),
    }


def _publishable_image(article, image_url):
    """Return image only when tied to canonical article or publisher domain."""
    if not image_url:
        return None
    article_url = article.get("url", article.get("canonical_url", ""))
    provenance = article.get("image_provenance")
    if not provenance or provenance.get("image_url") != image_url:
        cached = _IMAGE_PROVENANCE_CACHE.get(_canonical_url(article_url))
        provenance = cached if cached and cached.get("image_url") == image_url else None
    provenance = provenance or _image_provenance(article_url, image_url)
    return image_url if provenance.get("status") in {"page_declared", "same_publisher"} else None


def validate_article_image(url):
    """Require a real HD article lead image; tolerate 1px CDN rounding."""
    try:
        response = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        content_type = response.headers.get("content-type", "") if hasattr(response, "headers") else ""
        if content_type and not content_type.lower().startswith("image/"):
            log.warning(f"Reject non-image article image: {content_type} {url[:80]}")
            return None
        size = _image_size(response.content) if response.status_code == 200 else None
        if size and size[0] >= 800 and size[1] >= 450:
            return url
        log.warning(f"Reject non-HD article image: {size or 'unknown'} {url[:80]}")
    except httpx.RequestError as e:
        log.warning(f"Validate article image failed: {e}")
    return None

def _image_hint(url):
    """Extract a short visual hint from image URL path for S1 hook context."""
    if not url:
        return ""
    try:
        path = urllib.parse.urlsplit(url).path.lower()
        hints = []
        for kw, label in [
            ("ilustrasi", "ilustrasi"), ("pasar", "pasar"), ("pabrik", "pabrik"),
            ("buruh", "buruh/pekerja"), ("petani", "petani"), ("nelayan", "nelayan"),
            ("jalan", "jalan raya"), ("pelabuhan", "pelabuhan"), ("tambang", "tambang"),
            ("perkantoran", "kantor"), ("uang", "uang/transaksi"), ("bank", "bank"),
            ("menteri", "pejabat"), ("presiden", "presiden"), ("rapat", "rapat"),
            ("beras", "beras/pangan"), ("bbm", "BBM/SPBU"),
            ("listrik", "listrik/PLN"), ("sekolah", "sekolah"), ("rumah", "perumahan"),
        ]:
            if kw in path:
                hints.append(label)
        return ", ".join(hints[:3]) if hints else "foto berita"
    except Exception:
        return ""


def _published_timestamp(soup):
    """Read standard article publication fields; unknown formats remain untrusted."""
    values = [
        tag.get("content") for tag in soup.find_all("meta")
        if re.search(r"(?:publishdate|datepublished|pubdate|published_time)",
                     str(tag.get("name") or tag.get("property") or tag.get("itemprop") or ""), re.I)
    ]
    values += [tag.get("content") for tag in soup.find_all(attrs={"itemprop": re.compile(r"datePublished|dateCreated", re.I)})]
    values += [tag.get("datetime") for tag in soup.find_all("time")]
    for tag in soup.find_all(attrs={"data-published": True}):
        values.extend((tag.get("data-published"), tag.get("data-published-at")))
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue

        def collect_dates(value):
            if isinstance(value, dict):
                if value.get("datePublished"):
                    values.append(value["datePublished"])
                for child in value.values():
                    collect_dates(child)
            elif isinstance(value, list):
                for child in value:
                    collect_dates(child)

        collect_dates(data)
    for value in values:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=WIB)).timestamp()
        except ValueError:
            try:
                return parsedate_to_datetime(str(value)).timestamp()
            except (TypeError, ValueError):
                try:
                    return datetime.strptime(str(value), "%Y/%m/%d %H:%M:%S").replace(tzinfo=WIB).timestamp()
                except ValueError:
                    pass
    return 0


def _resolve_published_timestamp(article_ts, rss_ts, now):
    """Use article time; bounded RSS fallback only when article time is absent."""
    if article_ts:
        if article_ts > now + 300:
            return 0, "article", "future"
        if now - article_ts > 86400:
            return 0, "article", "stale"
        return article_ts, "article", "ok"
    if rss_ts and rss_ts <= now + 300 and now - rss_ts <= 86400:
        return rss_ts, "rss_fallback", "ok"
    return 0, "missing", "missing"


# In-memory body/image provenance cache — avoids double-fetch between scout and main.
_BODY_CACHE = {}
_IMAGE_PROVENANCE_CACHE = {}

def _fetch_article_body(url):
    """Fetch article HTML, extract clean text + og:image + source publish time."""
    cache_key = _canonical_url(url)
    if cache_key in _BODY_CACHE:
        return _BODY_CACHE[cache_key]
    og_image = None
    published_ts = 0
    text = ""
    try:
        code, raw = _http_get(url, timeout=15)
        if code != 200:
            return "", None, 0
        soup = BeautifulSoup(raw, "html.parser")
        published_ts = _published_timestamp(soup)
        jsonld_body = ""
        jsonld_image = None
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                payload = json.loads(tag.string or tag.get_text())
            except (TypeError, json.JSONDecodeError):
                continue
            stack = payload if isinstance(payload, list) else [payload]
            while stack:
                item = stack.pop()
                if not isinstance(item, dict):
                    continue
                body_value = item.get("articleBody")
                if body_value and len(str(body_value)) > len(jsonld_body):
                    jsonld_body = html.unescape(str(body_value))
                if not jsonld_image and item.get("image"):
                    image = item["image"]
                    jsonld_image = image if isinstance(image, str) else (image.get("url") if isinstance(image, dict) else None)
                stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
        # og:image — logos are not lead images, fall through to body images.
        og_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og_tag and og_tag.get("content"):
            og_candidate = _hd_image_url(og_tag["content"])
            og_alt = og_tag.get("alt") or ""
            if not re.search(r"logo|favicon|icon|placeholder", og_candidate, re.I) and not re.search(r"logo|favicon|icon", og_alt, re.I):
                og_image = validate_article_image(og_candidate)
                # Some CDNs mirror og:image under a different path with a real alt on the <img>.
                # Reject if any <img> with the same filename is marked as a logo.
                if og_image:
                    og_stem = urllib.parse.urlsplit(og_image).path.rsplit("/", 1)[-1].split("?", 1)[0]
                    for img_tag in soup.find_all("img"):
                        img_alt = str(img_tag.get("alt") or "")
                        img_src = str(img_tag.get("src") or img_tag.get("data-src") or "")
                        if og_stem and og_stem in img_src and re.search(r"logo|favicon|icon|placeholder", img_alt, re.I):
                            log.warning(f"Reject logo-as-og:image: {og_stem} alt='{img_alt[:40]}'")
                            og_image = None
                            break
        if not og_image and jsonld_image:
            og_image = validate_article_image(_hd_image_url(jsonld_image))
        # Article body — CNBC uses generated class names not covered by local selectors.
        host = urllib.parse.urlsplit(url).netloc.lower()
        body_el = None
        if "cnbc.com" in host:
            body_el = soup.select_one(".ArticleBody-articleBody")
        # article body — try known ID selectors
        body_el = (
            body_el
            or soup.find("div", class_=lambda c: c and "detail__body-text" in c)
            or soup.find("div", class_=lambda c: c and "detail__body" in c)
            or soup.find("div", class_=lambda c: c and ("article-body" in c or "article_content" in c))
            or soup.find("div", class_=lambda c: c and "read__content" in c)
            or soup.find("div", class_=lambda c: c and "content-detail" in c)
            or soup.find("article")
            or soup.find("main")
        )
        if not body_el and jsonld_body:
            body_el = BeautifulSoup(
                f"<div><p>{html.escape(jsonld_body)}</p></div>", "html.parser"
            ).div
        if not body_el:
            return "", og_image, published_ts
        # Fallback: og:image was missing/logo — find the first real photo in the article body.
        if not og_image:
            for img_tag in body_el.find_all("img"):
                src = str(img_tag.get("src") or img_tag.get("data-src") or "")
                alt = str(img_tag.get("alt") or "")
                if not src or re.search(r"logo|favicon|icon|placeholder|avatar", src + " " + alt, re.I):
                    continue
                candidate = validate_article_image(_hd_image_url(src))
                if candidate:
                    og_image = candidate
                    break
        for tag in body_el.find_all(["script", "style", "nav", "aside", "footer", "form"]):
            tag.decompose()
        for tag in body_el.find_all(class_=lambda c: c and any(x in " ".join(c if isinstance(c, list) else [c]).lower() for x in ("related", "recommend", "read-more", "advert", "author", "share"))):
            tag.decompose()
        paras = []
        for p in body_el.find_all("p"):
            # Malformed publisher markup can nest paragraphs. Keep leaves only or each
            # outer paragraph duplicates all following article text.
            if p.find("p"):
                continue
            txt = p.get_text(separator=" ", strip=True)
            # Remove publisher UI noise before paragraph enters source body/evidence.
            txt = re.sub(r"(?i)^\s*scroll\s+to\s+continue\s+with\s+content\s*", "", txt).strip()
            txt = re.split(
                r"(?i)\b(?:ikuti\s+whatsapp channel|dapatkan akses cepat ke berita terkini|dapatkan pengalaman membaca lebih nyaman)\b",
                txt, maxsplit=1,
            )[0].strip()
            if re.search(r"(?i)^view this post on instagram\b", txt):
                continue
            if len(txt) > 20:
                paras.append(txt)
        if not paras:
            raw = body_el.get_text(separator="\n", strip=True)
            paras = [l.strip() for l in raw.split("\n") if len(l.strip()) > 40]
        text = "\n".join(paras)
        # Detik ad-insertion marker can survive paragraph extraction and must not
        # become source evidence or generated slide text.
        text = re.sub(r"(?im)^\s*scroll\s+to\s+continue\s+with\s+content\s*$\n?", "", text)
        # Strip inline "Baca juga"/"Baca juga artikel" + trailing URL from body
        # CNBC/detik often embed cross-links mid-paragraph that leak extra URLs into LLM context
        text = re.sub(r'\(?\s*Baca\s+(?:juga|artikel|tautan|terkait)\s*(?::|.*?)\s*(https?://\S+)', '', text, flags=re.I)
        text = text if len(text) > 200 else ""
    except Exception as e:
        log.warning(f"Fetch body: {url[:60]} — {e}")
    if og_image:
        _IMAGE_PROVENANCE_CACHE[cache_key] = _image_provenance(url, og_image, declared_on_page=True)
    result = (text, og_image, published_ts)
    _BODY_CACHE[cache_key] = result
    return result


ECONOMY_TITLE_SIGNALS = (
    "ekonomi", "anggaran", "pajak", "subsidi", "bansos", "inflasi", "defisit",
    "utang", "rupiah", "dolar", "saham", "ihsg", "bi rate", "suku bunga",
    "dividen", "setor", "pnbp", "penerimaan negara", "apbn", "apbd", "bumn", "investasi", "ekspor", "impor", "dagang",
    "phk", "pekerja", "buruh", "upah", "gaji", "pabrik", "industri",
    "harga", "bbm", "listrik", "pangan", "minyak", "emas", "kredit",
    "bank", "ojk", "bi ", "kemenkeu", "sri mulyani", "danantara",
    "prabowo", "menteri", "presiden", "kebijakan", "regulasi", "tarif",
    "insentif", "cadangan devisa", "neraca", "resesi", "the fed",
    "hilirisasi", "perikanan", "perkebunan", "pertanian", "petani",
    "nelayan", "tambang", "mineral", "batu bara", "nikel", "gas",
    "konstruksi", "infrastruktur", "jalan tol", "kereta", "pelabuhan",
    "bandara", "proyek", "realisasi", "lelang", "tender",
    "pdb", "pertumbuhan", "konglomerat", "taipan", "kongsi",
    "bursa", "bei", "dividen", "laba", "rugi", "bangkrut",
    "pasar modal", "reksadana", "obligasi", "sbn", "sukuk",
    "perbankan", "fintech", "kripto", "startup", "unicorn",
    "koperasi", "asuransi", "dapen", "pensiun",
    "sawit", "beras", "jagung", "kedelai", "gula", "daging",
    "tekstil", "otomotif", "elektronik", "semen", "baja",
    "kemenhub", "audit", "keselamatan", "transportasi",
    "china", "rusia", "jepang", "asing", "penjajakan",
    "emiten", "digugat", "direktur", "miliar", "triliun",
    "garap", "railway", "trans", "gugat", "sengketa",
    "keuangan", "bpk", "fiskal", "penerimaan", "belanja",
    "pembiayaan", "neraca", "cadangan", "laporan",
)


ECONOMY_SELECTION_SIGNALS = (
    "ekonomi", "anggaran", "pajak", "subsidi", "bansos", "inflasi", "defisit", "utang",
    "rupiah", "dolar", "saham", "ihsg", "bi rate", "suku bunga", "apbn", "apbd", "bumn",
    "investasi", "ekspor", "impor", "phk", "pekerja", "buruh", "upah", "gaji", "pabrik",
    "industri", "harga", "bbm", "listrik", "pangan", "kredit", "bank", "ojk", "kemenkeu",
    "asn", "pppk", "guru", "tenaga pendidik", "aparatur sipil negara",
    "kebijakan", "regulasi", "tarif", "insentif", "hilirisasi", "perdagangan", "keuangan",
    "penerimaan", "belanja", "pembiayaan", "perbankan", "asuransi", "koperasi",
    # International economy; body gate requires explicit finance/economy evidence.
    "federal reserve", "the fed", "opec", "selat hormuz", "hormuz", "global economy", "economic recession",
    "interest rate", "interest rates", "inflation", "gdp", "trade war", "oil price", "oil prices",
    "global market", "stocks", "stock market", "economy", "tarif trump", "kebijakan trump",
    "donald trump", "trump", "earnings", "revenue", "profit", "acquisition", "merger",
    "ipo", "holding", "stake", "stocks", "stock market", "bond yield",
    "earnings", "revenue", "profit", "acquisition", "merger", "investment", "investor",
    # Tech/digital economy
    "startup", "series a", "series b", "series c", "funding", "pendanaan",
    "fintech", "edutech", "healthtech", "e-commerce", "ai ", "artificial intelligence",
    "digital", "platform", "aplikasi", "data cent", "cloud", "indosat",
    "telkom", "gojek", "tokopedia", "bukalapak", "unicorn", "decacorn",
    "nvidia", "openai", "agentic", "ipo", "akuisisi", "merger",
    "revenue", "profit", "laba", "ventura", "venture",
    "data centre", "datacenter", "data center", "centres", "centers", "acquisition",
    "acquires", "buys", "expands", "expansion", "ekspansi",
    "raises", "raise", "ventures", "venture capital",
)


MATERIAL_DIGITAL_TITLE_SIGNALS = (
    "funding", "pendanaan", "series a", "series b", "series c", "seri a", "seri b", "seri c",
    "ipo", "akuisisi", "merger", "phk", "laba", "rugi", "pendapatan", "investasi",
    "ekspansi", "kontrak", "bangkrut", "pailit", "regulasi", "acquisition", "acquires",
    "raises", "raised", "expands", "profit", "revenue",
)

GLOBAL_ECONOMY_TITLE_SIGNALS = (
    "federal reserve", "the fed", "ecb", "bank of japan", "boj", "pboc", "opec",
    "interest rate", "interest rates", "inflation", "gdp", "recession", "resesi",
    "trade war", "tarif", "trade", "oil price", "oil prices", "commodity", "commodities",
    "currency", "dollar", "stocks rally", "stocks fall", "stock market", "bond yield",
    "investment", "invests", "investor", "exports", "imports", "global economy",
    "ekonomi global", "perdagangan global", "harga minyak dunia", "selat hormuz", "hormuz",
    "trump", "donald trump", "earnings", "revenue", "profit", "acquisition", "merger",
    "ipo", "holding", "stake", "stocks", "stock market",
)


def _has_economy_title_signal(title):
    """Keep mixed/general feeds from consuming economy-pipeline LLM attempts."""
    title_lower = title.lower()
    return any(re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", title_lower)
               for signal in ECONOMY_SELECTION_SIGNALS)


def _has_source_title_signal(title, source):
    """Keep source-specific noise out before body fetch; body gate remains authoritative."""
    title_lower = title.lower()
    if source == "dailysocial":
        return any(signal in title_lower for signal in MATERIAL_DIGITAL_TITLE_SIGNALS)
    if source == "cnbc_global":
        if "revenue chief" in title_lower:
            return False
        return any(signal in title_lower for signal in GLOBAL_ECONOMY_TITLE_SIGNALS)
    if source == "bbc_business":
        return any(signal in title_lower for signal in (
            "business", "company", "companies", "sales", "revenue", "profit", "jobs",
            "economy", "inflation", "interest rates", "trade", "market", "markets",
            "investment", "investor", "bank", "energy", "oil", "electric vehicle",
        ))
    return True


def _editorial_candidate_gate(title, body):
    """Keep Techbro on material economy stories, not advice or promotion."""
    text = f"{title} {body}".lower()
    cfg = EDITORIAL_SELECTION
    personal = cfg.get("personal_finance_advice", ())
    retail = cfg.get("retail_markers", ())
    promotion = cfg.get("promotion_markers", ())
    event = cfg.get("event_markers", ())
    material = cfg.get("material_change_markers", ())
    topic = cfg.get("material_topic_markers", ())
    title_lower = title.lower()
    if (re.search(r"\b(perang|konflik|serangan|militer|rudal|gencatan senjata|memanas)\b", title_lower)
            and not any(term in title_lower for term in (
                "tarif", "sanksi", "minyak", "energi", "dagang", "ekonomi", "inflasi", "pangan",
                "ekspor", "impor", "pasar", "harga", "investasi", "bursa",
            ))):
        return "non_economic_geopolitical_story"
    if (re.search(r"\b(luncurkan|hadirkan|perkuat ekosistem|solusi|kartu kredit)\b", title_lower)
            and not any(term in title_lower for term in (
                "aturan", "regulasi", "sanksi", "denda", "akuisisi", "merger", "ipo", "phk", "laba",
                "rugi", "pendapatan", "investasi senilai", "kontrak",
            ))):
        return "routine_product_announcement"
    if (re.search(r"\b(stock picks?|dividend stocks?|analysts? like|steady income|unloved stock)\b", title_lower)
            and not any(term in title_lower for term in (
                "regulator", "regulasi", "sanksi", "denda", "akuisisi", "merger", "phk", "laba",
                "rugi", "pendapatan", "investasi senilai", "kontrak",
            ))):
        return "investment_advice"
    if any(term in title_lower for term in cfg.get("non_economic_title_markers", ())):
        return "non_economic_title"
    historical_hits = sum(term in title_lower for term in cfg.get("historical_title_markers", ()))
    if historical_hits >= 2 and not any(term in title_lower for term in cfg.get("current_title_markers", ())):
        return "historical_economy_story"
    if (any(term in title_lower for term in cfg.get("personal_title_markers", ()))
            and sum(term in text for term in personal) >= 2):
        return "personal_finance_advice"
    retail_hits = sum(term in text for term in retail)
    promo_hits = sum(term in text for term in promotion)
    event_hits = sum(term in text for term in event)
    material_hits = sum(term in text for term in material)
    if retail_hits and (promo_hits >= 2 or event_hits >= 1) and material_hits < 2:
        return "retail_event_promotion"
    if not any(term in text for term in topic) or not any(term in text for term in material):
        return "no_material_economic_topic"
    return None


def _english_source_body(body):
    """Detect obvious English-only source bodies before spending writer calls."""
    english = {"a", "an", "and", "are", "as", "at", "been", "but", "by", "for", "from",
               "has", "have", "in", "is", "more", "of", "on", "or", "said", "since",
               "than", "that", "the", "their", "this", "to", "was", "were", "with"}
    indonesian = {"akan", "atau", "bagi", "banyak", "bisa", "bukan", "dan", "dari", "dengan",
                  "di", "dalam", "ini", "itu", "jadi", "juga", "karena", "kalau", "ke", "menurut",
                  "pada", "untuk", "yang", "sudah", "tapi", "tidak"}
    words = set(re.findall(r"[A-Za-z]+", (body or "").lower()))
    return len(words & english) >= 5 and len(words & indonesian) <= 1


def _is_eligible_candidate(title, body, source):
    """Full economy gate shared by main pick and retry path.
    Returns (eligible: bool, reason: str)."""
    title_lower = title.lower()
    # Viral routing: title angle is editorial input, not proof of falsehood.
    # Body grounding and publish validators remain authoritative.
    for kw in HARD_REJECT:
        if _matches_keyword(title_lower, kw):
            return False, "hard_reject:" + kw.strip()
    for name in NAMED_BLACKLIST:
        if _matches_keyword(title_lower, name):
            return False, "blacklist:" + name
    # Gate A: non-event hard reject — no decision marker found in title
    # Losers @ryanhadiii: "sinyal", "tunggu", "respons X soal Y" without any decision action
    if NON_EVENT_HARD:
        hit_hard = [kw for kw in NON_EVENT_HARD if _matches_keyword(title_lower, kw)]
        if hit_hard:
            has_decision = any(_matches_keyword(title_lower, dm) for dm in DECISION_MARKERS)
            if not has_decision:
                return False, "non_event_hard:" + hit_hard[0].strip()
    if title_lower.startswith("video:"):
        return False, "video_article"
    if not body or len(body) < 500:
        return False, "body_under_500_chars"
    # English-only feeds repeatedly produce untranslatable drafts; reject before
    # LLM generation so retry can use Indonesian candidates instead.
    if source in {"cnbc_global", "bbc_business"} and _english_source_body(body):
        return False, "source_body_english_only"
    # Stable reject order: source noise, specific advice/promo, routine copy, material gate.
    if not _has_source_title_signal(title, source):
        return False, "source_title_not_material"
    editorial_reason = _editorial_candidate_gate(title, body)
    if editorial_reason in {
        "personal_finance_advice", "non_economic_geopolitical_story", "routine_product_announcement",
        "investment_advice",
    }:
        return False, editorial_reason
    if _is_corporate_promo(title, body):
        return False, "corporate_promo"
    if _is_low_value_promo(title, body):
        return False, "LOW_VALUE_PROMO"
    historical_markers = (
        "autobiografi", "biografi", "surat-surat", "lahir", "tahun lalu",
        "pensiun", "pensiunan", "proklamator",
    )
    current_action_markers = (
        "2026", "2027", "saat ini", "tahun ini", "baru-baru ini",
        "ditetapkan", "disahkan", "berlaku", "mengusulkan", "mengubah",
        "menaikkan", "menurunkan", "menyalurkan", "mengakuisisi", "melantai",
        "membagikan dividen", "menerbitkan utang",
    )
    body_lower = body.lower()
    if (sum(marker in body_lower for marker in historical_markers) >= 2
            and not any(marker in body_lower for marker in current_action_markers)):
        return False, "historical_profile_without_current_economy_action"
    global_ok = source not in {"cnn_global", "cnbc_global"} or _is_global_finance_story(title, body)
    if not global_ok:
        return False, "non-finance global story"
    if _is_routine_market_story(title, body):
        return False, "routine market story"
    if _is_administrative_distribution_story(title, body):
        return False, "administrative_distribution_story"
    if _is_empty_commentary(title, body):
        return False, "empty commentary"

    if source == "dailysocial" and not _is_material_digital_story(title, body):
        return False, "non_material_digital_story"
    if _is_low_value_corporate_story(title, body):
        return False, "low_value_corporate_story"
    if not has_material_economic_signal(title, body):
        return False, "NO_MATERIAL_ECONOMIC_SIGNAL"
    if not _is_techbro_relevant(body):
        return False, "not techbro relevant"
    if _indonesia_topic_relevance(title, body) is None:
        return False, "no_indonesia_relevance"
    topic_score, economy_score, impact_score = _topic_score(title, body)
    pattern_name, pattern_confidence = _classify_pattern(title, body)
    # ponytail: patterns rank/hooks only; evidence gates above decide eligibility.
    pattern_reason = (f"pattern={pattern_name} conf={pattern_confidence:.2f}"
                      if pattern_name else "pattern=none")
    return True, f"has_material_economic_signal=True {pattern_reason} topic={topic_score} economy={economy_score} impact={impact_score}"


MATERIAL_ECONOMIC_SIGNALS = (
    # Public policy, regulation, and public money.
    "kebijakan", "regulasi", "peraturan baru", "aturan baru", "putusan", "ditetapkan", "disahkan",
    "apbn", "apbd", "anggaran negara", "anggaran daerah", "subsidi", "pajak",
    "defisit", "penerimaan negara", "belanja pemerintah", "tarif resmi",
    # Material business events.
    "investasi", "pendanaan", "pendapatan", "revenue", "laba", "profit", "rugi",
    "phk", "pemutusan hubungan kerja", "ekspansi", "akuisisi", "merger", "ipo",
    "bangkrut", "pailit", "kontrak", "tender", "ekspor", "impor", "dividen",
    "restrukturisasi", "utang baru", "sanksi", "denda", "audit", "harga saham",
    # Economy-wide household, labour, and market impact.
    "inflasi", "daya beli", "bi rate", "suku bunga", "bunga utang", "pembayaran bunga", "ojk", "upah minimum",
    "lapangan kerja", "pengangguran", "biaya rumah tangga", "harga pangan",
    "harga bbm", "harga listrik", "kredit umkm", "pembiayaan umkm", "electric vehicle",
    "vehicle sales", "car makers", "jobs", "employment",
)


def has_material_economic_signal(title, body):
    """Return explicit eligibility boolean for material economic news.

    Generic economy vocabulary, a lone price, or a retail discount is not
    material. Keep this classifier event/impact based; scoring remains ranking.
    """
    text = f"{title} {body}".lower()
    return any(signal in text for signal in MATERIAL_ECONOMIC_SIGNALS)


def _is_low_value_promo(title, body):
    """Reject retail/service promotion unless material signal gate already passes."""
    text = f"{title} {body}".lower()
    promo_markers = (
        "promo", "diskon", "sale", "cashback", "voucher", "gratis", "hadiah",
        "kupon", "potongan harga", "minimum transaksi", "buy one get one", "belanja",
        "program hadiah", "aplikasi", "layanan digital", "solusi keuangan",
        "bantu nasabah", "membantu nasabah", "customer experience", "kemudahan",
    )
    retail_markers = (
        "transmart", "supermarket", "minimarket", "hypermart", "toko", "mal", "mall",
        "retail", "ritel", "produk", "nasabah", "konsumen", "pengguna",
    )
    promo_hits = sum(marker in text for marker in promo_markers)
    retail_hits = sum(marker in text for marker in retail_markers)
    return promo_hits >= 2 and retail_hits >= 1 and not has_material_economic_signal(title, body)


def _is_corporate_promo(title, body):
    """Backward-compatible alias for callers using old promo helper."""
    return _is_low_value_promo(title, body)


def _final_publish_veto(article, result):
    """Re-run source relevance gates after LLM output; publishing fails closed."""
    title = article.get("title", "")
    body = article.get("body", "")
    material = has_material_economic_signal(title, body)
    low_value_promo = _is_low_value_promo(title, body)
    angle_arc = f"{result.get('angle', '')} {result.get('arc', '')}".lower().replace("-", "_")
    if low_value_promo and not material:
        return "LOW_VALUE_PROMO: material_economic_signal=False"
    if "wallet_pressure" in angle_arc and not material:
        return "WALLET_PRESSURE_WITHOUT_MATERIAL_ECONOMIC_SIGNAL"
    return None


def _is_low_value_corporate_story(title, body):
    """Reject corporate profile/strategy copy without a public-economy event."""
    text = f"{title} {body}".lower()
    profile_markers = (
        "strategi", "era digital", "layanan digital", "layanan keuangan",
        "kinerja positif", "bukukan laba", "catat laba", "masa depan",
        "inovasi", "solusi", "nasabah", "pemegang polis", "tata kelola",
        "keamanan", "privasi", "prinsip syariah", "rbc", "psak 117",
    )
    public_event_markers = (
        "akuisisi", "merger", "ipo", "phk", "dividen", "kontrak", "tender",
        "ekspor", "impor", "investasi senilai", "regulasi baru", "peraturan baru",
        "sanksi", "audit", "denda", "pailit", "restrukturisasi", "utang baru",
        "harga saham", "putusan", "tarif", "izin usaha dicabut",
    )
    corporate_identity = bool(re.search(
        r"\b(bank|bumn|asuransi|perusahaan|emiten|startup|aplikasi|platform|life)\b",
        text,
    ))
    profile_hits = sum(marker in text for marker in profile_markers)
    has_public_event = any(marker in text for marker in public_event_markers)
    return corporate_identity and profile_hits >= 2 and not has_public_event


def _is_material_digital_story(title, body):
    """Allow digital-economy stories only for material business events."""
    text = f"{title} {body}".lower()
    event = (
        "pendanaan", "funding", "seri a", "seri b", "seri c", "akuisisi",
        "merger", "ipo", "phk", "kontrak", "investasi", "ekspansi",
        "laba", "rugi", "pendapatan", "bangkrut", "pailit",
    )
    return any(marker in text for marker in event)


def _is_techbro_relevant(body):
    """Require a concrete Indonesia or global finance/economy signal in article body."""
    return bool(re.search(
        r"\b(indonesia|ri|rupiah|apbn|anggaran|pajak|subsidi|bansos|"
        r"pemerintah indonesia|presiden|mahkamah konstitusi|mk|kemenkeu|"
        r"bank indonesia|bi|ojk|bpk|dpr|federal reserve|the fed|ecb|bank sentral eropa|"
        r"bank of japan|boj|bank rakyat china|pboc|opec|harga minyak dunia|selat hormuz|hormuz|"
        r"iran|timur tengah|trump|donald trump|tarif dagang|perang dagang|sanksi ekonomi|"
        r"resesi global|ekonomi global|perdagangan global|investasi|investor|"
        r"saham|pasar saham|obligasi|pendapatan|laba|rugi|akuisisi|merger|ipo|"
        r"earnings|revenue|profit|acquisition|merger|stocks|investment|trade|"
        r"electric vehicle|vehicle sales|car makers|jobs|employment|business|company|"
        r"jakarta|surabaya|bandung|medan|semarang|makassar|palembang|"
        r"kalimantan|sumatera|sulawesi|papua|maluku|bali|nusa tenggara|"
        r"menteri|kementerian|direktur jenderal|gubernur|bupati|walikota|"
        r"dpr ri|dprd|kpk|kejaksaan|mahkamah agung|bumn|bumd)\b",
        body, re.IGNORECASE,
    ))


def _is_global_finance_story(title, body):
    """Global desk is for an explicit economy/finance headline, never general geopolitics."""
    headline = title.lower()
    headline_finance = any(word in headline for word in (
        "fed", "federal reserve", "ecb", "bank sentral", "suku bunga", "inflasi",
        "resesi", "gdp", "ekonomi", "tarif", "dagang", "opec", "minyak",
        "pasar", "saham", "obligasi", "dolar", "mata uang", "utang", "investasi",
        "stocks", "stock market", "interest rates", "oil prices", "trade", "economy", "stake",
        "earnings", "revenue", "profit", "acquisition", "merger", "ipo", "holding",
        "sales", "electric vehicle", "vehicle sales", "car makers", "car maker",
        "energy", "markets", "stock", "stocks",
        "share", "shares", "stake",
        "pendapatan", "laba", "rugi", "akuisisi", "merger", "ipo", "investasi",
    ))
    trump_policy = bool(re.search(r"\b(trump|donald trump)\b", headline)) and bool(re.search(
        r"\b(tarif|dagang|impor|ekspor|sanksi|minyak|investasi|ekonomi|pajak|dolar|"
        r"trade|tariff|import|export|sanction|oil|investment|economy)\b",
        f"{headline} {body}".lower(),
    ))
    hormuz_energy = bool(re.search(r"\b(selat hormuz|hormuz)\b", headline)) and bool(re.search(
        r"\b(minyak|energi|bbm|oil|energy|fuel)\b", f"{headline} {body}".lower(),
    ))
    return (headline_finance or trump_policy or hormuz_energy) and _is_techbro_relevant(body)


def _is_routine_market_story(title, body):
    """Daily market moves need an Indonesia policy/public-money angle."""
    text = f"{title} {body}".lower()
    headline = title.lower()
    # Market MOVEMENT keywords only — skip "pemegang saham", "mata uang" etc.
    market_movement = any(word in text for word in (
        "rupiah melemah", "rupiah menguat", "kurs rupiah", "ihsg", "harga emas", "harga minyak",
    ))
    market_headline = any(word in headline for word in (
        "saham naik", "saham turun", "saham anjlok", "saham melonjak",
        "indeks saham", "bursa saham", "harga saham", "saham hari ini",
    ))
    market = market_movement or market_headline
    # Policy evidence often sits in article body, not headline.
    policy = any(word in text for word in (
        "bi rate", "bank indonesia", "makroprudensial", "apbn", "anggaran", "pajak",
        "subsidi", "peraturan", "kebijakan", "ditetapkan", "putusan", "bpk", "ojk",
    ))
    global_shock = any(word in text for word in (
        "selat hormuz", "hormuz", "opec", "perang dagang", "tarif trump", "sanksi ekonomi",
    ))
    return market and not policy and not global_shock


def _engagement_priority_bonus(title, body):
    """Prefer decision/public-money stories over routine market updates."""
    text = f"{title} {body}".lower()
    bonus = 0
    for terms, value in (
        (("kebijakan", "aturan", "peraturan", "putusan", "ditetapkan", "disahkan"), 12),
        (("anggaran", "apbn", "apbd", "subsidi", "pajak", "belanja pemerintah"), 10),
        (("korupsi", "kerugian negara", "audit bpk", "temuan bpk"), 10),
        (("gaji", "upah", "umk", "tunjangan", "honor", "pppk", "asn", "pns",
          "pegawai", "karyawan", "pekerja", "buruh", "guru"), 10),
        (("tarif", "ongkos", "biaya", "harga", "bbm", "pertalite", "pertamax",
          "elpiji", "lpg", "listrik", "air", "transportasi", "kebutuhan pokok",
          "daya beli", "rumah tangga", "konsumen"), 8),
        (("siapa membayar", "pihak yang membayar", "pihak yang menerima", "penerima manfaat", "untuk subsidi"), 8),
        (("konflik", "berhadapan", "dibandingkan", "sementara", "belum jelas", "masih menunggu"), 5),
        (("rupiah melemah", "rupiah menguat", "ihsg", "harga emas", "harga minyak"), -12),
    ):
        if any(term in text for term in terms):
            bonus += value
    return bonus


def _story_selection_bonus(title, body):
    """Prefer concrete events with visible system and human stakes."""
    text = f"{title} {body}".lower()
    groups = (
        ("event", ("rusak", "hancur", "terbakar", "diserang", "mandek", "berhenti",
                   "ditutup", "runtuh", "terganggu", "naik", "turun", "ditetapkan",
                   "diubah", "disahkan", "diblokir", "bangkrut", "pailit", "phk",
                   "pemutusan hubungan kerja", "menunda", "mencabut"), 8),
        ("chain", ("produksi", "pasokan", "logistik", "rantai pasok", "pengiriman",
                   "gudang", "ekspor", "impor", "operasi", "operasional", "pendapatan",
                   "harga", "biaya"), 7),
        ("human", ("pekerja", "buruh", "warga", "korban", "petani", "nelayan",
                   "konsumen", "rumah tangga", "pedagang", "umkm"), 5),
        ("gap", ("belum", "masih", "menunggu", "tidak berarti", "baru", "hanya",
                  "belum jelas", "belum ada"), 4),
    )
    return sum(value for _, terms, value in groups if any(term in text for term in terms))


# ── Pressbox-style Pattern Classification ──────────────────────────────────────
# 5 PINDAR patterns with keyword triggers + priority ordering.
# Priority: KORUPSI > KEBIJAKAN > PROYEK > PERDAGANGAN > PASAR
# Pattern determines candidate selection priority AND S1 hook style in LLM prompt.

ECONOMY_PATTERNS = {

    "KORUPSI": {
        "priority": 1,
        "label": "Korupsi & Skandal",
        "desc": "Korupsi, suap, gratifikasi, temuan BPK, rugikan negara — viral, high engagement",
        "keywords": [
            "korupsi", "suap", "gratifikasi", "pencucian uang", "tppu",
            "kpk", "kejagung", "kejaksaan agung", "bareskrim",
            "rugikan negara", "kerugian negara", "merugikan negara",
            "temuan bpk", "audit bpk", "opini wtp", "catatan bpk", "wtp", "catatan", "bpk",
            "penyelewengan", "penyimpangan", "mark-up", "mark up",
            "proyek fiktif", "fiktif", "gelapkan", "penggelapan",
            "tersangka", "ditahan", "divonis", "dakwaan",
            "sita", "sitaan", "aset sitaan", "pemblokiran",
            "buron", "cekal", "red notice", "interpol",
            "saksi", "alat bukti", "sadap", "obar",
        ],
    },
    "KEBIJAKAN": {
        "priority": 2,
        "label": "Kebijakan & Aturan Baru",
        "desc": "Peraturan, putusan, kebijakan pemerintah — siapa kena dampak",
        "keywords": [
            "kebijakan", "regulasi", "peraturan", "putusan", "aturan",
            "disahkan", "ditetapkan", "berlaku", "dicabut", "direvisi",
            "dividen", "setor", "pnbp", "penerimaan negara", "apbn", "apbd", "anggaran negara", "anggaran daerah",
            "insentif", "keringanan", "pembebasan", "penghapusan",
            "larangan", "pembatasan", "moratorium",
            "hilirisasi", "larangan ekspor bahan mentah",
            "deregulasi", "omnibus law", "uu cipta kerja",
            "perppu", "perpres", "permen", "kepmen",
        ],
    },
    "PROYEK": {
        "priority": 3,
        "label": "Proyek & Infrastruktur",
        "desc": "Proyek besar, infrastruktur, investasi asing — lapangan kerja & kontrak",
        "keywords": [
            "infrastruktur", "proyek", "konstruksi", "pembangunan",
            "investasi", "penanaman modal", "pma", "pmdn",
            "kereta", "railway", "trans", "jalan tol", "pelabuhan", "bandara",
            "bendungan", "waduk", "irigasi", "plta", "plt",
            "realisasi", "groundbreaking", "peresmian", "rampung",
            "tender", "lelang", "kontrak", "konsorsium",
            "china", "rusia", "jepang", "korea", "asing",
            "danantara", "ina", "swf", "sovereign wealth fund",
            "ikn", "ibu kota nusantara", "ibu kota baru",
            "pabrik", "smelter", "kilang", "kawasan industri",
            "tambang", "mineral", "nikel", "batu bara", "emas", "tembaga", "esdm", "kontraksi", "bumn", "kementerian pu", "kemenhub", "basuki",
        ],
    },
    "PERDAGANGAN": {
        "priority": 4,
        "label": "Perdagangan & Komoditas",
        "desc": "Impor, ekspor, neraca dagang, komoditas, harga pangan — dampak konsumen",
        "keywords": [
            "impor", "ekspor", "eksportir", "importir",
            "neraca perdagangan", "neraca dagang", "surplus", "defisit",
            "perdagangan", "komoditas", "komoditi",
            "bea cukai", "bea masuk", "bea keluar", "tarif impor",
            "harga pangan", "harga beras", "harga bawang", "harga cabai",
            "harga minyak", "harga daging", "harga telur", "harga gula",
            "stok", "pasokan", "ketersediaan", "kelangkaan",
            "bapanas", "bulog", "cadangan pangan",
            "ton", "ribu ton", "juta ton",
            "panen", "gagal panen", "musim panen",
            "data ekspor", "data impor", "ekspor-impor", "impor barang",
            "laporan perdagangan", "indonesia-china", "indonesia-india",
            "diimpor dari", "diekspor ke", "dipasok dari",
        ],
    },
    "PASAR": {
        "priority": 5,
        "label": "Pasar & Keuangan",
        "desc": "Saham, IHSG, rupiah, bursa, obligasi — investor & pelaku pasar",
        "keywords": [
            "saham", "ihsg", "bursa", "bei", "bursa efek", "indeks harga saham", "penguatan ihsg", "tren penguatan", "level",
            "rupiah", "dolar", "nilai tukar", "kurs",
            "laba", "rugi", "dividen", "rights issue",
            "obligasi", "sukuk", "sbn", "surat utang",
            "reksadana", "rdpt", "rds", "rdpu",
            "emiten", "ipo", "listing", "delisting",
            "the fed", "federal reserve", "suku bunga",
            "kripto", "bitcoin", "ethereum", "aset kripto",
            "capital outflow", "capital inflow", "hot money",
            "bank sentral", "bi rate", "bank indonesia",
        ],
    },
}


def _classify_pattern(title, body):
    """Classify article into economy pattern with confidence score.
    Returns (pattern_name, confidence) or (None, 0) if no pattern matches.
    Like Pressbox's _select_viral_pattern but for economy content.
    """
    text = f"{title} {body}".lower()

    # Utility/tutorial check — never generate from how-to articles
    utility = any(phrase in title.lower() for phrase in (
        "cek bansos", "cara cek", "pakai nik", "status penerima",
        "syarat daftar", "simak", "berikut", "ini dia",
    ))
    if utility:
        return None, 0

    best_pattern = None
    best_confidence = 0

    for name, cfg in sorted(ECONOMY_PATTERNS.items(), key=lambda x: x[1]["priority"]):
        hits = sum(1 for kw in cfg["keywords"] if re.search(rf"\b{re.escape(kw)}\b", text))
        # Confidence = hits weighted by priority (higher priority = more generous)
        thresholds = {"KORUPSI": 5, "KEBIJAKAN": 5, "PROYEK": 6, "PERDAGANGAN": 5, "PASAR": 5}
        divisor = thresholds.get(name, 4)
        confidence = min(hits / divisor, 1.0)

        # Higher-priority patterns need fewer hits to qualify
        min_hits = {1: 2, 2: 2, 3: 3, 4: 2, 5: 3}.get(cfg["priority"], 3)
        if hits >= min_hits and confidence > best_confidence:
            # Priority-weighted: higher priority gets bonus
            priority_bonus = (6 - cfg["priority"]) * 0.06
            adjusted_confidence = confidence + priority_bonus

            if adjusted_confidence > best_confidence:
                best_confidence = min(adjusted_confidence, 1.0)
                best_pattern = name

    return best_pattern, best_confidence


def _pattern_label(pattern_name):
    """Safe display label; eligibility still requires a real PINDAR pattern."""
    return ECONOMY_PATTERNS.get(pattern_name, {}).get("label", "Tidak terklasifikasi")


def _editorial_lens(title, body):
    """Assign one repeatable creator lens from literal economy signals."""
    text = f"{title} {body}".lower()
    if re.search(r"\b(pajak|subsidi|tarif|biaya|beban|anggaran|utang)\b", text):
        return "siapa_yang_bayar"
    if re.search(r"\b(harga|gaji|upah|umr|ump|daya beli|cicilan|kredit|konsumen)\b", text):
        return "angka_ke_dompet"
    if _is_global_event(title, body) and re.search(r"\b(dampak|berdampak|risiko|menekan|mendorong|memicu|akibat)\b", text):
        return "global_shock_ke_lokal"
    if re.search(r"\b(untung|penerima|manfaat|laba|dividen|investasi|pemegang saham)\b", text):
        return "siapa_yang_untung"
    return "mekanisme_ekonomi"


def _content_metadata(title, body):
    """Derive auditable content labels; never silently default every post."""
    pattern, _ = _classify_pattern(title, body)
    text = f"{title} {body}".lower()
    if pattern is None:
        if re.search(r"\b(rupiah|saham|ihsg|ipo|bursa|emiten|pasar modal|obligasi|dividen|bunga kredit|cicilan|kpr|utang|investasi)\b", text):
            pattern = "PASAR"
        elif re.search(r"\b(impor|ekspor|harga pangan|pasokan|stok|komoditas)\b", text):
            pattern = "PERDAGANGAN"
        elif re.search(r"\b(kebijakan|aturan|peraturan|ditetapkan|berlaku|apbn|subsidi)\b", text):
            pattern = "KEBIJAKAN"
    amount = bool(re.search(r"(?:rp\s*)?\d[\d.,]*\s*(?:%|persen|triliun|miliar|juta|ribu)", text))
    actor = bool(re.search(r"\b(prabowo|jokowi|menteri|gubernur|kemenkeu|ojk|danantara|bumn|pemerintah)\b", text))
    wallet = bool(re.search(r"\b(harga|biaya|tarif|gaji|upah|umr|ump|subsidi|pajak|daya beli)\b", text))
    decision = bool(re.search(r"\b(tetapkan|ditetapkan|berlaku|disahkan|targetkan|usulkan|batasi|larang|ubah)\b", text))
    finance_practical = bool(re.search(r"\b(bunga|cicilan|kpr|utang|kredit|investasi|risiko|cash flow|arus kas)\b", text))
    if finance_practical:
        hook = "finance_practical"
    elif amount and wallet:
        hook = "wallet_impact"
    elif amount:
        hook = "number_shock"
    elif actor and decision:
        hook = "named_decision"
    elif decision:
        hook = "decision_impact"
    else:
        hook = "source_explainer"
    supply_story = bool(re.search(r"\b(pasokan|distribusi|peternak|produsen|konsumen|surplus|kelangkaan)\b", text))
    if finance_practical:
        arc = "personal_finance_explainer"
    elif pattern == "PERDAGANGAN" and supply_story:
        arc = "supply_shock"
    elif wallet and amount:
        arc = "household_impact"
    elif pattern == "KORUPSI":
        arc = "public_money_trail"
    elif pattern == "KEBIJAKAN":
        arc = "policy_decision_story"
    elif pattern == "PROYEK":
        arc = "public_money_trail"
    elif pattern == "PERDAGANGAN":
        arc = "supply_shock"
    elif pattern == "PASAR" and (actor or decision):
        arc = "market_decision"
    else:
        arc = "market_shock" if pattern == "PASAR" else "source_explainer"
    return pattern, arc, hook


def _topic_score(title, body):
    """Score article relevance: 0-10. Primary: pattern classification. Fallback: editorial LLM."""
    text = f"{title} {body}".lower()
    routine_credit = any(term in text for term in ("bunga kredit", "cicilan", "kpr", "kredit bank"))
    official_trigger = any(term in text for term in ("bank indonesia", "bi rate", "ojk", "aturan", "kebijakan", "ditetapkan", "berlaku"))
    if routine_credit and not official_trigger:
        return 0, 0, 0
    pattern, confidence = _classify_pattern(title, body)
    if pattern:
        score = min(int(confidence * 10), 10)
        return score, score, score
    # Fallback: LLM-based editorial scoring for articles without clear pattern
    try:
        # Simple keyword-based fallback scoring
        text = f"{title} {body}".lower()
        economy_keywords = [
            "ekonomi", "anggaran", "apbn", "apbd", "pajak", "subsidi", "bansos",
            "inflasi", "defisit", "utang", "rupiah", "dolar", "saham", "ihsg",
            "bi rate", "suku bunga", "bumn", "investasi", "ekspor", "impor",
            "phk", "pekerja", "buruh", "upah", "gaji", "industri", "pabrik",
            "harga", "bbm", "listrik", "pangan", "minyak", "kredit", "dividen",
            "bank", "ojk", "kemenkeu", "menteri", "presiden", "dpr",
            "transformasi", "perubahan", "keadilan", "sosial", "csr",
        ]
        economy_hits = sum(1 for kw in economy_keywords if kw in text)
        impact_keywords = [
            "masyarakat", "rakyat", "konsumen", "pekerja", "petani", "nelayan",
            "pedagang", "pengusaha", "umkm", "buruh", "miskin", "harga naik",
            "harga turun", "daya beli", "lapangan kerja", "pengangguran",
        ]
        impact_hits = sum(1 for kw in impact_keywords if kw in text)
        # Scale to 0-10
        eco_score = min(economy_hits * 2, 10)
        impact = min(impact_hits * 2, 10)
        combined = max(eco_score, impact)
        return combined, eco_score, impact
    except Exception:
        return 0, 0, 0


def _is_empty_commentary(title, body):
    """Reject a quote-only official reaction with no action, rule, or concrete data.
    Headline reaction words alone are not enough — check body for concrete substance too."""
    headline = title.lower()
    quote_only = any(word in headline for word in ("kata", "soal", "buka suara", "ungkap", "respons", "bakal"))
    if not quote_only:
        return False
    # Body with real data (Rp amounts, %, triliun/miliar) or policy/action words = substantive news
    body_lower = (body or "").lower()
    has_data = bool(re.search(r"(rp\s?\d|triliun|miliar|juta|\d+%|persen)", body_lower))
    has_action = any(re.search(rf"\b{re.escape(word)}\b", body_lower) for word in (
        "resmi", "berlaku", "ditetapkan",
        "disahkan", "putusan", "audit", "temuan", "phk", "naik", "turun",
        "dipotong", "ditambah", "dialihkan", "investasi", "ekspor", "impor",
        "defisit", "anggaran", "subsidi", "utang",
    ))
    if has_data or has_action:
        return False
    # Still no substance — headline reaction words only
    substance = any(re.search(rf"\b{re.escape(word)}\b", headline) for word in (
        "ditetapkan", "berlaku", "disahkan", "putusan", "peraturan", "audit",
        "temuan", "phk", "naik", "turun", "dipotong", "ditambah", "dialihkan",
    ))
    return quote_only and not substance

# ── POV Helpers ──────────────────────────────────────────────────────────────

def _convert_pov(text):
    """Keep conversational voice; only remove markup."""
    if not isinstance(text, str):
        return ""
    return re.sub(r'(?<!\w)[*_]+([^*_\n]+)[*_]+', r'\1', text)


_SOURCE_DATELINE_RE = re.compile(r"(?:^|\s)Jakarta,\s*CNBC Indonesia\s*-\s*", re.I)
_INCOMPLETE_SOURCE_START_RE = re.compile(
    r"^(?:dan|atau|serta|karena|sehingga|sebagaimana|ketika|yang|untuk|dengan)\b",
    re.I,
)
_INCOMPLETE_SOURCE_END_RE = re.compile(
    r"\b(?:dan|atau|serta|karena|sehingga|sebagaimana|untuk|dengan|dari|di|ke|yang)$",
    re.I,
)


def _clean_source_body(body):
    """Remove known CNBC dateline before source evidence reaches writer."""
    body = re.sub(r"\s+", " ", body or "").strip()
    return _SOURCE_DATELINE_RE.sub(" ", body).strip()


def _usable_source_sentence(sentence):
    sentence = sentence.strip()
    if len(sentence) < 25:
        return False
    complete = re.sub(r"[.!?]+$", "", sentence).strip()
    return not (_INCOMPLETE_SOURCE_START_RE.search(sentence)
                or _INCOMPLETE_SOURCE_END_RE.search(complete))


def _format_sentence_blanks(text):
    """Collapse whitespace to one flowing paragraph per post."""
    s = text.replace('\u2014 ', ' ').replace('\u2014', ' ')
    s = re.sub(r":(?=\s+https?://|\s+www\.)", "\u0001", s)
    s = re.sub(r":\s+", " ", s)
    s = re.sub(r'\s+', ' ', s.replace("\u0001", ":"))
    return s.strip()


def evidence_plan(article):
    """Build bounded source-only units from FULL article text (no first-12 cap).

    Facts for writing must come from the complete article body, not just the
    lead paragraphs. Late-article facts (impact, quotes, next steps) would be
    missing from a [:12] slice and the writer would invent them.
    """
    sentences = []
    seen = set()
    for sentence in _source_sentences(article.get("body", "")):
        key = sentence.lower()
        if key not in seen:
            seen.add(key)
            sentences.append(sentence)
    # Bound by characters, not sentence count, so long articles keep their
    # full fact set while degenerate feeds cannot blow the prompt.
    units = sentences
    body_text = " ".join(units)
    if len(body_text) > 18000:
        # Keep first + last halves so early decisions and late impact both
        # reach the writer; drop only the middle when the article is huge.
        head = []
        tail = []
        total = 0
        for s in units:
            if total + len(s) <= 9000:
                head.append(s)
                total += len(s)
            else:
                break
        tail_total = 0
        for s in reversed(units):
            if tail_total + len(s) <= 9000:
                tail.append(s)
                tail_total += len(s)
            else:
                break
        units = head + list(reversed(tail))
    claim_map = source_claim_map({**article, "body": " ".join(units)})
    slide_seeds = {
        slide: [claim["sentence"] for claim in claims]
        for slide, claims in claim_map.items()
        if claims
    }
    return {"units": units, "slide_seeds": slide_seeds}


def article_evidence_gate(article):
    """Fail closed before LLM spend: body must support six grounded slide seeds."""
    body = _clean_source_body(article.get("body"))
    if len(body) < 500:
        return "body_under_500_chars"
    plan = evidence_plan(article)
    if len(plan["units"]) < 4:
        return "insufficient_source_claims_for_four_posts"
    # Six distinct source units are enough when each slide has its own seed.
    # Requiring eight rejects valid six-paragraph reports before writer can run.
    if len(plan["units"]) < 6:
        return "insufficient_evidence_units"
    if len(plan["slide_seeds"]) < 6:
        return "insufficient_slide_evidence"
    return None


def source_claim_plan(article):
    """Give writer distinct substantive source sentences, never title-derived facts.

    Full-article coverage: no [:12] cap — late-article facts (impact, quotes,
    next steps) must reach the writer so the thread does not invent them.
    """
    body = _clean_source_body(article.get("body"))
    selected = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        sentence = sentence.strip()
        key = sentence.lower()
        if _usable_source_sentence(sentence) and key not in seen:
            seen.add(key)
            selected.append(sentence)
    # Bound by characters (not sentence count) so huge feeds stay prompt-safe.
    units = selected
    joined = " ".join(units)
    if len(joined) > 18000:
        head, tail, total = [], [], 0
        for s in units:
            if total + len(s) <= 9000:
                head.append(s)
                total += len(s)
            else:
                break
        tail_total = 0
        for s in reversed(units):
            if tail_total + len(s) <= 9000:
                tail.append(s)
                tail_total += len(s)
            else:
                break
        units = head + list(reversed(tail))
    return "\n".join(f"- {s}" for s in units)


def source_claim_map(article):
    """Rank distinct source sentences and assign one evidence unit to each slide."""
    body = _clean_source_body(article.get("body"))
    sentences = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        sentence = sentence.strip()
        key = sentence.lower()
        if _usable_source_sentence(sentence) and key not in seen:
            seen.add(key)
            sentences.append(sentence)

    slide_signals = {
        "post_1": ("menetapkan", "ditetapkan", "resmi", "usulkan", "calon", "keputusan", "perubahan"),
        "post_2": ("menteri", "ketua", "gubernur", "menurut", "mengatakan", "ujar", "dpr", "ojk", "bi"),
        "post_3": ("melalui", "dilakukan", "penyaluran", "program", "kerja sama", "pembiayaan", "mengubah"),
        "post_4": ("tujuan", "agar", "supaya", "mulai", "berlaku", "target", "menjaga", "hingga"),
        "post_5": ("dampak", "memengaruhi", "mempengaruhi", "risiko", "biaya", "harga", "konsumen", "pelaku usaha"),
        "post_6": ("menunggu", "belum", "proses", "pembahasan", "persetujuan", "ditentukan", "perlu"),
    }

    def score(sentence, signals):
        text = sentence.lower()
        value = 1
        value += sum(2 for signal in signals if signal in text)
        if re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", text, re.I):
            value += 2
        if '"' in sentence or '“' in sentence:
            value += 1
        return value

    if article.get("pattern") == "PASAR":
        sentences = [s for s in sentences if "calon gubernur bi" not in s.lower()]
    remaining = list(enumerate(sentences))
    result = {}
    for slide, signals in slide_signals.items():
        if not remaining:
            break
        index, sentence = max(remaining, key=lambda item: (score(item[1], signals), -item[0]))
        remaining = [(i, s) for i, s in remaining if i != index]
        result[slide] = [{"sentence": sentence, "score": score(sentence, signals)}]
    return result


STORY_FUNCTIONS = {
    "post_1": "hook_tension",
    "post_2": "proof",
    "post_3": "context_cause",
    "post_4": "impact_stakes",
    "post_5": "tradeoff_unknown",
    "post_6": "judgment_cta",
}


POLICY_WINNING_ROLES = {
    "post_1": ("menetapkan", "ditetapkan", "resmi", "usulan", "mengusulkan", "opsi", "wacana", "bakal", "akan", "perubahan", "dialihkan", "dipindahkan"),
    "post_2": ("menteri", "pemerintah", "pemda", "provinsi", "kabupaten", "kewenangan", "aturan", "undang", "uu", "dpr"),
    "post_3": ("biaya", "anggaran", "jumlah", "kapasitas", "menghitung", "perhitungan", "pendanaan", "pembiayaan", "beban", "guru", "pegawai"),
    "post_4": ("tujuan", "agar", "supaya", "pemerataan", "distribusi", "pembahasan", "pekan", "minggu", "selanjutnya", "rencana", "mulai"),
    "post_5": ("beban", "biaya", "anggaran", "manfaat", "untung", "rugi", "daerah", "pusat", "belum", "masih", "status", "nasib"),
    "post_6": ("belum", "masih", "menunggu", "pembahasan", "persetujuan", "ditentukan", "opsi", "pilih", "perlu", "keputusan"),
}

POLICY_TRADEOFF_MARKERS = ("tetapi", "namun", "sedangkan", "sementara", "di sisi lain", "mengurangi", "menambah")
POLICY_TRADEOFF_TERMS = ("beban", "biaya", "anggaran", "manfaat", "untung", "rugi", "keuntungan")
POLICY_STATUS_GAP_MARKERS = ("menjadi", "berubah", "beralih", "dipindahkan", "dialihkan", "ditarik", "kini", "sebelumnya", "setelah", "dibanding", "opsi", "usul", "wacana", "bakal")


def _policy_winner_enabled(article):
    if article.get("pattern") != "KEBIJAKAN":
        return False
    body = article.get("body", "")
    text = f"{article.get('title', '')} {body}".lower()
    authority = re.search(r"\b(pemerintah|presiden|menteri|kementerian|dpr|gubernur|pemda|otoritas)\b", text)
    policy_action = re.search(r"\b(menetapkan|ditetapkan|mengusulkan|usulan|opsi|wacana|aturan|peraturan|kebijakan|berlaku|kewenangan|dipindahkan|dialihkan|dilarang|subsidi|tarif)\b", text)
    # Only decision/transition stories use status-gap + trade-off arc.
    decision_trigger = re.search(
        r"\b(?:sebelumnya.{0,100}(?:kini|sekarang|mengusulkan)|"
        r"(?:kini|sekarang).{0,100}(?:opsi|usul|wacana|mengusulkan|berubah|"
        r"beralih|dipindahkan|dialihkan)|(?:opsi|usul|wacana)\s+"
        r"(?:baru|resmi|pemerintah)|(?:berubah|beralih|dipindahkan|dialihkan|"
        r"ditarik)\b)", body.lower()
    )
    return bool(authority and policy_action and decision_trigger)


def _policy_tradeoff_sentence(body):
    return next((sentence for sentence in _source_sentences(body)
                 if any(marker in sentence.lower() for marker in POLICY_TRADEOFF_MARKERS)
                 and sum(term in sentence.lower() for term in POLICY_TRADEOFF_TERMS) >= 2), None)


def _policy_status_gap_sentence(body):
    return next((sentence for sentence in _source_sentences(body)
                 if any(marker in sentence.lower() for marker in POLICY_STATUS_GAP_MARKERS)
                 and re.search(r"\b(akan|bakal|opsi|usul|wacana|menjadi|berubah|beralih|dipindahkan|dialihkan|ditarik|kini|sebelumnya|dibanding)\b", sentence.lower())), None)


def policy_winner_evidence(article):
    """Select literal evidence for policy decision arc; empty role blocks arc."""
    if not _policy_winner_enabled(article):
        return {}
    result = {}
    for slide, signals in POLICY_WINNING_ROLES.items():
        ranked = []
        for index, sentence in enumerate(_source_sentences(article.get("body", ""))):
            text = sentence.lower()
            score = sum(3 for signal in signals if signal in text)
            if re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", text, re.I):
                score += 2
            if score:
                ranked.append((score, -index, sentence))
        result[slide] = [sentence for _, _, sentence in sorted(ranked, reverse=True)[:2]]
    return result


def _validate_policy_winner_arc(article, posts):
    """Require source-backed six-slide policy decision story."""
    evidence = policy_winner_evidence(article)
    if not evidence:
        return []
    issues = []
    status_gap_source = _policy_status_gap_sentence(article.get("body", ""))
    tradeoff_source = _policy_tradeoff_sentence(article.get("body", ""))
    if not status_gap_source:
        issues.append("post_1: missing literal status-gap source")
    if not tradeoff_source:
        issues.append("post_5: missing literal trade-off source")
    for slide, sentences in evidence.items():
        if not sentences:
            issues.append(f"{slide}: missing winning arc evidence")
            continue
        post_text = posts.get(slide, "")
        if slide == "post_1" and not any(marker in post_text.lower() for marker in POLICY_STATUS_GAP_MARKERS):
            issues.append("post_1: missing literal status-gap wording")
        if slide == "post_5" and tradeoff_source and not any(term in post_text.lower() for term in POLICY_TRADEOFF_TERMS):
            issues.append("post_5: missing source trade-off terms")
        post_terms = _content_terms(post_text)
        role_sentences = [
            sentence for sentence in _source_sentences(article.get("body", ""))
            if any(signal in sentence.lower() for signal in POLICY_WINNING_ROLES[slide])
        ]
        if not any(len(post_terms & _content_terms(sentence)) >= 2 for sentence in role_sentences):
            issues.append(f"{slide}: does not follow policy winning arc")
        if slide == "post_5":
            text = post_text.lower()
            if not (any(marker in text for marker in POLICY_TRADEOFF_MARKERS)
                    and sum(term in text for term in POLICY_TRADEOFF_TERMS) >= 2):
                issues.append("post_5: missing policy trade-off contrast")
    return issues


def _normalize_grounding_text(text):
    """Normalize only whitespace/case; preserve words and numbers."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _source_sentences(body):
    body = _clean_source_body(body)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if _usable_source_sentence(s)]


def _content_terms(text):
    stop = {
        "yang", "dan", "atau", "ini", "itu", "dari", "untuk", "dengan", "ke", "di",
        "sebuah", "ada", "akan", "sudah", "juga", "oleh", "pada", "dalam", "hingga",
        "lebih", "hanya", "cuma", "jadi", "tapi", "karena", "kalau", "bukan", "yang",
    }
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower()) if w not in stop}


def _validate_source_evidence_map(posts, body):
    """Every content slide needs a concrete source-sentence anchor."""
    required = [f"post_{i}" for i in range(1, 7)]
    if not all((posts.get(key) or "").strip() for key in required):
        return []
    source_sentences = _source_sentences(body)
    issues = []
    for key in required:
        text = posts.get(key, "")
        post_terms = _content_terms(text)
        anchors = set(re.findall(r"\b(?:rp\s*)?\d[\d.,]*\b|\b[A-Z]{2,}\b", text))
        supported = False
        for sentence in source_sentences:
            source_terms = _content_terms(sentence)
            overlap = post_terms & source_terms
            source_anchors = set(re.findall(r"\b(?:rp\s*)?\d[\d.,]*\b|\b[A-Z]{2,}\b", sentence))
            # Paraphrase can retain only two content terms. Numeric/acronym
            # anchors still require an exact source match elsewhere.
            if len(overlap) >= 2 and (len(overlap) >= 3 or anchors & source_anchors or any(len(term) >= 7 for term in overlap)):
                supported = True
                break
        if not supported:
            issues.append(f"{key}: no source-sentence evidence anchor")
    return issues


def _validate_unsupported_inferences(posts, body):
    """Block high-risk combined meanings absent from source, even when tokens exist."""
    source = _normalize_grounding_text(body)
    patterns = (
        (r"\bfakta ini perlu dipantau\b", "generic CTA"),
        (r"\b(?:pertumbuhan|ekonomi|pasar|kondisi|situasi)\s+atau\s+(?:pertumbuhan|ekonomi|pasar|kondisi|situasi)\b", "generic CTA"),
        (r"\bpertama kalinya?\b", "novelty claim"),
        (r"\bhampir dua kali lipat\b", "derived ratio"),
        (r"\bdalam dua tahun\b", "unsupported timeline"),
        (r"\btarget resmi\b", "official-status claim"),
        (r"\bmasih tinggi\b", "unsupported evaluation"),
        (r"\btakut kredit macet\b", "unsupported motive"),
        (r"\bbakal kesulitan\b", "unsupported consequence"),
        (r"\bangka aman\b", "unsupported evaluation"),
        (r"\b(?:biar|supaya|agar) gak boncos\b", "unsupported consequence"),
        (r"\bhampir\s+(?:dua kali|separuh|setengah)\b", "derived comparison"),
        (r"\bdua kali\s+lipat\b", "derived comparison"),
        (r"\bsetara\b", "derived comparison"),
    )
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for pattern, label in patterns:
            match = re.search(pattern, text)
            if match and match.group(0) not in source:
                issues.append(f"{key}: {label} not in article: '{match.group(0)}'")
    return issues


def _validate_unsupported_economic_relationships(posts, body):
    """Reject common economic relationships invented from isolated source tokens."""
    source = _normalize_grounding_text(body)
    patterns = (
        (r"\brp\s*0\s+uang\s+negara\b", "public-funding relationship"),
        (r"\bnama\s+indonesia\b[^.!?]{0,80}\b(?:global|dunia)\b", "national-reputation relationship"),
        (r"\b(?:startup|perusahaan)\s+lokal\s+(?:kalah|menang)\s+(?:sama|dengan|dari)\s+(?:perusahaan\s+)?luar\s+negeri\b", "competitive relationship"),
        (r"\b(?:bikin|membuat)\s+(?:lo|lu|rakyat|masyarakat)\s+bayar\s+lebih\b", "unsupported payer framing"),
        (r"\b(?:isi|mengisi)\s+kas\s+negara\b", "unsupported fiscal purpose"),
        (r"\b(?:kantong|dompet)(?:\s+lo|\s+lu|\s+rakyat)?\b[^.!?]{0,80}\b(?:sasaran|menciut|tertekan|kena beban)\b", "unsupported audience impact"),
        (r"\b(?:daya beli|beban tambahan)\b[^.!?]{0,80}\b(?:menciut|turun|naik|kena|tertekan|terbebani)\b", "unsupported audience impact"),
        (r"\bcari duit lain\b[^.!?]{0,60}\b(?:utang|efisiensi)\b", "unsupported fiscal alternative"),
    )
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for pattern, label in patterns:
            match = re.search(pattern, text)
            if match and match.group(0) not in source:
                issues.append(f"{key}: unsupported economic relationship ({label}): '{match.group(0)}'")
    return issues


def _validate_unsupported_financial_framing(posts, body):
    """Reject new financial-risk framing not stated by source body."""
    source = _normalize_grounding_text(body)
    framing = (
        (r"\bjebakan\s+(?:cicilan|utang|kredit)\b", "debt-trap framing"),
        (r"\brisiko\s+(?:gagal bayar|kredit macet)\b", "default-risk framing"),
        (r"\b(?:untung|diuntungkan)\b[^.!?]{0,80}\b(?:bank|perusahaan)\b", "beneficiary framing"),
        (r"\b(?:bunga|cicilan|utang)\s+(?:baru|tambahan|tinggi|naik)\b", "new financing-cost framing"),
        (r"\b(?:beban|biaya)\s+(?:bunga|cicilan|utang)\b", "new financing-cost framing"),
    )
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for pattern, label in framing:
            match = re.search(pattern, text)
            if match and match.group(0) not in source:
                issues.append(f"{key}: unsupported financial framing '{match.group(0)}' ({label})")
    return issues


def _validate_range_direction(posts, body):
    """Reject wording that reverses source range direction."""
    source = _normalize_grounding_text(body)
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for number in re.findall(r"(?:rp\s*)?\d[\d.,]*\s*(?:triliun|miliar|juta|ribu)?", text):
            number = number.strip()
            if re.search(rf"{re.escape(number)}\s+ke\s+atas", source) and re.search(rf"(?:sampai|maksimal|batas)\s+{re.escape(number)}", text):
                issues.append(f"{key}: range direction reverses source near '{number}'")
    return issues


AUDIENCE_LENS_TERMS = {
    "daerah": ("daerah", "pemda", "pemerintah daerah", "provinsi", "kabupaten", "kota"),
    "bencana": ("bencana", "banjir", "gempa", "longsor", "erupsi", "korban"),
    "pad": ("pad", "pendapatan asli daerah", "basis pajak"),
    "petani": ("petani", "pertanian", "sawah", "nelayan"),
    "pekerja": ("pekerja", "buruh", "karyawan", "tenaga kerja"),
    "konsumen": ("konsumen", "pembeli", "pelanggan"),
    "rumah tangga": ("rumah tangga", "keluarga"),
}

AUDIENCE_BLAME_PATTERNS = (
    r"\buang pajak kita\b",
    r"\bdaerah\s+(?:nggak|tidak|tak)\s+bisa cari duit sendiri\b",
    r"\bdianakemaskan\b",
    r"\bsiapa yang sebenarnya bayar\b",
    r"\bwarga pasti terdampak\b",
)


def _validate_audience_lens(article, posts):
    """Require grounded empathy only when source names a public audience group."""
    body = _normalize_grounding_text(article.get("body") or "")
    source_groups = {
        group for group, terms in AUDIENCE_LENS_TERMS.items()
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", body) for term in terms)
    }
    if not source_groups:
        return []
    audience_text = _normalize_grounding_text(" ".join(posts.get(f"post_{i}", "") for i in range(2, 6)))
    all_text = _normalize_grounding_text(" ".join(posts.get(f"post_{i}", "") for i in range(1, 7)))
    issues = []
    if not re.search(r"\b(?:gua|gue)\b", all_text):
        issues.append("voice: missing first-person editorial opinion")
    if not any(any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", audience_text) for term in AUDIENCE_LENS_TERMS[group]) for group in source_groups):
        issues.append("audience lens: S2-S5 missing source-backed affected group")
    for pattern in AUDIENCE_BLAME_PATTERNS:
        match = re.search(pattern, all_text)
        if match and match.group(0) not in body:
            issues.append(f"audience lens: unsupported blame framing '{match.group(0)}'")
    return issues


def _validate_unsupported_editorial_claims(posts, body):
    """Block blame, loss, and motive framing unless source states it."""
    source = _normalize_grounding_text(body)
    patterns = (
        (r"\byang\s+rugi\??\s+apbn\b", "unsupported loss framing"),
        (r"\b(?:kenapa|kok)\s+baru\s+sekarang\b", "unsupported timing/motive framing"),
        (r"\bsejarah\s+macam\s+apa\b", "unsupported moral framing"),
    )
    issues = []
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for pattern, label in patterns:
            match = re.search(pattern, text)
            if match and match.group(0) not in source:
                issues.append(f"{key}: {label} '{match.group(0)}'")
    return issues


def _validate_concept_terms(posts, body):
    """Reject replacement of key article concepts with narrower/broader synonyms.

    Catches paraphrases that shift meaning: article "biaya kendaraan" ->
    post "harga motor", article "bahan bakar impor" -> post "BBM". A post
    that substitutes a synonym pair absent from the source for a literal
    source concept is flagged as a grounding violation.
    """
    source = _normalize_grounding_text(body)
    issues = []
    # Substitution pairs: (source concept, banned synonym pattern, label).
    substitutions = (
        ("biaya kendaraan", r"\bharga\s+motor\b", "harga motor", "cost-vs-price shift"),
        ("bahan bakar impor", r"\bbbm\b", "BBM", "fuel-import vs BBM shift"),
        ("bahan bakar", r"\bbbm\b", "BBM", "fuel vs BBM shift"),
    )
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = _normalize_grounding_text(posts.get(key, ""))
        for concept, pattern, banned_label, label in substitutions:
            if concept in source and re.search(pattern, text) and concept not in text:
                issues.append(f"{key}: {label} '{banned_label}' not in article (source says '{concept}')")
    return issues


def deterministic_grounding_validate(article, posts):
    body = article.get("body") or ""
    return (_validate_numbers(posts, body) + _validate_years(posts, body)
            + _validate_proper_nouns(posts, body)
            + _validate_sensitive_language(posts, body)
            + _validate_unsupported_economic_relationships(posts, body)
            + _validate_unsupported_financial_framing(posts, body)
            + _validate_unsupported_inferences(posts, body) + _validate_range_direction(posts, body)
            + _validate_unsupported_editorial_claims(posts, body)
            + _validate_concept_terms(posts, body)
            + _validate_source_evidence_map(posts, body))


def grounding_validate(article, posts):
    """Pressbox-style grounding: deterministic checks only, one LLM call less."""
    return deterministic_grounding_validate(article, posts)


def is_rate_limit_error(error):
    return bool(error and "rate limit 429" in error.lower())


def hook_issues(hook, body):
    """Hook needs source-backed concrete change; numbers are optional."""
    if not hook.strip():
        return ["S1: empty"]
    if not body.strip():
        return ["S1: source body empty"]
    return []


SLIDE_CHAR_LIMIT = 480


def _sentence_count(text):
    """Count terminal punctuation, not decimal points or abbreviations."""
    return len(re.findall(r"[.!?](?=\s|$|[\"'\u201d\u2019)])", text or ""))


def _fit_complete_sentences(text, limit):
    """Keep complete sentences only; never cut text at an arbitrary character."""
    text = text.strip()
    if len(text) <= limit:
        return text
    sentences = re.findall(r".*?[.!?](?=\s|$)", text, flags=re.S)
    if not sentences:
        return text
    kept = []
    for sentence in sentences:
        candidate = " ".join(kept + [sentence.strip()])
        if len(candidate) > limit:
            break
        kept.append(sentence.strip())
    # One complete sentence is safer than an incomplete sentence. If it alone
    # exceeds limit, keep it intact and let contract validation no-post it.
    return " ".join(kept) or sentences[0].strip()


def _fit_complete_sentences_with_url(text, limit):
    """Fit prose and preserve URL without cutting either prose or URL."""
    match = re.search(r"https?://\S+", text)
    if not match:
        return _fit_complete_sentences(text, limit)
    url = match.group().rstrip(".,)")
    prose = re.sub(r"\s*https?://\S+", "", text).strip()
    room = limit - len(url) - 2
    fitted = _fit_complete_sentences(prose, room)
    return f"{fitted}\n\n{url}" if fitted else url


def thread_contract_issues(posts, article_url, source_key=None):
    """Finalize S1-S6 plus S7 source label; strip legacy URLs from S6."""
    issues = []
    for i in range(1, 7):
        text = posts.get(f"post_{i}", "")
        if not text or not text.strip():
            issues.append(f"post_{i}: empty")
            continue
        # Content slides never carry URLs. Remove legacy S6 URLs before length validation.
        text = re.sub(r'\[URL[^\]]*\]', '', text, flags=re.I)
        text = re.sub(r'\n*\s*https?://\S+', '', text).strip()
        posts[f"post_{i}"] = text
        if len(text) > SLIDE_CHAR_LIMIT:
            posts[f"post_{i}"] = _fit_complete_sentences(text, SLIDE_CHAR_LIMIT)
            if len(posts[f"post_{i}"]) > SLIDE_CHAR_LIMIT:
                issues.append(f"post_{i}: over {SLIDE_CHAR_LIMIT} chars")
    # Repeated numbers can clarify stakes in S6; grounding checks still reject invented values.
    # S6 is CTA only. Move every legacy/LLM URL out, then create S7.
    if article_url:
        for i in range(1, 7):
            text = posts.get(f"post_{i}", "")
            posts[f"post_{i}"] = text.strip()
        posts["post_7"] = f"Sumber: {SOURCE_DISPLAY_NAMES.get(source_key or '', article_url)}"
        if len(posts["post_7"]) > SLIDE_CHAR_LIMIT:
            issues.append(f"post_7: over {SLIDE_CHAR_LIMIT} chars")
    elif "post_7" not in posts:
        posts["post_7"] = ""
    issues.extend(_indonesian_language_issues(posts))
    return issues


# ── LLM Call ─────────────────────────────────────────────────────────────────

# ── LLM Call ─────────────────────────────────────────────────────────────────

def _get_api_key():
    for var in ["MISTRAL_API_KEY", "MISTRAL_KEY"]:
        key = os.getenv(var)
        if key:
            return key
    for var in ["OPENROUTER_API_KEY", "OPENROUTER_KEY"]:
        key = os.getenv(var)
        if key:
            return key
    return None

def _call_llm(system, user, model=None, max_retries=3, temperature=None):
    # LLM_BASE_URL/LLM_MODEL route to Mistral API directly.
    base_url = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1/chat/completions").rstrip("/")
    if base_url.endswith("/v1"):
        base_url += "/chat/completions"
    if "20128" in base_url:
        api_key = os.getenv("HERMES_CUSTOM_43_157_200_187_20128_API_KEY") or os.getenv("LLM_API_KEY")
    else:
        api_key = os.getenv("LLM_API_KEY") or _get_api_key()
    if not api_key:
        return None, "No API key found"
    model = model or os.getenv("LLM_MODEL", "mistral-large-latest")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": random.uniform(0.7, 0.9) if temperature is None else temperature,
        "max_tokens": 6000,
    }
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = httpx.post(base_url, headers=headers, json=payload, timeout=90)
            if r.status_code == 200:
                # Strip any trailing SSE sentinel (local proxy artefacts).
                body_text = r.text.strip()
                body_text = re.sub(r"\s*data: \[DONE\]\s*$", "", body_text)
                parsed = json.loads(body_text)
                content = (parsed["choices"][0]["message"].get("content") or "").strip()
                if content.startswith("data: "):
                    content = content[len("data: "):].strip()
                return content, None
            elif r.status_code == 401:
                return None, f"Auth error {r.status_code}"
            elif r.status_code == 429:
                # Provider quota is run-wide. Retry/candidate churn worsens 429.
                return None, "Rate limit 429"
            else:
                last_error = f"HTTP {r.status_code}: {r.text[:120]}"
                if attempt < max_retries:
                    delay = 2 + attempt  # Pressbox-style: 3s, 4s, 5s
                    time.sleep(delay)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            last_error = str(e)[:120]
            if attempt < max_retries:
                time.sleep(2 + attempt)
    return None, f"LLM failed: {last_error}"

# Active writer contract: RCTOE adapted to Techbro runtime and validators.
SYSTEM_PROMPT = """# ROLE
Kamu penulis analisis ekonomi untuk akun Threads Indonesia @ryanhadiii. Pahami ekonomi, lalu jelaskan dengan bahasa sehari-hari. Tulis seperti kreator ekonomi papan atas: tajam, dekat, observasional, konkret, dan punya sudut pandang. Suara lo berani beropini dan berpihak ke rakyat kecil bila fakta mendukung — semua klaim tetap literal dari artikel. Adopsi prinsip editorial, jangan menyalin kalimat referensi.

# EDITORIAL LANE
Pilih hanya topik ekonomi nasional atau internasional yang punya perubahan material: kebijakan, anggaran, pajak, subsidi, harga, upah, pekerjaan, perdagangan, industri, bisnis besar, pasar, atau guncangan ekonomi global. Prioritaskan peristiwa konkret yang punya rantai bukti ke sistem ekonomi atau kelompok manusia: kerusakan/keputusan/perubahan, lalu produksi, pasokan, biaya, pekerjaan, konsumen, atau pihak yang disebut artikel. Jangan tulis promo/event retail, tips personal finance, gosip korporasi, atau berita layanan rutin. Artikel internasional boleh tanpa kaitan Indonesia jika benar-benar berdampak pada ekonomi global; jangan memaksa kaitan Indonesia.

# PLAIN LANGUAGE
Tulis untuk pembaca umum dan pembaca awam, bukan ekonom. Hindari jargon teknis. Ganti dengan kata sehari-hari bila akurat. Jika istilah wajib dipakai, jelaskan artinya saat pertama disebut bila natural; jangan memaksa definisi. Jangan menumpuk istilah ekonomi dalam satu kalimat.

# LANGUAGE — HARD REQUIREMENT
Seluruh `post_1` sampai `post_6` wajib Bahasa Indonesia santai. Bahasa sumber boleh Inggris, tetapi jangan menyalin kalimat Inggris. Nama resmi dan istilah teknis boleh tetap asli; terjemahkan kalimat sekitarnya. Jika enam post Bahasa Indonesia tidak bisa ditulis tanpa mengarang fakta, balas `insufficient_evidence`.

# CONTEXT
Audiens masyarakat umum Indonesia, bukan investor. Ubah berita ekonomi kaku jadi cerita yang tajam, cepat, dan enak dibagikan. Jangan terdengar seperti ringkasan berita atau laporan korporat. Buka dengan angka, perubahan, kontras, kutipan, atau fakta paling mengganggu dari artikel. Pakai bahasa gw–lo, kalimat padat dan jelas, dan detail konkret. Opini boleh tegas jika fakta dan opini jelas terpisah. Jangan menambah fakta, angka, motif, atau dampak yang tidak ada di artikel.

# VOICE CONTRACT — TECHBRO
- Suara: conversational, tajam, konkret, sedikit nyeletuk. Tulis seperti menjelaskan temuan ekonomi ke satu teman cerdas, bukan seperti news anchor, siaran pers, atau esai kebijakan.
- Kalibrasi referensi positif: mulai dari benda, angka, keputusan, atau ucapan yang konkret; segera beri belokan conversational yang menyorot gap/kontras; tutup dengan judgment kecil yang terasa personal. Jangan meniru kalimat, persona, atau pengalaman referensi.
- Hook S1 dimulai dari fakta literal yang membuat pembaca berhenti: angka, perbandingan yang memang ada, kutipan, keputusan, atau kontradiksi nyata. Reaksi boleh muncul dulu, tetapi fakta harus ada di kalimat yang sama atau berikutnya. Jangan mulai dengan konteks panjang atau ringkasan headline.
- Jika sumber menyediakan kontras, buka dengan dua fakta literal yang saling menekan; jangan cuma melaporkan perubahan satu angka. Contoh mekanik: hasil terlihat bagus, tetapi ukuran lain memburuk. Jangan membuat kontras jika sumber hanya punya satu sisi.
- Pakai bahasa ngobrol secukupnya: lo, gue/gua, nah, tapi, padahal, soalnya, makanya. Sapaan hanya dipakai bila membuat kontras terasa lebih dekat; slang bukan hiasan wajib. Jangan memaksa lo/gue di setiap slide. JANGAN pakai emoji (ditolak gate kualitas).
- KALIBRASI THEODERICK (gaya reframe yang menang di Threads): reframe paradox — ambil dua fakta literal yang saling menekan, bungkus jadi pernyataan kontra-intuitif yang bikin orang berhenti ("Tuhan lebih peduli kondisi daripada performa" → di ekonomi: "Yang penting bukan seberapa besar angka, tapi siapa yang menanggungnya"). Kontra-intuitif HARUS lahir dari kontras literal artikel, bukan asumsi. Aksen khas theoderick WAJIB minimal 1 per thread: ges, ndak, gokil, bgt, krn, dg — pilih slide paling natural (umumnya S2/S3 saat membahas angka atau mekanisme), jangan di setiap slide. Contoh pemakaian natural: "Duitnya kabur ke mana? Krn ternyata bukan asing yang narik, tapi kita sendiri. Gokil bgt kan?" Struktur line-break: hook singkat, lalu ekspansi dengan jeda baris, lalu satu baris afirmasi/pertanyaan. Campur Inggris ringan hanya untuk kata kunci (progress, impact, growth, purpose, step by step); jangan kalimat Inggris utuh.
- Satu post satu pukulan: fakta konkret, belokan/kontras singkat, lalu reaksi atau judgment yang langsung ditopang fakta tersebut. Variasikan ritme; jangan membuat semua slide mengikuti pola fakta-artinya-dampak-kesimpulan. Satu thread satu cerita: S1-S6 harus tetap pada SATU isu dan SATU angle; jangan melompat ke isu/berita lain yang kebetulan ada di artikel (misal jangan masukin SpaceX di thread tentang Trump/Oman).
- S2-S5 harus menaikkan tensi dengan bukti baru: konsekuensi, pihak terdampak, pihak yang tetap untung, keputusan aktor, atau gap yang belum terjawab—hanya yang literal tersedia. Jangan mengulang premis dengan sinonim.
- Jika sumber menyebut aktor dan keputusan konkret, jadikan keputusan aktor itu objek penilaian; jangan mengarang motif atau menyederhanakan aktor yang tidak disebut.
- Pisahkan tiga lapis: FAKTA = apa yang artikel nyatakan; OPINI = penilaian lo yang jelas ditandai sebagai pandangan; TUDUHAN/MOTIF/AKIBAT = jangan tulis kecuali artikel menyatakannya. Jangan ubah kematian, biaya, atau keputusan menjadi klaim siapa yang rugi, siapa yang salah, atau kenapa tindakan terlambat tanpa bukti literal.
- Punchline harus berbasis evidence span. Jangan menambah motif, dampak, korban, prediksi, atau hubungan sebab-akibat demi terdengar tajam. Ironi atau sarkasme hanya boleh memakai kontras literal dari masalah nyata di artikel.
- Akui batas sumber secara natural: "yang belum jelas...", "artikel ini cuma menyebut...", atau "sumbernya belum menjelaskan...". Jangan mengisi lubang informasi dengan asumsi.
- POV boleh tegas bila lahir dari kontras literal. Orang pertama hanya untuk opini editorial, bukan pengalaman, investasi, percakapan, akses, atau fakta pribadi yang dibuat-buat.
- S6 hanya memakai CTA jika artikel memuat pilihan atau benturan konkret. CTA harus menyebut pilihan literal dari sumber dan meminta satu sumbu judgment yang gampang dijawab. SAAT pilihan/benturan literal ADA, rumuskan CTA sebagai perdebatan 2 sisi yang bisa dibantah: dua kubu yang masing-masing bisa dipertahankan (misal "Lo lebih condong ke mana: X atau Y?"), bukan pertanyaan yang gampang dijawab "iya" atau setuju-tidak-setuju datar. Satu kubu harus punya argumen tandingan yang bikin pembaca pengen nyaut. Jangan menambah opsi yang tidak ada di artikel; dua sisi harus dari sumber. Jangan mengubah CTA menjadi soal ujian kebijakan atau meminta pembaca merancang solusi. DILARANG format daftar pilihan A/B/multiple choice (misal "A. ... B. ..."); tulis CTA sebagai kalimat tanya naratif biasa, bukan enumerasi opsi.
- Dilarang: pembuka template seperti "2027 jadi tahun paling mahal", "alasannya?", atau "yang bikin gue mikir"; jargon kebijakan tanpa penjelasan; drama seperti "beban rakyat" atau "negara makin hancur"; daftar strategi panjang; opini abstrak; dan gaya formal news anchor.
- Jangan menyalin frase referensi. Adopsi prinsip ritme dan ketajaman, bukan kalimatnya. Grounding tetap lebih tinggi daripada gaya.

# HOOK & STRUKTUR — KALIBRASI AKSI
- S1 boleh dibuka sebagai masalah nyata yang pembaca alami (biaya, tagihan, harga, syarat, proses) SELAMA masalah itu literal di artikel, lalu janji konkret: thread ini kasih angka/jawaban/sudut yang beda. Janji harus ditepati di S2-S6; jangan janji kosong.
- Jika artikel memuat urutan, syarat, atau proses konkret (bertahap, alur, pembagian kewenangan), gunakan struktur aksi singkat: langkah/fakta paling penting dulu, lalu konsekuensi. Satu slide tetap satu pukulan; jangan mengubah post jadi tutorial panjang.
- Sorot pihak yang diuntungkan dan pihak yang menanggung biaya HANYA bila artikel menyebut keduanya atau dasarnya literal. Angle "institusi bikin susah" boleh muncul sebagai opini tegas bila artikel memuat fakta yang menopangnya (biaya tersembunyi, syarat berbelit, keputusan yang merugikan kelompok); jangan menuduh motif bila tidak tertulis.
- Mekanik skeptis-ke-data: boleh buka "sempat ngira X, ternyata data bilang Y" bila artikel punya kontras literal antara asumsi umum dan angka. Tunjukkan proses berpikir singkat (ragu → cek angka → kesimpulan), jangan pura-pura ragu.
- Angka konkret dulu, tafsir belakangan. Kalau artikel punya angka, taruh angkanya di kalimat pembuka atau segera setelah reaksi; interpretasi mengikuti.
- Penutup S6 boleh mengajak pembaca membandingkan pengalaman nyatanya dengan satu fakta dari artikel ("pernah ngerasain X?"), bila fakta itu literal. Ini berbeda dari pertanyaan moral generik — harus menyebut elemen konkret dari sumber.

# POLA TERBUKTI (dari analisis post viral @ryanhadiii)
- Analogi sehari-hari 1 kalimat: jelaskan angka/statistik dengan gambaran akrab ("pesta mewah di tengah kampung paceklik", "mesin jalan tapi olinya bocor"). Analogi hanya untuk memperjelas, JANGAN menambah fakta, angka, pihak, atau hubungan sebab-akibat yang tidak ada di artikel.
- Pertanyaan retoris boleh dipakai sebagai bridge di S2-S5: satu pertanyaan menggantung ("Siapa yang sebenarnya untung?", "Uangnya kabur ke mana?") untuk memancing lanjut baca; jawabannya harus tersedia di slide berikutnya atau di akhir thread, dan pertanyaan tidak boleh menuduh motif tanpa bukti literal.
- Tease aktor: bila artikel menyebut aktor bernama, S1 boleh memberi hint deskriptif tanpa nama ("tiga orang ngobrol") dan membuka nama di S2. Jangan menahan nama bila S1 tidak punya deskripsi kuat; jangan mengarang ciri aktor.

# VOICE SAFETY
- ISI ARTIKEL dan evidence plan adalah batas fakta. Voice tidak boleh memperluas evidence.
- Opini boleh tegas, tetapi opini tidak boleh mengubah fakta: jangan membalik arah fakta (misal artikel bilang X naik, opini tidak boleh bilang X turun), jangan menambah angka, jangan mengganti nama/pihak, jangan menciptakan hubungan sebab-akibat. Opini adalah penilaian terhadap fakta, bukan revisi fakta.
- Jika voice tajam bertentangan dengan evidence, buang punchline, bukan evidence gate.

# TASK
Ubah satu artikel sumber menjadi 6 post Threads yang saling tersambung. Gunakan satu ISI ARTIKEL sebagai sumber tunggal. ISI ARTIKEL satu-satunya sumber. Kata sambung boleh diparafrasekan; jangan mengganti atau menambah makna. Jelaskan sebab-akibat hanya jika hubungan itu tertulis atau jelas dinyatakan artikel; jika artikel tidak menjelaskan sebab atau dampak, nyatakan batas informasi tersebut, jangan mengarang.

Fungsi post:
Pilih arc sesuai bukti sumber: kebijakan, household impact (harga/upah/daya beli), public money, supply shock, atau market decision. Jangan pakai arc kebijakan untuk semua artikel. Jangan gunakan label `wallet_pressure`; promo retail tidak boleh diubah menjadi tekanan ekonomi rumah tangga.
Buat satu STORY SPINE sebelum menulis: satu perubahan/konflik/status gap yang benar-benar tertulis. SATU THREAD = SATU CERITA: seluruh S1-S6 membahas SATU isu utama dengan SATU angle saja; jika artikel memuat beberapa isu, pilih SATU yang paling penting bagi pembaca dan jangan mencampur isu lain (misal jangan membahas SpaceX di thread tentang Trump/Oman). S1 membuka reaksi, observasi, atau ketegangannya, bukan sekadar "X bilang Y". S2-S5 tidak punya fungsi tetap; tiap slide memilih satu bukti atau benturan berbeda dari artikel. Jangan memaksa keputusan, mekanisme, angka pembanding, pihak terdampak, atau trade-off bila sumber tidak menyediakannya. S6 kembali ke ketegangan S1 dan memberi dua pilihan yang benar-benar ada di artikel bila tersedia. Jika tidak ada pilihan atau benturan konkret, tutup dengan simpulan editorial. Simpulan harus spesifik berbasis fakta. Untuk lane internasional, jelaskan kanal dampak Indonesia hanya jika kalimat sumber menghubungkannya.
1. HOOK — S1 maksimal 480 karakter. Mulai dari reaksi atau observasi bila fakta mendukung, lalu tampilkan angka, konflik, perubahan, kontras, kutipan, atau konsekuensi yang tertulis di artikel. Jika tidak memakai reaction-first, Buka dengan fakta paling mahal dan fakta paling kuat; buat kalimat pertama menyampaikan fakta. Ambil sisi dari fakta; opini tegas boleh selama tidak menambah klaim. Sisakan curiosity gap yang jawabannya ada di S2-S6. Jangan mulai dengan lead berita biasa, "menurut laporan", atau deskripsi gambar.

Jangan menyebut PHK, nasib karyawan, kompensasi, atau penempatan ulang kecuali literal ada di ISI ARTIKEL.
2-5. BUKTI BERBEDA — pilih fakta, mekanisme, pihak, angka, konteks, atau batas informasi yang tersedia. hitung-hitungan pelaksanaan dan biaya hanya boleh masuk bila sumber menyediakannya. S5 boleh menunjukkan beban/keuntungan antar pihak bila literal di artikel. Tidak semua jenis bukti wajib muncul. Jangan mengubah ketiadaan informasi menjadi klaim.
6. CLOSING — simpulan singkat. Tambahkan satu pertanyaan JUDGMENT spesifik hanya jika pilihan/benturan nyata ada di artikel; jika tidak, jangan memaksa CTA. Pertanyaan boleh membandingkan dua pihak/risiko/dampak yang ADA di artikel, bukan istilah abstrak. SAAT pilihan/benturan literal ADA, S6 WAJIB memancing perdebatan 2 sisi yang bisa dibantah: rumuskan pertanyaan yang membelah pembaca ke dua kubu (misal "Lo lebih condong ke mana: X atau Y?" di mana X dan Y dua opsi/risiko literal dari artikel), bukan pertanyaan setuju-tidak-setuju yang gampang dijawab "iya". Satu kubu harus punya downside/argument yang bikin pembaca pengen ngebantah. Jangan menambah opsi yang tidak ada di artikel; dua sisi harus dari sumber. DILARANG format daftar pilihan A/B/multiple choice (misal "A. ... B. ..."); tulis CTA sebagai kalimat tanya naratif biasa, bukan enumerasi opsi.
7. SOURCE — sistem menambahkan `post_7` berisi URL artikel canonical. Jangan menulis URL di S1-S6.

# RULES
- Jangan menambah dampak, profesi, angka, skenario, motif, status resmi, timeline, penilaian, nama, lokasi, tanggal, sebab-akibat, prediksi, atau kutipan. Jangan menambah dampak, profesi, angka, skenario, penilaian baru.
- SUMBER ADALAH BATAS. ISI ARTIKEL satu-satunya sumber. Jangan membuat fakta baru.
- Dilarang memakai emoji, emotikon, atau ASCII emoticon di post_1 sampai post_6. Gunakan kata, bukan simbol.
- Nama institusi, orang, dan label kejadian (termasuk bencana) WAJIB persis dari artikel. Jangan menambah, mengganti, menyingkat, atau mengarang singkatan/label baru.
- Kalimat pertama boleh berupa reaksi, observasi, atau fakta paling kuat. Jika dibuka dengan reaksi, fakta literal harus muncul di kalimat yang sama atau berikutnya.
- Minimal dua slide memiliki POV editorial eksplisit: S1 wajib punya reaksi atau observasi berbasis fakta; satu slide lain boleh memberi judgment berbasis fakta. Jangan memaksa gw/lo di setiap slide. POV hanya boleh lahir dari fakta/kontras literal artikel; jika artikel tidak memuat kontras atau konflik yang cukup, slide boleh TANPA POV editorial — laporkan fakta dengan bersih. JANGAN memproduksi konflik, skenario hipotesis ("tetep bisa", "bayangin kalau", "bakal"), atau judgment yang tidak tertopang sumber.
- Jangan ulang angka, fakta, atau contoh. jangan ulang angka, fakta, atau contoh dalam slide lain. Jika fungsi sebab/dampak/relevansi tidak punya bukti, gunakan bukti lain yang tersedia; jangan mengisi bagian kosong dengan tebakan. Jangan ulang angka, fakta, atau contoh tanpa bukti berbeda dari artikel.
- Untuk kebijakan: gunakan opsi resmi + kelompok terdampak + status belum final hanya jika literal; jelaskan pembagian kewenangan serta dasar aturan bila tertulis.
- Jangan mengubah satuan atau menghitung angka baru.
- Grounding per-slide: setiap angka, nama, perbandingan, istilah spesifik, atau klaim di post WAJIB salin literal dari ISI ARTIKEL; jangan parafrase fakta baru dan jangan mengisi slide tanpa fakta literal dengan kalimat umum. Lebih baik slide pendek berisi fakta literal daripada slide panjang berisi tafsir tanpa sumber.
- Parafrase boleh jika makna tetap sama. Istilah/konsep kunci dari artikel (misal "biaya kendaraan", "bahan bakar impor") TIDAK BOLEH diganti sinonim yang mengubah cakupan: "biaya kendaraan" ≠ "harga motor", "bahan bakar impor" ≠ "BBM". Pertahankan minimal satu kata kunci literal dari istilah sumber (biaya/kendaraan, bahan bakar/impor). Jangan menyingkat istilah resmi jadi akronim baru yang tidak ada di artikel.
- Setiap post wajib minimal 1 kalimat jelas dan maksimal 480 karakter. Fragment pendek atau ellipsis boleh sebagai bagian dari ritme percakapan bila maknanya tetap jelas. Satu ide utama per post. Slide lebih panjang BOLEH bila setiap kalimat menambah bukti/judgment baru; jangan menambah slide demi padding.
- Utamakan bahasa sehari-hari. Istilah teknis boleh dipakai bila membuat kalimat lebih tajam; jelaskan bila natural, jangan memaksa definisi.
- Bahasa Indonesia santai, jelas, dan natural. Jargon bukan alasan untuk mengubah voice jadi penjelasan buku teks. Hindari slogan, hashtag, URL, dan pembuka generik.
- Jangan memaksa bagian sebab, dampak, atau relevansi jika bukti tidak tersedia.
- Jangan menulis label slide di dalam teks post.
- Balas JSON valid saja. Sistem menambahkan post_7 berisi sumber.

# EDITORIAL BOUNDARY
Tegangan hanya boleh datang dari perbandingan atau perubahan yang literal di artikel. Jangan memancing dengan teka-teki karangan. Curiosity gap BOLEH asal dari ketegangan literal artikel — sisakan pertanyaan yang jawabannya ADA di S2-S6, bukan keraguan palsu. Tidak perlu memaksa satu jenis fakta ke slide tertentu. Jangan pakai label-colon, hashtag, jargon birokratis, template AI. Hindari slogan, kalimat motivasi, atau kesimpulan yang terdengar besar. Gaya tajam bukan izin untuk mengarang dampak.

# OPINI BERPIHAK — BOLEH, TAPI JANGAN MENGARANG
Ambil posisi secara eksplisit bila fakta mendukung: sorot konsumen, pekerja, wajib pajak, rumah tangga, atau pihak yang menanggung biaya. Opini/penilaian BOLEH selama tidak menambah kerugian, motif, korban, emosi, atau dampak yang tidak tertulis. Bedakan jelas fakta artikel vs opini lo. Jangan mengubah pajak menjadi klaim bahwa pembaca harus bayar, dompet/kantong terkena, kas negara terisi, atau utang/efisiensi menjadi alternatif kecuali hubungan itu literal di artikel. Jangan otomatis berpihak jika sumber tidak memberi dasar. Penutup boleh memancing judgment nyata, misal: “Menurut lo, ini adil atau berpihak ke siapa?” atau “Menurut lo, biaya ini layak dibayar untuk hasil itu?” hanya jika kedua unsur literal ada di artikel.
Jika artikel menyebut daerah, bencana, PAD, petani, pekerja, konsumen, atau rumah tangga, usahakan S2-S5 menjelaskan kelompok tersebut berdasarkan ISI ARTIKEL dan gunakan opini orang pertama bila membantu. Ini kualitas editorial, bukan izin menambah klaim. Jangan menyalahkan kelompok atau mengarang hubungan ekonomi. Dilarang memakai “uang pajak kita”, “daerah nggak bisa cari duit sendiri”, “dianakemaskan”, “siapa yang sebenarnya bayar”, “warga pasti terdampak”, “Rp0 uang negara”, “nama Indonesia di mata global”, atau “startup lokal kalah sama perusahaan luar negeri” kecuali hubungan itu literal di sumber.

# OUTPUT
{"status":"success","angle":"sudut pandang","post_1":"...","post_2":"...","post_3":"...","post_4":"...","post_5":"...","post_6":"..."}

Jika artikel tidak memiliki cukup bukti untuk 6 post akurat:
{"status":"error","message":"insufficient_evidence"}
"""

REVISION_PROMPT = """PERBAIKI HANYA field yang disebut di bawah. JANGAN ubah field lain. JANGAN membuat ulang slide yang tidak disebut issue.
{revision_notes}

ATURAN REVISI:
1. hapus seluruh frasa yang disebut issue; ganti dengan fakta yang muncul literal di ISI ARTIKEL. Jangan menambah angka, nama, label penilaian, motif, dampak, atau klaim baru di luar ALLOWLIST di bawah.
2. Gunakan fakta yang muncul literal di ISI ARTIKEL; jangan parafrase fakta baru, jangan mengubah satuan atau menghitung angka baru.
3. Nama/entitas: HANYA pakai nama dari daftar NAMA/ENTITAS LITERAL. JANGAN tambah nama baru. Institution/acronym: JANGAN mengarang istilah yang tidak ada di artikel — HAPUS saja.
4. STOP-SLOP: hindari pembuka laporan, transisi bertele-tele, kontras formulaik, hedge samar, rujukan gambar, dan kalimat pasif.
5. S6: CTA hanya jika pilihan/benturan konkret literal di artikel; jika tidak, pertahankan simpulan editorial. Jika tidak bisa perbaiki tanpa invent nama/angka/label baru, kembalikan ke value asli field tersebut.
6. S1 maksimal 480 karakter. Seluruh post wajib Bahasa Indonesia. KALIBRASI AKSI: jangan ringkasan berita; ubah jadi masalah nyata pembaca + angka/fakta literal. SKEPTIS-KE-DATA: mekanik "sempat ngira X, ternyata data bilang Y" hanya bila artikel punya kontras literal; Jangan pura-pura ragu tanpa kontras di sumber. PENUTUP: S6 boleh membandingkan pengalaman pembaca dengan elemen konkret artikel bila literal; jangan pertanyaan moral generik. Jangan menuduh motif institusi tanpa fakta tertulis.
7. RETURN TO ORIGINAL: jika tidak ada perbaikan yang aman, kembalikan ke value asli field tersebut. Jangan tambah apa-apa.

Jika tidak ada enam post yang bisa dipertahankan akurat dan memenuhi aturan di atas, balas {{"status":"error","message":"insufficient_evidence"}}."""


def build_revision_prompt(revision_notes, posts, article=None):
    """Give revision model current draft so it patches, not rewrites, slides."""
    fields = sorted(set(re.findall(r"post_[1-6]", revision_notes or "")), key=lambda key: int(key.split("_")[1]))
    field_scope = ", ".join(fields) if fields else "field yang tidak lolos validasi"
    safe_notes = (f"Perbaiki hanya {field_scope}. Hapus nama, angka, perbandingan, atau klaim "
                  "yang tidak literal di ISI ARTIKEL. Pertahankan fakta valid.")
    draft = {"status": "success"}
    draft.update({f"post_{i}": posts.get(f"post_{i}", "") for i in range(1, 7)})
    source = ""
    if article:
        body = article.get("body", "")[:25000]
        facts = literal_fact_allowlist(body)
        entities = literal_entity_allowlist(body)
        source = "\n\nSUMBER REVISI — FAKTA LITERAL SAJA:\n" + "\n".join(
            ["ALLOWLIST FAKTA LITERAL:"]
            + [f"- {fact}" for fact in facts]
            + ["NAMA/ENTITAS LITERAL:"]
            + [f"- {entity}" for entity in entities]
        )
    context = "\n\n" + safe_notes + source
    return REVISION_PROMPT.format(revision_notes=safe_notes) + context + "\n\nDRAFT SAAT INI:\n" + json.dumps(draft, ensure_ascii=False)


def _parse_llm_json(content):
    """Parse JSON object from plain, fenced, or prose-wrapped LLM output."""
    if not isinstance(content, str):
        return None
    text = re.sub(r"```(?:json)?\s*|\s*```", "", content.strip(), flags=re.I)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _source_fallback_posts(article):
    """Build grounded fallback with winning six-slide story jobs, not article-order pairs."""
    sentences = [re.sub(r"^[\\\"'“”]+|[\\\"'“”]+$", "", s).strip()
                 for s in _source_sentences(article.get("body", ""))]
    sentences = [s for s in sentences if len(s) <= SLIDE_CHAR_LIMIT]
    if len(sentences) < 12:
        return None
    pattern = article.get("pattern", "")
    if pattern == "KEBIJAKAN" and _policy_winner_enabled(article):
        roles = [(slide, POLICY_WINNING_ROLES[slide]) for slide in ("post_1", "post_2", "post_3", "post_4", "post_5", "post_6")]
    else:
        # Pressbox-style extractive fallback: source order, two complete facts/slide.
        # No role optimizer; no invented arc evidence.
        numeric = re.compile(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", re.I)
        weak = ("hal ini", "yang tak kalah", "yang tidak kalah", "misal ", "ujar ",
                "ucap ", "kata ", "sambung ", "begitu juga", "selanjutnya",
                "kemudian harga", "kemudian angka", "kemudian data")
        promo = ("dialog eksklusif", "forum", "konferensi", "webinar", "summit",
                 "cnbc menghadirkan", "acara didukung", "pantau terus", "jangan lupa",
                 "saksikan", "secara live", "program squawk box", "program tersebut",
                 "cnbcindonesia.com", "cnbc indonesia tv", "update informasi")
        dangling_opener = re.compile(r"^\W*[\w\s'\"“”’.()]{1,60}\s+(ini|tersebut|demikian|begini|begitu)\b", re.I)
        def usable(sentence):
            low = sentence.lower().strip()
            if low.startswith(weak) or any(term in low for term in promo):
                return False
            if re.search(r",\s*[\"'“”]?\s*(?:kata|ujar|ucap)\b\s*[^.]*$", low):
                return False
            if re.search(r"\b(?:dalam rilis|dikutip)\b", low):
                return False
            if re.search(r"\b(?:kata|ujar|ucap)\b.*\s+[a-z]\.$", low):
                return False
            if low.endswith((",", ":", "-")) or low.count('"') % 2:
                return False
            return len(sentence) >= 45
        pairs = []
        remaining = [s for s in sentences if usable(s)]
        # Reserve two source sentences for S6 before greedy allocation. This keeps
        # CTA evidence non-numeric and prevents early slides consuming all valid pairs.
        s6_choices = []
        for i, first in enumerate(remaining):
            for j, second in enumerate(remaining[i + 1:], i + 1):
                text = f"{first} {second}"
                if len(text) <= SLIDE_CHAR_LIMIT and not numeric.search(text):
                    s6_choices.append((i, j, text))
        if not s6_choices:
            return None
        i6, j6, s6_text = s6_choices[-1]
        reserved = {i6, j6}
        remaining = [s for n, s in enumerate(remaining) if n not in reserved]
        for slide in range(5):
            choices = []
            for i, first in enumerate(remaining):
                for j, second in enumerate(remaining[i + 1:], i + 1):
                    text = f"{first} {second}"
                    limit = SLIDE_CHAR_LIMIT if slide == 0 else SLIDE_CHAR_LIMIT
                    if len(text) <= limit:
                        choices.append((i, j, text))
            clean_choices = [choice for choice in choices if not dangling_opener.match(choice[2].strip())]
            if clean_choices:
                choices = clean_choices
            if not choices:
                return None
            if slide == 0:
                # S1 hook gate: prefer pairs with a concrete signal (number,
                # past-tense change, named contrast) over raw article leads like
                # "REPUBLIKA.CO.ID, JAKARTA -- Bank Indonesia memproyeksikan...".
                # Raw leads are journalism, not hooks — they don't stop scroll.
                # Also drop journalism datelines ("MEDIA, CITY --") from S1 pool.
                dateline = re.compile(
                    r"^[A-Z][A-Za-z.]*(?:\.co\.id|\.com)?,\s*[A-Z][A-Za-z]+\s*--",
                    re.I,
                )
                no_dateline = [c for c in choices if not dateline.match(c[2].strip())]
                hook_signal = re.compile(
                    r"(?:rp\s*)?\d|persen|%|miliar|juta|triliun|"
                    r"\b(memutuskan|menaikkan|menurunkan|menghentikan|melarang|"
                    r"menolak|mengumumkan|menetapkan|membatalkan|mengubah|"
                    r"menyebutkan angka|turun|naik|melonjak|anjlok)\b",
                    re.I,
                )
                weak_start = re.compile(
                    r"^\s*(sementara itu|selain itu|kemudian|selanjutnya|di sisi lain|"
                    r"sementara|adapun|terkait hal ini|dalam keterangan)",
                    re.I,
                )
                # Strong S1: no weak transition opener AND signal in FIRST sentence.
                strong = [
                    c for c in no_dateline
                    if not weak_start.match(c[2].strip())
                    and hook_signal.search(c[2].split(". ")[0])
                ]
                hook_choices = [c for c in no_dateline if hook_signal.search(c[2])]
                if strong:
                    choices = strong
                elif hook_choices:
                    choices = hook_choices
                elif no_dateline:
                    choices = no_dateline
            i, j, text = choices[0]
            pairs.append(text)
            remaining = [s for n, s in enumerate(remaining) if n not in (i, j)]
        pairs.append(s6_text)
        body_lower = article.get("body", "").lower()
        # Generic terms ("ekonomi", "pembahasan", "pertumbuhan") create empty CTAs.
        # Require two concrete source-backed stakes instead.
        cta_terms = ("konsumsi", "investasi", "belanja pemerintah", "rumah tangga",
                     "industri", "pasar", "biaya", "risiko", "anggaran", "aturan",
                     "bantuan", "penerima", "kredit", "laba", "pajak", "subsidi",
                     "harga", "gaji", "upah", "daya beli", "konsumen", "peternak",
                     "pedagang", "daerah", "pusat", "keuntungan", "kerugian")
        options = [term for term in cta_terms
                   if re.search(r"\b" + re.escape(term) + r"\b", body_lower)]
        cta = (f"Menurut lo, lebih penting memantau {options[0]} atau {options[1]}?"
               if len(options) >= 2 else None)
        posts = {f"post_{i}": pairs[i - 1] for i in range(1, 6)}
        if not cta:
            concrete = ("investor", "investasi", "biaya", "anggaran", "aturan", "harga", "upah", "gaji", "pasar", "rumah tangga", "konsumen", "pajak", "subsidi", "risiko")
            if not any(term in body_lower for term in concrete):
                return None
        posts["post_6"] = f"{pairs[5]} {cta}" if cta else pairs[5]
        return posts

    weak_prefixes = (
        "hal ini", "yang tak kalah", "yang tidak kalah", "misal ", "ujar ",
        "ucap ", "kata ", "sambung ", "begitu juga", "selanjutnya",
        "kemudian harga", "kemudian angka", "kemudian data",
    )

    def score(sentence, signals):
        text = sentence.lower()
        value = sum(3 for signal in signals if signal in text)
        if re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", text, re.I):
            value += 2
        if '"' in sentence or "“" in sentence:
            value += 1
        if text.startswith(weak_prefixes):
            value -= 10
        if pattern == "KEBIJAKAN" and signals == POLICY_WINNING_ROLES["post_5"]:
            value += sum(4 for marker in POLICY_TRADEOFF_MARKERS if marker in text)
        return value

    remaining = list(sentences)
    pairs = []
    reserved_tradeoff = None
    if pattern == "KEBIJAKAN":
        tradeoff_sentence = _policy_tradeoff_sentence(article.get("body", ""))
        if tradeoff_sentence:
            reserved_tradeoff = tradeoff_sentence
    for slide, signals in roles:
        limit = SLIDE_CHAR_LIMIT if not pairs else SLIDE_CHAR_LIMIT
        pool = remaining
        if reserved_tradeoff and slide != "post_5":
            pool = [sentence for sentence in remaining if sentence != reserved_tradeoff]
        choices = [
            (score(a, signals) + score(b, signals), i, j, a, b)
            for i, a in enumerate(pool)
            for j, b in enumerate(pool[i + 1:], i + 1)
            if len(a) + len(b) + 1 <= limit
            and (slide not in ("post_6", "open") or not re.search(r"(?:rp\s*)?\d|\d+\s*(?:persen|%|miliar|juta|triliun)", f"{a} {b}", re.I))
        ]
        if reserved_tradeoff and slide == "post_5" and reserved_tradeoff in remaining:
            choices = [choice for choice in choices if reserved_tradeoff in choice[3:]] or choices
        if not choices:
            return None
        _, i, j, a, b = max(choices, key=lambda item: item[0])
        if score(b, signals) > score(a, signals):
            a, b = b, a
        pairs.append(f"{a} {b}")
        remaining = [s for s in remaining if s not in {a, b}]

    cta_terms = {
        "KEBIJAKAN": ("biaya", "anggaran", "aturan", "bantuan", "penyaluran", "pembahasan", "target"),
        "PERDAGANGAN": ("harga", "pasokan", "impor", "logistik", "biaya", "stok"),
        "PROYEK": ("hasil", "investasi", "laba", "efisiensi", "biaya", "pelaksanaan"),
        "PASAR": ("harga", "rupiah", "saham", "pasar", "dolar"),
        "KORUPSI": ("anggaran", "kerugian", "biaya", "proyek", "hasil"),
    }
    body_lower = article.get("body", "").lower()
    options = [term for term in cta_terms.get(pattern, ()) if re.search(r"\b" + re.escape(term) + r"\b", body_lower)]
    if pattern == "KEBIJAKAN":
        cta_pairs = (("pusat", "daerah"), ("anggaran", "pemerataan"), ("biaya", "manfaat"),
                     ("persetujuan", "pembahasan"), ("aturan", "pembahasan"))
        pair = next((pair for pair in cta_pairs if all(re.search(r"\b" + re.escape(term) + r"\b", body_lower) for term in pair)), None)
        if pair:
            cta = f"Menurut lo, yang harus diprioritaskan: {pair[0]} atau {pair[1]}?"
        elif len(options) >= 2:
            cta = f"Menurut lo, yang harus diprioritaskan: {options[0]} atau {options[1]}?"
        else:
            cta = None
    elif len(options) >= 2:
        cta = f"Menurut lo, yang harus diprioritaskan: {options[0]} atau {options[1]}?"
    else:
        cta = None
    posts = {f"post_{i}": pairs[i - 1] for i in range(1, 6)}
    posts["post_6"] = f"{pairs[5]} {cta}" if cta else pairs[5]
    if any(len(text) > SLIDE_CHAR_LIMIT for text in posts.values()):
        return None
    return posts


def literal_fact_allowlist(body):
    """Literal body sentences are the only permitted facts for writer and revision.

    Bounded by characters (25k) instead of sentence count so late-article facts
    (impact, quotes, next steps) stay available to the writer.
    """
    sentences = re.split(r"(?<=[.!?])\s+", _clean_source_body(body))
    selected = [sentence for sentence in sentences if _usable_source_sentence(sentence)]
    joined = " ".join(selected)
    if len(joined) <= 25000:
        return selected
    head, tail, total = [], [], 0
    for s in selected:
        if total + len(s) <= 12500:
            head.append(s)
            total += len(s)
        else:
            break
    tail_total = 0
    for s in reversed(selected):
        if tail_total + len(s) <= 12500:
            tail.append(s)
            tail_total += len(s)
        else:
            break
    return head + list(reversed(tail))


def source_slide_audit(body, posts):
    """Evidence audit; source sentence anchors are hard publication evidence."""
    source_sentences = _source_sentences(body)
    stop = {"yang", "dan", "atau", "ini", "itu", "dari", "untuk", "dengan", "ke", "di", "sebuah", "ada", "lo", "gue", "gua", "menurut"}

    def terms(text):
        return {w for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in stop}

    audit = {}
    for key, post in posts.items():
        post_terms = terms(post)
        matches = []
        shared = set()
        for index, sentence in enumerate(source_sentences, 1):
            overlap = post_terms & terms(sentence)
            if len(overlap) >= 2:
                matches.append(index)
                shared.update(overlap)
        audit[key] = {
            "lexical_match": bool(matches),
            "source_sentences": matches,
            "shared_terms": sorted(shared),
            "audit": "lexical overlap only; not a grounding verdict",
        }
    return audit


def literal_entity_allowlist(body):
    """Proper nouns and institutions literally present in the article body.
    Writer must reuse these verbatim; invented names fail noun validation."""
    if not body:
        return []
    text = re.sub(r"https?://\S+|www\.\S+", " ", body)
    entities = set(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", text))
    entities |= set(re.findall(r"\b[A-Z]{2,}\b", text))
    drop = {"Data", "Menurut", "Padahal", "Kalau", "Kalo", "Yang", "Dan", "Tapi",
            "Karena", "Sumber", "Jadi", "Namun", "Bahkan", "Pertama", "Bukan",
            "Setelah", "Sebelum", "Dengan", "Untuk", "Dari", "Lalu", "Sementara",
            "Sedangkan", "Risikonya", "Soalnya", "Alasan", "Alasannya",
            "URL", "HTTP", "HTTPS", "WWW", "COM", "CO", "ID", "ORG", "NET"}
    clean = sorted(e for e in entities if e not in drop)
    # Full-article coverage: keep head+tail entities (no [:40] first-only cap)
    # so late-article names/institutions reach the writer and validator.
    if len(clean) > 40:
        clean = clean[:20] + clean[-20:]
    return clean


def build_user_prompt(article):
    """Pass FULL article text plus evidence-plan facts; code owns validation.

    Facts in the post must come from the complete article body. We send the
    full cleaned body so the writer never needs to invent late-article facts.
    """
    plan = evidence_plan(article)
    body = " ".join(plan["units"])
    facts = literal_fact_allowlist(body)
    entities = literal_entity_allowlist(body)
    claim_map = source_claim_map({**article, "body": body})
    cta_evidence = []
    for slide in ("post_5", "post_6"):
        cta_evidence.extend(claim["sentence"] for claim in claim_map.get(slide, []))
    cta_evidence = list(dict.fromkeys(cta_evidence))[:4]
    claim_lines = ["CLAIM MAP S1-S6:"]
    for slide in [f"post_{i}" for i in range(1, 7)]:
        claims = claim_map.get(slide, [])
        claim_lines.append(f"{slide}: " + " | ".join(c["sentence"] for c in claims))
    return "\n".join([
        "ARTIKEL SUMBER:",
        "",
        "ALLOWLIST FAKTA LITERAL:",
        *[f"- {fact}" for fact in facts],
        "",
        "NAMA/ENTITAS LITERAL:",
        *[f"- {entity}" for entity in entities],
        "",
        *claim_lines,
        f"LANE: {_story_lane(article.get('title', ''), article.get('body', ''))}",
        f"KANAL DAMPAK INDONESIA: {_international_impact_channel(article.get('title', ''), article.get('body', '')) or 'tidak ada'}",
        f"EDITORIAL LENS: {_editorial_lens(article.get('title', ''), article.get('body', ''))}",
        "LENS WAJIB: gunakan lens ini sebagai cara memilih fakta dan judgment, bukan sebagai izin menambah klaim.",
        "VOICE CONTRACT AKTIF: conversational, tajam, konkret, sedikit nyeletuk; bukan news anchor atau esai kebijakan.",
        "POV EDITORIAL: S1 wajib punya reaksi/judgment; minimal satu slide lain harus punya POV editorial eksplisit (misal \"siapa yang beneran kena\", \"uang siapa yang hilang\", \"siapa yang untung\"). POV boleh tegas HANYA bila lahir dari kontras literal artikel; jangan mengarang motif, korban, atau pihak terdampak yang tidak ada di CLAIM MAP. Jika artikel tidak memuat kontras yang cukup, slide boleh TANPA POV — jangan memproduksi skenario hipotesis (\"tetep bisa\", \"bayangin kalau\", \"bakal\", \"nggak peduli\") atau judgment yang tidak tertopang sumber.",
        "KALIBRASI THEODERICK: reframe paradox — bungkus dua fakta literal yang saling menekan jadi pernyataan kontra-intuitif (kontras HARUS dari artikel, bukan asumsi); aksen khas ges/ndak/gokil/bgt/krn/dg WAJIB minimal 1 per thread, letakkan di slide paling natural (S2/S3), jangan tiap slide; struktur hook singkat → ekspansi → afirmasi; campur Inggris ringan hanya kata kunci (progress, impact, growth).",
        "HOOK: mulai S1 dari angka, keputusan, kutipan, kontras, atau fakta literal paling mengganggu. Reaksi boleh dulu, tetapi fakta harus muncul di kalimat yang sama atau berikutnya. PRIORITAS HOOK: jika CLAIM MAP memuat angka spesifik (nominal rupiah, persen, jumlah orang) yang kontras dengan ekspektasi umum, buka S1 dengan angka itu (contoh: 'Rp2,71 triliun, melonjak 804,7%'; 'Rp17.100 per saham padahal cuma rumor'). Angka di S1 TERBUKTI menaikkan performa; hindari membuka dengan kata tanya atau pengantar berita.",
        "KONTRADIKSI: jika CLAIM MAP memuat dua fakta literal yang saling menekan, pasangkan di S1; jangan cuma melaporkan perubahan satu angka dan jangan menciptakan kontras baru.",
        "PROGRESI: tiap slide menaikkan tensi dengan bukti baru; jangan mengulang premis memakai sinonim. Gunakan konsekuensi, pihak, keputusan aktor, atau gap hanya jika ada di CLAIM MAP.",
        "RITME: satu slide satu pukulan. Fakta konkret dulu, lalu satu kontras, reaksi, atau judgment berbasis evidence span. Variasikan panjang dan struktur slide. Slide boleh lebih panjang (hingga 480 karakter) selama setiap kalimat menambah bukti/judgment baru; jangan padding.",
        "BAHASA: pakai lo/gue/gua/nah/tapi/padahal/soalnya/makanya hanya bila natural; jangan memaksa slang di setiap slide.",
        "BATAS VOICE: jangan membuat punchline dari motif, dampak, korban, prediksi, hubungan sebab-akibat, atau lubang informasi yang tidak ada di evidence plan.",
        "LARANGAN VOICE: pembuka template, jargon kebijakan tanpa penjelasan, drama buatan, daftar strategi, opini abstrak, news-anchor framing, dan CTA moral generik.",
        "PILIHAN CTA BERBASIS BUKTI (opsional):",
        *[f"- {sentence}" for sentence in cta_evidence],
        "Jika CTA dipakai, pilih dua pihak, biaya, manfaat, risiko, status, atau dampak yang muncul literal di ISI ARTIKEL.",
        "CTA: minta satu judgment sederhana yang gampang dijawab; jangan meminta pembaca merancang kebijakan atau memilih prioritas teknis.",
        "Jangan membuat pilihan CTA dari istilah abstrak atau taruhan baru. Jika tidak ada pilihan konkret, tutup dengan simpulan editorial berbasis fakta.",
        "Jangan menambah klaim di luar CLAIM MAP. Jangan membuat fakta baru.",
        "S1 wajib punya reaksi atau observasi editorial berbasis fakta + curiosity gap dari fakta literal (bukan teka-teki). Satu slide lain boleh punya POV personal bila judgment dapat ditarik langsung dari artikel. S6 memakai pertanyaan judgment hanya bila pilihan konkret tersedia.",
        "Nama/entitas hanya boleh memakai allowlist; dilarang membuat frasa nama baru.",
        "Pakai bahasa warung: ganti istilah teknis dengan kata sehari-hari bila makna tetap akurat. Jika istilah teknis wajib, jelaskan artinya dalam kalimat yang sama.",
        "Dilarang membuat perbandingan/ekuivalensi baru: setara, hampir dua kali, dua kali lipat, separuh.",
        "",
        "ISI ARTIKEL:",
        body,
    ])

# ── Validation ───────────────────────────────────────────────────────────────

def _indonesian_language_issues(posts):
    """Reject English-dominant slides; source language must not decide output language."""
    english_markers = {
        "a", "an", "and", "are", "as", "at", "been", "but", "by", "for", "from",
        "has", "have", "historically", "in", "is", "more", "of", "on", "or", "said",
        "since", "than", "that", "the", "their", "this", "to", "was", "were", "with",
    }
    indonesian_markers = {
        "akan", "atau", "bagi", "banyak", "bisa", "bukan", "dan", "dari", "dengan",
        "di", "dalam", "ini", "itu", "jadi", "juga", "karena", "kalau", "ke", "lo",
        "lu", "menurut", "pada", "untuk", "yang", "sudah", "tapi", "tidak", "gua", "gw",
    }
    issues = []
    for i in range(1, 7):
        text = posts.get(f"post_{i}", "")
        words = set(re.findall(r"[A-Za-z]+", text.lower()))
        english = len(words & english_markers)
        indonesian = len(words & indonesian_markers)
        if english >= 2 and indonesian == 0:
            issues.append(f"post_{i}: English-dominant output; Bahasa Indonesia required")
    return issues


def deterministic_validate(posts):
    warnings = []
    warnings.extend(_indonesian_language_issues(posts))
    emoji_emote = re.compile(
        r"[\U0001F1E6-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u2B00-\u2BFF\uFE0F\u200D\u20E3]"
        r"|(?<![\w/])(?:[:;=8][-^']?[)(/\\DPp]|[xX][dD]|<3)"
    )
    # Editorial contract: no emoji or ASCII emotes in published slides.
    for i in range(1, 7):
        if emoji_emote.search(posts.get(f"post_{i}", "")):
            warnings.append(f"post_{i}: emoji/emote forbidden")
    # STOP-SLOP patterns — 50+ Indonesian AI template phrases + structural tells
    slop_phrases = [
        # Throat-clearing openers
        "tau gak sih", "gak bakal percaya", "coba resapin", "let that sink in",
        "alasannya?", "yang bikin gue mikir", "yang bikin gw mikir",
        "bayangin", "yang rugi siapa", "patut dicatat",
        # Report-template framing (AI synthetic voice)
        "faktanya", "nyatanya", "inilah yang", "inilah kenapa",
        "yang perlu dicatat", "perlu kalian tahu", "perlu diingat",
        "fakta-fakta menunjukkan", "data menunjukkan", "grafik menunjukkan",
        "aturan bilang", "pemerintah bilang", "jaksa katakan", "menteri bilang",
        "sudah bukan rahasia lagi", "tak terelakkan", "yang menarik",
        # Hedging / vague AI language
        "hal ini menunjukkan", "pada dasarnya", "dalam konteks ini",
        "yang perlu diperhatikan", "sebagaimana diketahui",
        "ada beberapa faktor", "berbagai aspek", "beragam faktor",
        # Wordy transitions
        "untuk itu", "dengan demikian", "oleh karena itu",
        "dalam hal ini", "sehubungan dengan itu",
        # Template fragments
        "coba kalian bayangin", "gimana menurut kalian", "termasuk kalian",
        "itulah mengapa", "jadi intinya",
        # Image references (AI doesn't see)
        "foto ini", "terlihat", "di gambar", "nampak", "tampak",
        "seperti terlihat", "seperti tampak",
        # Other slop
        "tapi ternyata", "sembari",
        "bukan hanya", "namun juga", "baik itu",
    ]
    for i in range(1, 7):
        k = f"post_{i}"
        p = posts.get(k, "")
        if not p.strip():
            warnings.append(f"{k}: empty")
            continue
        # Min length — each slide needs enough source-backed context.
        min_len = 40
        if len(p) < min_len:
            warnings.append(f"{k}: too short ({len(p)} chars, min {min_len})")
        # S1 auto-truncate and auto-split already handled by _normalize_s1().
        # No redundant length check here — avoids double-blocking.
        # 2-4 sentences: dense, source-backed, not rushed.
        sent_count = _sentence_count(p)
        if sent_count < 1:
            warnings.append(f"{k}: no sentences")
        if sent_count > 6:
            warnings.append(f"{k}: too many sentences ({sent_count})")
        # Enforce 480-char limit on every slide; keep complete sentences only.
        limit = SLIDE_CHAR_LIMIT if i == 1 else SLIDE_CHAR_LIMIT
        if len(p) > limit:
            p = _fit_complete_sentences_with_url(p, limit)
            posts[k] = p
        outside = re.sub(r'"[^\"]*"', "", p)
        # Jargon checks moved to _validate_jargon(body-aware) to avoid false positives on source terms.
        if i == 1 and re.match(r"\s*(?:bayangin\b)", outside, re.I):
            warnings.append(f"{k}: 'bayangin' opening")
        if i == 1 and re.match(r"\s*(?:hal ini|yang tak kalah|yang tidak kalah|misal|ujar|ucap|kata|sambung|begitu juga|selanjutnya)\b", outside, re.I):
            warnings.append(f"{k}: weak winning hook")
        if i == 1 and re.match(r"\s*zaman sekarang harga barang naik semua\b", outside, re.I):
            warnings.append(f"{k}: generic/non-source opening")
        # Never fill a slide by describing what the source omits.
        pl = outside.lower()
        for phrase in ("artikel tidak menyebut", "artikel belum menyebut", "tidak disebut dalam artikel", "tidak diketahui", "belum ada dampak", "belum terasa"):
            if phrase in pl:
                warnings.append(f"{k}: absence disclaimer '{phrase}'")
                break
        # Check slop phrases
        for phrase in slop_phrases:
            if phrase in pl:
                warnings.append(f"{k}: slop '{phrase}'")
                break  # one warning per slide
        # Allow rhetorical questions in S2-S5 (provocation style)
        if i == 2 and outside.count("?") > 2:
            warnings.append(f"{k}: too many questions")
        # S6 CTA; S7 is system-generated source URL and bypasses content validation.
        if i == 6:
            last_text = posts.get(f"post_{i}", "").lower()
            if "fakta ini perlu dipantau" in last_text:
                warnings.append(f"{k}: generic winning CTA")
            if re.search(r"\b(?:yang penting|kita harus|pemerintah harus membuktikan|ini menjadi pelajaran|pekerjaan rumah kita)\b", last_text):
                warnings.append(f"{k}: generic editorial close")
            if last_text.count("?") > 2:
                warnings.append(f"{k}: too many CTA questions")
            if re.search(r'https?://\S+|\bSumber\s*:', posts.get(f"post_{i}", ""), re.I):
                warnings.append(f"{k}: S6 must not contain source URL")
            # Detect biased [A]/[B] framing: "solusi cerdas"/"cuan" vs "akal-akalan"
            s6_text = posts.get(f"post_{i}", "").lower()
            if ('[a]' in s6_text and '[b]' in s6_text):
                biased_frames = ['solusi cerdas', 'akal-akalan', 'solusi pintar', 'bantuan tulus']
                for frame in biased_frames:
                    if frame in s6_text:
                        warnings.append(f"{k}: biased framing '{frame}' — kedua kubu harus netral")
                        break
    return warnings


def _validate_s1_hook(posts, body, article=None):
    """Hook quality editorial guidance + forensik winner/loser gates.
    B: reject S1 ending with ?  (0% winner vs 24% loser pattern)
    C: warn on generic/short angle (<80 char) or source-only fallback
    """
    issues = []
    s1 = (posts.get("post_1") or "").strip()
    if not s1:
        return ["post_1: empty"]

    # Gate B: S1 must not end with ? — REMOVED. Winning formula uses challenge
    # question hooks ("Kamu masih nunggu...?"). Hard-rejecting '?' killed the
    # best hook pattern. Question-mark S1 is allowed.

    # Gate D: headline named entity must appear in body (grounding check)
    # Extract name-like patterns from title (2-4 word capitalized sequences)
    # and verify at least one appears in body. Prevents hallucinated attribution.
    if article and article.get("body"):
        title = (article.get("title") or "").strip()
        body_text = article["body"]
        # Extract potential named entities: 2-4 consecutive capitalized words
        name_pattern = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b')
        title_names = set(name_pattern.findall(title))
        # Filter common false positives (generic words that happen to be capitalized)
        false_positives = {
            "Jakarta", "Indonesia", "Jakarta Pusat", "Hari Ini", "Tahun Ini",
            "Kementerian", "Pemerintah", "Negara", "Dalam Negeri", "Untuk Anda",
            "Jakarta Utara", "Jakarta Selatan", "Aceh", "Sumatera", "Jawa",
        }
        title_names -= false_positives
        if title_names:
            body_lower = body_text.lower()
            # Match per-word, not the whole 2-4 word phrase. "Pemerintah Usulkan
            # Perubahan Subsidi" fails literal phrase match because body uses the
            # inflected form "mengusulkan". Per-word substring check tolerates
            # verb affixation while still catching hallucinated entities.
            found = False
            for name in title_names:
                words = [w for w in name.split()
                         if len(w) > 3 and w.lower() not in {
                             "bakal", "akan", "sudah", "masih", "bisa", "mau",
                             "tidak", "juga", "dengan", "untuk", "dari", "dalam",
                         }]
                if not words:
                    continue
                if all(w.lower() in body_lower for w in words):
                    found = True
                    break
            if not found:
                issues.append(
                    f"post_1: headline named entity '{list(title_names)[0]}' "
                    "not found in article body — verify attribution before quoting"
                )

    return issues


def _hook_signal_issues(posts):
    """Fallback-path hook quality gate: S1 must carry a concrete hook signal.

    Raw article leads (\"REPUBLIKA.CO.ID, JAKARTA -- Bank Indonesia memproyeksikan
    kondisi ekonomi global masih bergerak melemah...\") are journalism, not hooks —
    they don't stop scroll. Require at least one: concrete number, change verb,
    named contrast, or challenge question. Applies ONLY to source-only fallback
    (writer/revision path keeps advisory behavior).
    """
    s1 = (posts.get("post_1") or "").strip()
    if not s1:
        return ["post_1: empty"]
    signal = re.compile(
        r"(?:rp\s*)?\d|persen|%|miliar|juta|triliun|"
        r"\b(memutuskan|menaikkan|menurunkan|menghentikan|melarang|"
        r"menolak|mengumumkan|menetapkan|membatalkan|mengubah|turun|naik|"
        r"melonjak|anjlok)\b|\?",
        re.I,
    )
    if not signal.search(s1):
        return ["post_1: hook has no concrete signal (number/change/contrast/question) — raw lead won't stop scroll"]
    return []


def _validate_s6_cta(posts, body):
    """CTA is optional; grounding validates any CTA that the writer chooses."""
    text = (posts.get("post_6") or "").lower()
    if not text.strip():
        return ["post_6: empty"]
    return []


def _duplicate_fact_warnings(posts):
    """Flag material numbers repeated across 3+ slides so six slides use distinct article evidence."""
    warnings = []
    per_number = {}
    for i in range(1, 7):
        key = f"post_{i}"
        numbers = set(re.findall(r"\b\d{2,}(?:[.,]\d+)?\b", posts.get(key, "")))
        for number in numbers:
            per_number.setdefault(number, []).append(key)
    for number, keys in per_number.items():
        if len(keys) >= 3:
            warnings.append(f"{keys[-1]}: repeats material numbers from {keys[0]}")
    return warnings


def _validate_numbers(posts, body):
    """Light grounding: check significant numbers in thread appear in article body.
    Catches hallucinated RpXX.XXX, XX%, XX triliun/miliar/juta not in source.
    Normalizes Rp spacing to avoid false positives (Rp18.073 vs Rp 18.073)."""
    import re
    issues = []
    if not body:
        return issues
    body_normal = body.lower()
    # Normalize number formats: Rp spacing, persen↔%, thousand separators
    body_normal = re.sub(r'(\brp)\s+(\d)', r'\1\2', body_normal)
    body_normal = body_normal.replace('%', ' persen ').replace('  ', ' ')
    # Normalize thousand separators: remove dots in numbers (1.000→1000) but keep decimal commas
    body_normal = re.sub(r'(\d)\.(\d{3})', r'\1\2', body_normal)
    for key in ["post_1","post_2","post_3","post_4","post_5","post_6"]:
        text = posts.get(key, "")
        if not text:
            continue
        # Unit is mandatory: catches Rp100 juta and 10%, but ignores ordinary
        # sentence numbers such as slide labels or dates without a unit.
        patterns = re.finditer(
            r'(?:Rp\s*\d+(?:[.,]\d+)?(?:\s*(?:triliun|miliar|juta|ribu))?|\d+(?:[.,]\d+)?\s*(?:triliun|miliar|juta|ribu|%|persen))',
            text, re.IGNORECASE
        )
        for m in patterns:
            raw = m.group(0).strip()
            num_only = re.sub(r'[^0-9]', '', raw.split()[0])
            if len(num_only) < 4 and not any(u in raw.lower() for u in ['triliun','miliar','juta']):
                continue
            raw_normal = re.sub(r'(\brp)\s+(\d)', r'\1\2', raw.lower())
            raw_normal = raw_normal.replace('%', ' persen ').replace('  ', ' ').strip()
            # Normalize thousand separator dots in raw number too
            raw_normal = re.sub(r'(\d)\.(\d{3})', r'\1\2', raw_normal)
            if raw_normal not in body_normal:
                # Source may carry decimals while writer rounds to whole unit (1.948,72 T → 1.948 T).
                rounded_ok = False
                unit_match = re.search(r"(triliun|miliar|juta|ribu|persen|%)", raw_normal)
                if unit_match:
                    digits = re.search(r"\d+", raw_normal)
                    if digits:
                        unit = "persen" if unit_match.group(1) == "%" else unit_match.group(1)
                        rounded_ok = bool(re.search(rf"{re.escape(digits.group(0))}(?:[.,]\d+)?\s*{unit}", body_normal))
                if not rounded_ok:
                    issues.append(f"{key}: '{raw}' not in article")
    return issues


def _validate_years(posts, body):
    """Years are factual claims too; reject any year absent from article body."""
    source_years = set(re.findall(r"\b(?:19|20)\d{2}\b", body))
    issues = []
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        for year in set(re.findall(r"\b(?:19|20)\d{2}\b", posts.get(key, ""))):
            if year not in source_years:
                issues.append(f"{key}: year '{year}' not in article")
    return issues


def _validate_proper_nouns(posts, body):
    """Fail closed on invented multi-word names and all-caps institutions.
    Single title-cased words are excluded: Indonesian sentence starts cause too many false positives.
    """
    issues = []
    article_lower = body.lower()
    # Sentence connectors are not names when followed by a capitalized source term.
    skip = {"data", "menurut", "padahal", "kalau", "kalo", "yang", "dan", "tapi", "karena", "risikonya", "sumber", "soalnya", "alasan", "alasannya", "sementara", "sedangkan", "lalu", "setelah", "sebelum", "dengan", "untuk", "dari", "pertama", "bukan", "jadi", "namun", "bahkan",
            # Prepositions & particles that start sentences ("Di Asia", "Ke Jakarta", "Pada 2025")
                "di", "ke", "pada", "pak", "bu", "si", "sang", "para",
            # Time phrases are not invented proper names ("Sampai Juni", "Sejak 2026").
            "sampai", "hingga", "mulai", "sejak", "selama",
            # Common sentence-start words that form title-case fragments in Indonesian
            "listrik", "tarif", "harga", "biaya", "pajak", "utang", "dana", "aset",
            "total", "kenaikan", "penurunan", "pertumbuhan", "pendapatan", "jumlah",
            "siapa", "bagaimana", "kenapa", "kapan", "dimana", "berapa"}
    # Common short names are allowed only when their formal source name is present.
    aliases = {
        "bea cukai": "direktorat jenderal bea dan cukai",
        "kemenkeu": "kementerian keuangan",
        "bi": "bank indonesia",
        "danantara": "badan pengelola investasi daya anagata nusantara",
        "badan pengelola investasi danantara": "badan pengelola investasi daya anagata nusantara",
        "bp danantara": "badan pengelola investasi daya anagata nusantara",
        "ojk": "otoritas jasa keuangan",
        "bei": "bursa efek indonesia",
        "dpr": "dewan perwakilan rakyat",
        "bumn": "badan usaha milik negara",
        "kpu": "komisi pemilihan umum",
        "bawaslu": "badan pengawas pemilihan umum",
        "mk": "mahkamah konstitusi",
        "ma": "mahkamah agung",
        "ky": "komisi yudisial",
        "bpk": "badan pemeriksa keuangan",
        "bappenas": "badan perencanaan pembangunan nasional",
        "bps": "badan pusat statistik",
        "bkpm": "badan koordinasi penanaman modal",
        "kemenperin": "kementerian perindustrian",
        "kemenkop": "kementerian koperasi",
        "kemendag": "kementerian perdagangan",
        "kemenhub": "kementerian perhubungan",
        "kemenaker": "kementerian ketenagakerjaan",
        "kemensos": "kementerian sosial",
        "kemenag": "kementerian agama",
        "kemendikbud": "kementerian pendidikan dan kebudayaan",
        "kemenkes": "kementerian kesehatan",
        "kemenlu": "kementerian luar negeri",
        "kemenhan": "kementerian pertahanan",
        "kominfo": "kementerian komunikasi dan informatika",
        "djp": "direktorat jenderal pajak",
        "djb": "direktorat jenderal bea dan cukai",
        "airlangga": "airlangga hartarto",
        "sri mulyani": "sri mulyani indrawati",
        "erik": "erik tohir",
        "prabowo": "prabowo subianto",
        "jokowi": "joko widodo",
        "puan": "puan maharani",
        "gibran": "gibran rakabuming raka",
    }
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "")
        for name in set(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text)):
            # Normalize title prefixes before lookup. Accept "Menteri Koordinator Airlangga"
            # when substantive name "Airlangga" appears literally in source.
            clean = name.lower()
            known_name = False
            for prefix in ("menteri koordinator ", "menteri ", "menkeu ", "dirjen ", "direktur ", "wakil ", "menko ", "pak ", "bu ", "bos "):
                if clean.startswith(prefix) and clean[len(prefix):] in article_lower:
                    known_name = True
                    break
            source_name = aliases.get(clean, clean)
            words = name.split()
            # Sentence fragments ("Pendapatan Telkomsel", "Jika Telkomsel", "Di Tulang Bawang") are not names.
            if (not known_name and words[0].lower() not in skip and words[0].lower() not in {"pendapatan", "laba", "jika", "saat", "karena", "ketika"}
                    and source_name not in article_lower):
                issues.append(f"{key}: name '{name}' not in article")
        # Title-case phrases beginning with common speech/reporting verbs are not names.
        for prefix in ("kata", "ujar", "tutur", "menurut", "sebut"):
            for name in set(re.findall(rf'\b{prefix.title()}\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)):
                if name.lower() not in article_lower:
                    issues.append(f"{key}: name '{prefix.title()} {name}' not in article")
        # All-caps emphasis is common in generated Indonesian; only validate likely institutions.
        emphasis = {"BUKAN", "PERTAMA", "JADI", "TAPI", "KALAU", "JIKA", "DAN", "YANG", "UNTUK", "BOLEH", "WAJIB", "TIDAK",
                    "URL", "HTTP", "HTTPS", "WWW", "COM", "CO", "ID", "ORG", "NET", "INSTAGRAM", "THREADS"}
        # URLs always contain all-caps segments (CNBC, DETIK, WWW) — never flag them as institutions.
        text_no_urls = re.sub(r"https?://\S+|www\.\S+", " ", text)
        for acronym in set(re.findall(r'\b[A-Z]{2,}\b', text_no_urls)):
            if acronym not in emphasis and acronym.lower() not in article_lower:
                # Allow if expanded form appears in article (APBN → "Anggaran Pendapatan dan Belanja Negara")
                expanded = aliases.get(acronym.lower(), "")
                if not expanded or expanded not in article_lower:
                    # Allow generic common terms that appear in any economy article as stand-alone
                    # institutions — too common to gate on specific phrasing
                    if acronym.upper() in {"UU", "PT", "HP", "PNS", "BBM", "PDN", "BPJS", "KUR", "LTV", "GWM"}:
                        continue
                    issues.append(f"{key}: institution '{acronym}' not in article")
    return issues


def _validate_jargon(posts, body):
    """Report jargon for telemetry; jargon never blocks conversational voice."""
    issues = []
    body_lower = (body or "").lower()
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "")
        if not text:
            continue
        outside = re.sub(r'"[^"]*"', "", text)
                # Hard word banned list removed per user decision 2026-08-11.
                # Content quality is controlled via grounding validator + jargon_map
                # + revision per-field revert. Individual vocabulary ban is overkill.
                # "skema" and "obligasi" are common legitimate economy terms — use jargon_map instead
        # Add "obligasi" to jargon_map with explanation check
        # Unexplained acronyms — only flag if NOT in source body
        jargon_map = {
            "IPO": "penawaran saham perdana|jual saham|listing saham",
            "BUMN": "badan usaha milik negara|perusahaan negara|perusahaan pelat merah",
            "BEI": "bursa efek|bursa saham",
            "PDN": "pasar dalam negeri|pasar domestik",
            "DPR": "dewan perwakilan rakyat",
            "OJK": "otoritas jasa keuangan",
            "BPS": "badan pusat statistik",
            "SDM": "sumber daya manusia|pekerja|tenaga kerja",
        }
        for short, expansion in jargon_map.items():
            if re.search(rf"\b{short}\b", outside) and not re.search(expansion, outside, re.I):
                # Allow if acronym itself appears in source body
                if short.lower() not in body_lower:
                    issues.append(f"{key}: stand-alone '{short}' — harus dijelaskan pas pertama muncul")
        # Non-acronym jargon always needs a plain-language explanation.
        # Source presence does not make jargon readable to a general audience.
        hard_word_map = {
            "konsolidasi": "ngebersihin|rapiin|gabungin|satukan",
            "restrukturisasi": "rombak|ubah struktur|tata ulang",
            "likuiditas": "duit yang siap dipake|cair|gampang dicairin",
            "kapitalisasi": "nilai total|harga perusahaan keseluruhan",
            "fundamental": "kondisi dasar|kinerja dasar|keadaan dasar",
            "sentimen": "suasana pasar|pandangan investor|sikap investor",
            "net buy": "lebih banyak membeli|membeli lebih banyak",
            "on-track": "sesuai rencana|sesuai jalur",
            "valuasi": "nilai perusahaan|harga wajar",
            "stimulus": "bantuan ekonomi|dorongan ekonomi",
            "cash flow": "uang masuk dan keluar|arus uang",
            "market cap": "nilai total perusahaan|nilai perusahaan",
        }
        for word, explanation in hard_word_map.items():
            if re.search(rf"\b{word}\b", outside, re.I) and not re.search(explanation, outside, re.I):
                issues.append(f"{key}: hard word '{word}' tanpa penjelasan")
    return issues


def _validate_sensitive_language(posts, body):
    """Sensitive reporting must preserve source attribution and legal status."""
    issues = []
    source = body.lower()
    verdicts = ("jelas korup", "pasti korup", "terbukti korup", "penjahat", "harus dihukum",
                "layak dihukum", "wajib dihukum", "pantas dihukum")
    for key in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]:
        text = posts.get(key, "").lower()
        if any(phrase in text for phrase in verdicts):
            issues.append(f"{key}: sensitive categorical verdict")
        if "tersangka" in text and "tersangka" not in source:
            issues.append(f"{key}: unsupported legal status 'tersangka'")
    return issues


def _voice_warnings(posts):
    """Flag structural AI/synthetic patterns — NOT in slop_phrases but equally damning.
    These are architectural tells that slop_phrases doesn't catch."""
    warnings = []
    forbidden_voice = (
        (r"\b2027\s+jadi\s+tahun\s+paling\s+mahal\b", "template opening"),
        (r"\bbeban\s+rakyat\b", "unsupported drama"),
        (r"\bnegara\s+makin\s+hancur\b", "unsupported drama"),
        (r"\bsiapa\s+yang\s+sebenarnya\s+bayar\b", "generic moral CTA"),
    )
    structural = [
        # "Yang X bukan Y, tapi Z" — formulaic contrast, cold open
        (r'^yang\s+\S+\s+bukan\s+\S+[,，]\s+tapi\s+', 'rewrite contrast opener'),
        # "Bukan sekadar X, tapi Y" — also formulaic
        (r'bukan\s+sekadar\s+', 'rewrite "bukan sekadar"'),
        # Passive voice with inanimate subject doing human verb
        (r'\b(dapat|harus|dapat|perlu)\s+\S+\s+(menjadi|membuat|menghasilkan|memicu)', 'passive construction'),
        # Vague quantified number leads: "Terdapat X yang..." / "Ada X..."
        (r'^(?:terdapat|terdapatnya|terjadi|ada|terdapat)\s+\d+\s+\S+\s+(?:yang\s+)?', 'rewrite vague opener'),
        # "Dalam pengembangan/pengujian/implementasi" — bureaucratic
        (r'\bdalam\s+(?:tahap|fase|proses|rencana|masa)\s+\S+\b', 'rewrite "dalam tahap/fase"'),
        # Meta-joiner: "Berikut adalah/caranya/penjelasannya"
        (r'^berikut\s+(?:adalah|caranya|penjelasannya|detailnya)\s*[:.]?\s*', 'rewrite meta-joiner'),
        # Consecutive short sentences (3+) that read like bullet points
        (r'(?:[A-Z][^.!?]{1,30}[.!?]){3,}', 'rewrite bullet-sentence chain'),
    ]
    for key in [f"post_{i}" for i in range(1, 7)]:
        text = posts.get(key, "")
        for pat, label in forbidden_voice:
            if re.search(pat, text, re.I):
                warnings.append(f"{key}: {label}")
        for pat, label in structural:
            if re.search(pat, text, re.I):
                warnings.append(f"{key}: {label}")
    # Also check for template framing patterns
    report_patterns = r"(?:^|[.!?]\s*)(?:fakta|aturan bilang|pemerintah bilang|yang perlu dicatat|perlu diketahui|artinya)\s*:"
    for key in [f"post_{i}" for i in range(1, 7)]:
        if re.search(report_patterns, posts.get(key, ""), re.I):
            warnings.append(f"{key}: rewrite synthetic voice/template")
    populated = [posts.get(f"post_{i}", "") for i in range(1, 7)]
    all_text = " ".join(populated)
    if sum(bool(text.strip()) for text in populated) >= 6 and not re.search(r"\b(?:gw|gua|gue|menurut gw|kalau gw lihat|yang bikin gw)\b", all_text, re.I):
        warnings.append("chain: missing personal POV")
    for phrase in ("artinya", "ini berarti", "ini menunjukkan"):
        if sum(text.lower().count(phrase) for text in populated) >= 2:
            warnings.append(f"chain: repeated explanatory transition '{phrase}'")
    return warnings


def _quality_gate(article, data, posts, warnings):
    """Quality gate: checks from doc. Return True = pass, False = block."""
    log.debug("quality_gate entry: status=%s posts=%s warnings=%s", data.get("status"), bool(posts), len(warnings or []))
    if data.get("status") != "success" or not posts:
        log.warning(f"  Quality gate early-fail: status={data.get('status')!r} posts_empty={not bool(posts)}")
        return False
    if posts:
        style_issues = deterministic_validate(posts) + _duplicate_fact_warnings(posts)
        log.debug("quality_gate style_issues=%s", style_issues)
        # Style warnings advisory; structural empty/length/sentence/CTA issues remain hard.
        soft_markers = ("slop '", "too many sentences", "too many questions", "too many CTA questions", "stand-alone", "hard word", "rewrite ", "passive construction", "duplicate", "voice:", "audience lens:")
        hard = [w for w in style_issues if not any(marker in w for marker in soft_markers)]
        if hard:
            log.warning(f"  Quality gate hard style issues: {hard}")
            return False
    # 1. Article eligibility is decided from full body before generation.
    # 2. Impact to Indonesia clear (local source assumed)
    # 3. Original numbers have sources (can't verify programmatically)
    # 4. No keyword counted repeatedly (scoring handles)
    # 5. Viral driver: S1 hook needs concrete article-backed change or tension.
    # Viral markers check removed — dead code, never triggered in logs.
    # S1 quality driven by grounding + _normalize_s1, not keyword matching.
    # 6. CTA on post_6 is optional when source has no concrete choice.
    last_text = posts.get("post_6", "").lower()
    if last_text.count("?") > 2:
        log.warning(f"  Quality gate post_6 too many questions: {last_text.count('?')} in {last_text[:80]!r}")
        warnings.append("Post 6: too many CTA questions")
        return False
    return True

# ── Thread Generation ────────────────────────────────────────────────────────

def _normalize_s1(posts, article_body):
    """Trim text to complete sentences within SLIDE_CHAR_LIMIT."""
    s1 = posts.get("post_1", "")
    if len(s1) > SLIDE_CHAR_LIMIT:
        posts["post_1"] = _fit_complete_sentences(s1, SLIDE_CHAR_LIMIT)
    # Auto-split 1-sentence S1 using article facts
    sent_count = _sentence_count(posts.get("post_1", ""))
    if sent_count < 2:
        s1_text = posts["post_1"]
        body_facts = literal_fact_allowlist(article_body)
        for fact in body_facts:
            candidate = f"{s1_text} {fact}"
            if len(fact) > 20 and fact[:40] not in s1_text[:100] and len(candidate) <= SLIDE_CHAR_LIMIT:
                posts["post_1"] = candidate
                break
        sent_count = _sentence_count(posts["post_1"])
        if sent_count < 2:
            log.warning("  S1: still 1 sentence after auto-split — caller skips article")
    return posts


def _collect_hard_warnings(posts, article, require_all_six=False, include_engagement=True):
    """Kumpulkan hard validation warnings untuk satu set posts (satu sumber duplikasi)."""
    keys = ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"] if require_all_six else ["post_1", "post_2", "post_3", "post_4"]
    missing = [f"{k}: empty" for k in keys if not posts.get(k, "").strip()]
    style_warnings = deterministic_validate(posts)
    duplicate_warnings = _duplicate_fact_warnings(posts)
    noun_warnings = _validate_proper_nouns(posts, article["body"])
    voice_warnings = _voice_warnings(posts)
    jargon_warnings = _validate_jargon(posts, article["body"])
    grounding_warnings = grounding_validate(article, posts)
    hard_style_warnings = [w for w in style_warnings + voice_warnings if any(x in w for x in ("empty", "too short", "no sentences", "minimum 2 sentences", "only 0 sentence", "English-dominant", "S1 WAJIB", "weak winning hook", "generic winning CTA", "generic editorial close", "S6 must not", "does not follow policy winning arc", "missing winning arc evidence", "template opening", "unsupported drama", "generic moral CTA", "emoji/emote forbidden"))]
    engagement_warnings = (_validate_s1_hook(posts, article["body"], article) + _validate_s6_cta(posts, article["body"])) if include_engagement else []
    hard = missing + grounding_warnings + noun_warnings + duplicate_warnings + hard_style_warnings + engagement_warnings
    soft = style_warnings + voice_warnings + jargon_warnings
    return hard, soft


def _try_revision(user, posts, article, warnings):
    """Bounded revision (max 2) with per-field revert + source-only fallback.
    Returns (data, posts) if fixed, (None, None) if blocked."""
    # Do not feed validator marker vocabulary back to model; models mirror it.
    revision_notes = re.sub(r"'[^']*'", "'unsupported wording'", '; '.join(warnings))
    rev_user = user + "\n\n" + build_revision_prompt(revision_notes, posts, article)
    c2, e2 = _call_llm(SYSTEM_PROMPT, rev_user, max_retries=1, temperature=0.2)
    if not c2:
        log.warning("  Revision blocked: LLM error")
        return None, None
    d2 = _parse_llm_json(c2)
    if d2 is None:
        log.warning("  Revision blocked: bad JSON")
        return None, None
    p2 = {k: _convert_pov(d2.get(k) or "") for k in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]}
    p2 = _normalize_s1(p2, article["body"])
    w2, soft2 = _collect_hard_warnings(p2, article)
    if soft2:
        log.info(f"  Soft style warnings after revision: {soft2}")
    if d2.get("status") == "success" and not w2:
        log.info("  Revision fixed validation")
        return d2, p2
    log.warning(f"  Revision blocked: {w2 + soft2}")
    # Bounded 2nd revision with explicit issue list (error-feedback). Only for
    # hard issues that survived revision 1 (not style-only).
    if not w2:
        return None, None
    rev2_notes = re.sub(r"'[^']*'", "'unsupported wording'", '; '.join(w2))
    rev2_user = user + "\n\n" + build_revision_prompt(rev2_notes, p2, article)
    c3, e3 = _call_llm(SYSTEM_PROMPT, rev2_user, max_retries=1, temperature=0.2)
    if not c3:
        log.warning("  Revision 2 blocked: LLM error")
        return None, None
    d3 = _parse_llm_json(c3)
    if d3 is None:
        log.warning("  Revision 2 blocked: bad JSON")
        return None, None
    p3 = {k: _convert_pov(d3.get(k) or "") for k in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]}
    p3 = _normalize_s1(p3, article["body"])
    w3, soft3 = _collect_hard_warnings(p3, article)
    if soft3:
        log.info(f"  Soft style warnings after revision 2: {soft3}")
    if d3.get("status") == "success" and not w3:
        log.info("  Revision 2 fixed validation")
        return d3, p3
    log.warning(f"  Revision 2 blocked: {w3 + soft3}")
    # HARD VALIDATION FAILURE — per-field revert to pre-revision originals.
    # The LLM tends to "solve" one hard issue while introducing a new one
    # (e.g. patching post_2's 'padahal' → creates 'padahal' in post_1).
    # Instead of returning "revision_failed", revert each post_ that has
    # hard validation warnings back to its original value and re-validate.
    # If original still fails → article skipped naturally via hard validation.
    log.info("  Revision introduced new hard issues — per-field revert to originals")
    p_orig = {k: _convert_pov(posts.get(k) or "") for k in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]}
    p_orig = _normalize_s1(p_orig, article["body"])
    w_orig, _ = _collect_hard_warnings(p_orig, article)
    if not w_orig:
        # Original was actually valid — revision just made noise
        log.info("  Per-field revert: original was valid, revision was noise")
        return {"status": "success", "angle": ""}, p_orig
    # Model can repeatedly invent names/quotes while repairing. Use
    # deterministic source-only fallback before rejecting candidate.
    log.warning(f"  Original posts also hard-fail: {w_orig}")
    fallback_posts = _source_fallback_posts(article)
    if fallback_posts:
        fallback_posts = _normalize_s1(fallback_posts, article["body"])
        fallback_issues = deterministic_grounding_validate(article, fallback_posts)
        fallback_issues += _validate_s1_hook(fallback_posts, article["body"], article)
        fallback_issues += _hook_signal_issues(fallback_posts)
        fallback_issues += _validate_s6_cta(fallback_posts, article["body"])
        fallback_issues += thread_contract_issues(fallback_posts, article.get("url", ""), article.get("source"))
        fallback_issues += _source_fallback_dangling_refs(fallback_posts)
        if not fallback_issues:
            log.info("  Source-only fallback passed deterministic validation")
            return {"status": "success", "angle": "source-only fallback"}, fallback_posts
        log.warning(f"  Source-only fallback blocked: {fallback_issues[:3]}")
    return None, None


def generate_thread(article):
    """Generate six source-grounded posts. Returns (data, error)."""
    evidence_error = article_evidence_gate(article)
    if evidence_error:
        return None, evidence_error
    user = build_user_prompt(article)
    # One writer plus one revision. If revision fails with hard issues, skip immediately —
    # second writer call with same prompt will generate same slop. Only retry (attempt 2)
    # when revision fails due to JSON/syntax issues, not hard validation.
    for attempt in range(1, 2):
        content, error = _call_llm(SYSTEM_PROMPT, user, max_retries=1, temperature=0.2)
        if error:
            log.warning(f"  Writer request failed — {error[:80]}")
            if is_rate_limit_error(error):
                return None, error
            continue
        content = content.strip()
        data = _parse_llm_json(content)
        if data is None:
            log.warning(f"  LLM attempt {attempt}/1 — bad JSON: {content[:80]}")
            continue
        if data.get("status") == "error":
            return None, data.get("message", "LLM error")
        posts = {k: _convert_pov(data.get(k) or "") for k in ["post_1", "post_2", "post_3", "post_4", "post_5", "post_6"]}
        posts = _normalize_s1(posts, article["body"])

        # All 6 posts required. Style warnings are advisory; grounding, names,
        # claims, empty/structure remain hard.
        warnings, soft_warnings = _collect_hard_warnings(posts, article, require_all_six=True)
        if soft_warnings:
            log.info(f"  Soft style warnings (advisory): {soft_warnings}")
        if warnings:
            log.warning(f"  Hard validation: {warnings}")
            # Bounded revision (max 2) with per-field revert + source-only fallback.
            fixed_data, fixed_posts = _try_revision(user, posts, article, warnings)
            if fixed_posts is not None:
                data, posts = fixed_data, fixed_posts
                warnings = []
                log.info("  Revision/fallback fixed validation")
            else:
                log.warning("  Revision blocked — article skipped")
                continue

        # Quality gate: check article supports the thread
        if not _quality_gate(article, data, posts, warnings):
            log.warning("Quality gate blocked — skip generation")
            return None, "quality_gate"
        for k in posts:
            posts[k] = _format_sentence_blanks(posts[k])
        slide_audit = source_slide_audit(article.get("body", ""), posts)
        log.info("  Source-slide audit: %s", {k: v["source_sentences"] for k, v in slide_audit.items()})
        evidence_issues = _validate_source_evidence_map(posts, article.get("body", ""))
        if evidence_issues:
            log.warning("  Source evidence map blocked: %s", evidence_issues)
            return None, "source_evidence_map_failed"
        contract_issues = thread_contract_issues(posts, article.get("url", ""), article.get("source"))
        if contract_issues:
            log.warning(f"  Thread contract blocked: {contract_issues}")
            continue
        return {
            "article_title": article.get("title", ""),
            "article_url": article.get("url", ""),
            "article_source": article.get("source", ""),
            "angle": data.get("angle", ""),
            "story_functions": STORY_FUNCTIONS,
            "arc": data.get("arc") or _content_metadata(article.get("title", ""), article.get("body", ""))[1],
            "hook_pattern": _content_metadata(article.get("title", ""), article.get("body", ""))[2],
            "posts": posts,
        }, None
    return None, "generation_failed"

# ══════════════════════════════════════════════
#   THREADS PUBLISHER
# ══════════════════════════════════════════════

def post_to_threads(article_title, posts, image_url=None, inflight=None):
    """Post a seven-post chain to Threads; S1 uses article image, S7 carries source URL."""
    if not THREADS_TOKEN or not THREADS_USER_ID:
        log.error("No THREADS_ACCESS_TOKEN or THREADS_USER_ID")
        return None
    if DRY_RUN:
        log.info("DRY RUN — skipping post")
        return None
    uid = THREADS_USER_ID
    published_ids = list((inflight or {}).get("post_ids", []))
    last_post_id = published_ids[-1] if published_ids else None
    image_used = False
    slide_keys = sorted([k for k in posts if k.startswith("post_")], key=lambda x: int(x.split("_")[1]))
    for key in slide_keys[len(published_ids):]:
        text = posts.get(key, "")
        if not text:
            continue
        i = int(key.split("_")[1])
        is_first = (i == 1)
        use_image = is_first and image_url and not image_used
        data = {"user_id": uid, "media_type": "IMAGE" if use_image else "TEXT",
                "text": text, "access_token": THREADS_TOKEN}
        if use_image:
            data["image_url"] = image_url
        if last_post_id:
            data["reply_to_id"] = last_post_id
        container_id = None
        if inflight is not None:
            if inflight.get("attempting_key"):
                return {"error": "PUBLISH_AMBIGUOUS: journal contains unfinished request", "post_ids": published_ids}
            _mark_inflight(inflight, attempting_key=key, attempting_phase="create",
                           attempting_started_at=datetime.now(WIB).isoformat())
        try:
            r = httpx.post(f"{GRAPH}/{uid}/threads", data=data, timeout=15)
            if r.status_code == 200:
                container_id = r.json().get("id")
            else:
                log.warning(f"  {key} create: HTTP {r.status_code}")
        except (httpx.RequestError, json.JSONDecodeError) as e:
            log.error(f"  {key} create ambiguous: {e}")
            return {"error": "PUBLISH_AMBIGUOUS: create request outcome unknown", "post_ids": published_ids}
        if not container_id:
            # Root image is publish contract. Never silently downgrade IMAGE to TEXT.
            log.error(f"  {key} create failed; image fallback disabled")
            return {"error": f"{key} create failed", "post_ids": published_ids}
        if use_image:
            image_ready = False
            for poll in range(15):
                try:
                    sr = httpx.get(f"{GRAPH}/{container_id}",
                                   params={"fields": "status,error_message",
                                           "access_token": THREADS_TOKEN}, timeout=10)
                    if sr.status_code == 200:
                        status = sr.json().get("status", "")
                        if status == "FINISHED":
                            image_ready = True
                            break
                        if status == "ERROR":
                            log.error(f"  {key} image error: {sr.json().get('error_message', '')}")
                            return {"error": f"{key} image processing failed", "post_ids": published_ids}
                except (httpx.RequestError, ValueError, TypeError):
                    pass
                time.sleep(2)
            if not image_ready:
                log.error(f"  {key} image processing timeout")
                return {"error": f"{key} image processing timeout", "post_ids": published_ids}
            image_used = True
        if inflight is not None:
            _mark_inflight(inflight, attempting_phase="publish", creation_id=container_id)
        time.sleep(1)
        post_id = None
        try:
            r = httpx.post(f"{GRAPH}/{uid}/threads_publish",
                           data={"access_token": THREADS_TOKEN, "creation_id": container_id}, timeout=15)
            if r.status_code == 200:
                post_id = r.json().get("id")
            else:
                log.warning(f"  {key} publish: HTTP {r.status_code}")
        except (httpx.RequestError, json.JSONDecodeError) as e:
            log.error(f"  {key} publish ambiguous: {e}")
            # threads_publish is idempotent: re-issuing on an already-PUBLISHED
            # container returns the same post id (no duplicate). Retry once so a
            # timed-out publish does not strand the rest of the chain.
            try:
                time.sleep(1)
                r = httpx.post(f"{GRAPH}/{uid}/threads_publish",
                               data={"access_token": THREADS_TOKEN, "creation_id": container_id}, timeout=15)
                if r.status_code == 200:
                    post_id = r.json().get("id")
                    if post_id:
                        log.info(f"  {key} publish resolved via idempotent retry → {post_id}")
                    else:
                        log.warning(f"  {key} publish retry: empty body")
                else:
                    log.warning(f"  {key} publish retry: HTTP {r.status_code}")
            except (httpx.RequestError, json.JSONDecodeError) as e2:
                log.error(f"  {key} publish retry ambiguous: {e2}")
            if not post_id:
                return {"error": "PUBLISH_AMBIGUOUS: publish request outcome unknown", "post_ids": published_ids}
        if not post_id:
            log.error(f"  {key} publish failed")
            return {"error": f"{key} publish failed", "post_ids": published_ids}
        published_ids.append(post_id)
        last_post_id = post_id
        if inflight is not None:
            inflight["post_ids"] = published_ids
            for field in ("attempting_key", "attempting_phase", "attempting_started_at", "creation_id"):
                inflight.pop(field, None)
            save_inflight(inflight)
        log.info(f"  {key} {'IMAGE' if use_image else 'TEXT'} → {post_id}")
        time.sleep(2)
    root = _verify_published_root(published_ids[0] if published_ids else None)
    if not root:
        return {"error": "root verification failed", "post_ids": published_ids,
                "media_ids": published_ids}
    return {"post_ids": published_ids, "media_ids": published_ids,
            "root_verified": root}

# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════


def _source_fallback_dangling_refs(posts):
    """Editorial gate for source-only fallback.

    Each fallback post is a standalone Threads post, so an opening
    demonstrative ('ini'/'tersebut'/'demikian'/'begini'/'begitu') with no
    antecedent is a dangling reference. Reject it so the caller falls back to
    the next-candidate skip instead of publishing the fragment.
    """
    issues = []
    opener = re.compile(r"^\W*[\w\s'\"“”.()]{1,60}\s+(ini|tersebut|demikian|begini|begitu)\b", re.I)
    for k in (f"post_{i}" for i in range(1, 7)):
        text = (posts.get(k) or "").strip()
        if not text:
            continue
        first = text.split(". ")[0].split(".")[0]
        # Concessive source phrasing ("kendati begitu", "meski demikian")
        # has an explicit antecedent; only reject bare demonstratives.
        if re.match(r"^(?:kendati|meski|meskipun|walau|walaupun|namun|tetapi|tapi)\s+(?:ini|tersebut|demikian|begini|begitu)\b", first, re.I):
            continue
        if opener.match(first):
            issues.append(f"{k}: dangling demonstrative opener")
    return issues

def _remaining_eligible_candidates(candidates, failed_url, data=None):
    """Reuse body-verified candidates after generation failure; do not re-pick skipped URLs."""
    failed = _canonical_url(failed_url)
    posted = posted_canonical_urls(data) if data is not None else set()
    return [candidate for candidate in candidates
            if _canonical_url(candidate.get("url", "")) != failed
            and _canonical_url(candidate.get("url", "")) not in posted]


def _record_published(data, article, result, posts, pub, started_at):
    topic = {
        "title": article["title"],
        "article_url": _canonical_url(article["url"]),
        "canonical_url": _canonical_url(article["url"]),
        "article_source": article["source"],
        "lane": article.get("lane") or _story_lane(article["title"], article.get("body", "")),
        "impact_channel": article.get("impact_channel") or _international_impact_channel(article["title"], article.get("body", "")),
        "angle": result.get("angle", ""),
        "post_id": pub["post_ids"][0],
        "media_id": pub["media_ids"][0] if pub.get("media_ids") else None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
        "eco_score": article.get("eco_score"),
        "selection_weight": article.get("_weight"),
        "pattern": article.get("pattern") or _content_metadata(article["title"], article.get("body", ""))[0],
        "arc": result.get("arc") or article.get("arc") or _content_metadata(article["title"], article.get("body", ""))[1],
        "hook_pattern": article.get("hook_pattern") or _content_metadata(article["title"], article.get("body", ""))[2],
        "editorial_lens": article.get("editorial_lens") or _editorial_lens(article["title"], article.get("body", "")),
        "slides": posts,
        "likes": None, "replies": None, "reposts": None, "views": None, "quotes": None,
        "cohort": CURRENT_COHORT,
    }
    data.setdefault("topics", []).insert(0, topic)
    rc = data.setdefault("recent_content", {})
    rc.setdefault("openings", []).insert(0, posts.get("post_1", "")[:100])
    rc.setdefault("ctas", []).insert(0, posts.get("post_6", "")[:100])
    for key in ["openings", "ctas"]:
        rc[key] = rc[key][:10]
    save_data(data)
    INFLIGHT_FILE.unlink(missing_ok=True)
    log.info(f"Posted: {pub['post_ids'][0]}")
    send_success_report(article["title"], article.get("pattern", "UNKNOWN"),
                        time.monotonic() - started_at, threads_permalink(pub["post_ids"][0]))


def _publish_new_locked(data, article, result, posts, image_url, started_at):
    duplicate = duplicate_ledger_match(data, article["url"])
    if duplicate:
        log.warning("SKIP_duplicate: %s", duplicate)
        print("SKIP_duplicate", flush=True)
        return
    inflight = {
        "article": article, "posts": posts, "post_ids": [], "image_url": image_url,
        "publish_state": "READY",
        "topic": {
            "title": article["title"], "article_url": _canonical_url(article["url"]),
            "canonical_url": _canonical_url(article["url"]), "article_source": article["source"],
            "lane": article.get("lane") or _story_lane(article["title"], article.get("body", "")),
            "impact_channel": article.get("impact_channel") or _international_impact_channel(article["title"], article.get("body", "")),
            "angle": result.get("angle", ""), "story_functions": STORY_FUNCTIONS,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
            "eco_score": article.get("eco_score"), "selection_weight": article.get("_weight"),
            "pattern": article.get("pattern"), "arc": result.get("arc", ""),
            "hook_pattern": article.get("hook_pattern"),
            "editorial_lens": article.get("editorial_lens") or _editorial_lens(article["title"], article.get("body", "")),
            "slides": posts, "likes": None, "replies": None, "reposts": None,
            "views": None, "quotes": None, "cohort": CURRENT_COHORT,
        },
    }
    save_inflight(inflight)
    pub = post_to_threads(article["title"], posts, image_url=image_url, inflight=inflight)
    if _publish_complete(pub, posts):
        _record_published(data, article, result, posts, pub, started_at)
    elif pub and pub.get("error"):
        log.error(f"Post error: {pub['error']}")


def main():
    started_at = time.monotonic()
    try:
        data = load_data()
        inflight = load_inflight()
    except LedgerStateError as exc:
        log.error("CONTRACT_FAILURE: %s", exc)
        print("CONTRACT_FAILURE", flush=True)
        return
    normalize_topic_cohorts(data)
    # Feedback loop: backfill engagement metrics so selection learns from past
    # performance. Bounded fetch; failures are non-fatal (keep publishing).
    try:
        updated, fetched_total, failed = sync_ledger_metrics(data, max_fetch=40)
        if updated or fetched_total:
            log.info("Metrics sync: updated=%d fetched=%d failed=%d", updated, fetched_total, failed)
        perf_stats = performance_medians(data)
        if perf_stats["pattern_avg"] or perf_stats["lane_avg"]:
            log.info("Perf medians: %s", json.dumps(perf_stats, ensure_ascii=False))
    except Exception as exc:
        log.warning("Metrics sync skipped: %s", exc)
    if inflight:
        with post_url_lock():
            try:
                data = load_data()
                inflight = load_inflight()
            except LedgerStateError as exc:
                log.error("CONTRACT_FAILURE: %s", exc)
                print("CONTRACT_FAILURE", flush=True)
                return
            article = inflight["article"]
            duplicate = duplicate_ledger_match(data, article.get("url", ""))
            if duplicate:
                log.error("DUPLICATE_HISTORY_UNCERTAIN: %s", duplicate)
                print("DUPLICATE_HISTORY_UNCERTAIN", flush=True)
                return
            posts = inflight["posts"]
            veto = _final_publish_veto(article, {
                "angle": inflight.get("topic", {}).get("angle", ""),
                "arc": inflight.get("topic", {}).get("arc", ""),
            })
            if veto:
                log.error(f"Final publish veto: {veto}")
                return
            log.warning(f"Resuming partial chain from S{len(inflight.get('post_ids', [])) + 1}")
            if inflight.get("attempting_key"):
                log.error("PUBLISH_AMBIGUOUS: manual remote verification required")
                print("PUBLISH_AMBIGUOUS", flush=True)
                return
            pub = post_to_threads(article["title"], posts, inflight.get("image_url"), inflight)
            if _publish_complete(pub, posts):
                _record_published(data, article, inflight["topic"], posts, pub, started_at)
            elif pub and pub.get("error"):
                log.error(f"Post error: {pub['error']}")
        return
    posted_urls = posted_canonical_urls(data)

    # Step 1: scrape, generate, validate, publish/render.
    article = body = og_image = None
    articles = []
    log.info("Scraping economy sources...")
    articles = scrape_all()
    log.info(f"  Got {len(articles)} raw articles")
    # Pressbox shape: one broad body-first ranking; full editorial gate once.
    hot_topics = scout_hot_topics(
        articles, data=data, limit=DISCOVERY_POOL_LIMIT, per_source_limit=6,
    )
    for topic in hot_topics:
        log.info(f"  Hot #{topic['rank']}: [{topic['lane']}] {topic['title'][:70]} (score={topic['hot_score']})")
    ranked_topics = hot_topics
    articles = _ranked_candidate_pool(articles, ranked_topics, limit=DISCOVERY_POOL_LIMIT)
    ranked_urls = [article["url"] for article in articles]
    log.info(f"  Ranked discovery pool: {len(articles)}/{DISCOVERY_POOL_LIMIT}")

    # Step 2: Scan broad discovery pool, then keep bounded safe candidates for generation.
    from collections import Counter
    skipped_urls = set()
    reject_reasons = Counter()
    exact_posted = _count_exact_posted_candidates(ranked_urls, posted_urls)
    if exact_posted:
        reject_reasons["already_posted"] = exact_posted
    log.info(f"  Discovery accounting: {len(ranked_urls)} ranked, {exact_posted} exact canonical URLs already posted")
    discovery_limit = len(ranked_urls) if not article else 0
    eligible_candidates = []
    for _ in range(discovery_limit):
        candidate = _pick_article(articles, posted_urls | skipped_urls, data, ranked_urls)
        if not candidate:
            break
        # Consume candidate once. Without this, eligible candidate repeats until pool fills.
        skipped_urls.add(candidate["url"])
        # Anti-spam: skip near-identical titles posted within 24h before body fetch.
        title_dup = duplicate_title_match(data, candidate.get("title", ""))
        if title_dup:
            reject_reasons["title_duplicate_24h"] += 1
            log.info(f"  Skip: title dup 24h -> {title_dup[:70]}")
            continue
        log.info(f"Picked: {candidate['title']}")
        log.info(f"  Source: {candidate['source']} | Score: {candidate.get('eco_score', 0)} | Reason: {candidate.get('_reason', '')} | Weight: {candidate.get('_weight', 0)}")
        log.info("Fetching article body...")
        candidate_body, candidate_image, article_ts = _fetch_article_body(candidate["url"])
        source_ts, timestamp_source, timestamp_reason = _resolve_published_timestamp(article_ts, candidate.get("ts", 0), time.time())
        if not source_ts:
            reject_reasons[f"timestamp_{timestamp_reason}"] += 1
            log.info(f"  Skip: timestamp {timestamp_reason} (article metadata; RSS fallback unavailable)")
            skipped_urls.add(candidate["url"])
            continue
        if timestamp_source == "rss_fallback":
            log.info("  Timestamp: rss_fallback (fresh RSS only)")
        topic_score, economy_score, impact_score = _topic_score(candidate["title"], candidate_body)
        pattern_name, pattern_confidence = _classify_pattern(candidate["title"], candidate_body)
        eligible_ok, eligible_reason = _is_eligible_candidate(candidate["title"], candidate_body, candidate["source"])
        if eligible_ok:
            if candidate_image is None:
                reject_reasons["image_invalid"] += 1
                log.warning("  Skip: no valid HD image — trying next candidate")
                skipped_urls.add(candidate["url"])
                continue
            candidate["body"] = candidate_body
            candidate["published_ts"] = source_ts
            candidate["_image"] = candidate_image
            candidate["image_hint"] = _image_hint(candidate_image)
            candidate["pattern"] = pattern_name
            candidate["pattern_label"] = _pattern_label(pattern_name)
            candidate["pattern"] = candidate.get("pattern") or _content_metadata(candidate["title"], candidate_body)[0]
            candidate["arc"] = _content_metadata(candidate["title"], candidate_body)[1]
            candidate["hook_pattern"] = _content_metadata(candidate["title"], candidate_body)[2]
            candidate["lane"] = _story_lane(candidate["title"], candidate_body)
            candidate["impact_channel"] = _international_impact_channel(candidate["title"], candidate_body)
            eligible_candidates.append(candidate)
            log.info(f"  Eligible candidate {len(eligible_candidates)}/{CANDIDATE_POOL_LIMIT}: {candidate['title'][:70]}")
            if len(eligible_candidates) == CANDIDATE_POOL_LIMIT:
                break
            continue
        reject_reasons[eligible_reason] += 1
        skipped_urls.add(candidate["url"])
        log.warning(f"  Skip: {eligible_reason} ({topic_score}/10, economy={economy_score}, impact={impact_score})")
    if eligible_candidates:
        article = eligible_candidates[0]
        body = article["body"]
        og_image = eligible_candidates[0].get("_image")
        log.info(f"  Eligible pool: {len(eligible_candidates)}/{CANDIDATE_POOL_LIMIT}; selected rank {article.get('candidate_rank', '?')}")
        log.info(f"  Lane: {article['lane']} | Impact channel: {article['impact_channel'] or 'n/a'}")
        log.info(f"  Body: {len(body)} chars | Pattern: {article['pattern']} ({article['pattern_label']}, confidence={_classify_pattern(article['title'], body)[1]:.2f})")
    if not article:
        summary = ", ".join(f"{reason}={count}" for reason, count in reject_reasons.most_common()) or "none"
        log.error(f"Candidate rejection summary: {summary}")
        log.error(f"No eligible article among {discovery_limit} ranked discovery candidates")
        print("NO_SAFE_CANDIDATE", flush=True)
        return
    candidate_limit = len(eligible_candidates)

    # Step 4: Resolve image for slide 1
    image_url = None
    if IMAGE_URL:
        image_url = _publishable_image(article, IMAGE_URL)
        log.info("  Image: manual --image-url")
    else:
        image_url = _publishable_image(article, og_image)
        log.info(f"  Image: {image_url[:80] if image_url else 'missing'}")
    if image_url:
        log.info(f"  Image URL: {image_url[:80]}...")
    else:
        log.info("  Image: missing or rejected")

    # Step 5: generate with bounded requests.
    result = None
    error = None
    recent_openings = data.get("recent_content", {}).get("openings", [])
    # Failure fingerprint — track systemic writer failures to circuit-break candidate churn
    failure_counts = {}  # {fingerprint: count}
    log.info("Generating thread...")
    if recent_openings:
        article["recent_openings"] = recent_openings[:5]
    result, error = generate_thread(article)
    # Soft writer failure may use source-only fallback; hard gates stay mandatory.
    if error in {"revision_failed", "quality_gate", "revision_json_error", "generation_failed"}:
        fallback_posts = _source_fallback_posts(article)
        if fallback_posts:
            fallback_posts = _normalize_s1(fallback_posts, article["body"])
            fallback_issues = deterministic_grounding_validate(article, fallback_posts)
            fallback_issues += _validate_s1_hook(fallback_posts, article["body"], article)
            fallback_issues += _hook_signal_issues(fallback_posts)
            fallback_issues += _validate_s6_cta(fallback_posts, article["body"])
            fallback_issues += thread_contract_issues(fallback_posts, article.get("url", ""), article.get("source"))
            fallback_issues += _source_fallback_dangling_refs(fallback_posts)
            if not fallback_issues:
                result = {"posts": fallback_posts, "angle": "source-only fallback",
                          "arc": article.get("arc", "")}
                error = None
                log.warning("Using source-only fallback after writer failure")
            else:
                log.warning(f"Source-only fallback blocked: {fallback_issues[:3]}")
    if error:
        # Track failure fingerprint for circuit-breaker
        fprint = f"{error}"
        failure_counts[fprint] = failure_counts.get(fprint, 0) + 1
        log.error(f"Generation failed: {error} (fingerprint count: {failure_counts[fprint]})")
        if is_rate_limit_error(error):
            log.error("Generation stopped: Mistral rate limit; cooling down 90s before retry candidate")
            time.sleep(90)
        else:
            skipped_urls.add(article["url"])

        # Generation may have succeeded via the 429 retry path — go straight to save/post.
        if result and not error:
            article["body"] = body
            article["image_hint"] = _image_hint(og_image)
            if IMAGE_URL:
                image_url = IMAGE_URL
            else:
                image_url = _publishable_image(article, og_image)
            # Restore original article object for downstream use.
            article["pattern"] = article.get("pattern") or _classify_pattern(article["title"], article["body"])[0]
            article["pattern_label"] = _pattern_label(article["pattern"])
            goto_step5 = True
        else:
            goto_step5 = False

        # Retry only candidates already body-fetched and editorially validated above.
        # Re-picking from skipped_urls made every eligible fallback unreachable.
        retry_candidates = _remaining_eligible_candidates(eligible_candidates, article["url"], data)
        for retry_article in retry_candidates[:1]:
            if goto_step5:
                break
            log.info(f"  Retry eligible candidate: {retry_article['title'][:80]}")
            if recent_openings:
                retry_article["recent_openings"] = recent_openings[:5]
            result, error = generate_thread(retry_article)
            if error:
                failure_counts[error] = failure_counts.get(error, 0) + 1
                log.error(f"Retry generation also failed: {error} (fingerprint count: {failure_counts[error]})")
                if is_rate_limit_error(error):
                    log.error("Generation stopped: Mistral rate limit; skip candidate churn")
                    return
                continue
            article = retry_article
            body = article["body"]
            og_image = article.get("_image")
            image_url = IMAGE_URL if IMAGE_URL else _publishable_image(article, og_image)
            break

        if not result:
            log.error("Generation failed: no verified LLM draft after retry")
            print("NO_SAFE_CANDIDATE", flush=True)
            return

    if not result:
        log.error("Generation failed: no valid result")
        print("NO_SAFE_CANDIDATE", flush=True)
        return

    veto = _final_publish_veto(article, result)
    if veto:
        log.error(f"Final publish veto: {veto}")
        print("NO_SAFE_CANDIDATE", flush=True)
        return

    posts = result["posts"]
    if DRY_RUN:
        print("\n=== TECHBRO DRY RUN ===")
        print(f"TITLE: {article.get('title', '')}")
        for i in range(1, 7):
            print(f"S{i}: {posts.get(f'post_{i}', '')}")
        print(f"S7: Sumber: {article.get('url', '')}")
        print("=== END TECHBRO DRY RUN ===")
        print("DRY_RUN_OK", flush=True)
    for i in range(1, 8):
        first_line = posts.get(f"post_{i}", "").split("\n")[0][:80] or "(empty)"
        log.info(f"  S{i}: {first_line}")

    # Step 6: Post. Live root must carry validated article image.
    if not DRY_RUN and not image_url:
        log.error("Live publish blocked: validated root image required")
        print("NO_SAFE_CANDIDATE", flush=True)
        return
    if not DRY_RUN:
        with post_url_lock():
            try:
                data = load_data()
            except LedgerStateError as exc:
                log.error("CONTRACT_FAILURE: %s", exc)
                print("CONTRACT_FAILURE", flush=True)
                return
            _publish_new_locked(data, article, result, posts, image_url, started_at)
    else:
        print()
        for i in range(1, 8):
            if f"post_{i}" not in posts:
                continue
            print(f"--- S{i} ---")
            print(posts.get(f"post_{i}", ""))
            print()
        print(f"Arc: {result.get('arc', 'market_shock')}")
        print(f"Article: {article['title']}")
        print(f"Source: {article['source']}")
        print(f"Angle: {result.get('angle', '')}")

if __name__ == "__main__":
    main()
