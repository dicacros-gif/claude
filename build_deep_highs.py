# -*- coding: utf-8 -*-
"""
build_deep_highs.py
52주 신고가 딥다이브 - TradingView + yfinance + FnGuide + Naver 통합 분석
출력: index.html (GitHub Pages 배포용)
"""

# ───────────────────────────────────────────────
# 섹션 1: 환경설정 & 상수
# ───────────────────────────────────────────────
import os, ssl, requests, concurrent.futures, json, math, re, time, warnings, html as _html
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter

warnings.filterwarnings("ignore")

os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""
os.environ["PYTHONHTTPSVERIFY"] = "0"
ssl._create_default_https_context = ssl._create_unverified_context

try:
    from curl_cffi import requests as _curl_req
    _YF_SESSION = _curl_req.Session(impersonate="chrome", verify=False)
except ImportError:
    _YF_SESSION = None

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

# ── 경로 ──────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

ENRICHED_HIGH_CSV  = DATA_DIR / "52_week_highs_enriched_history.csv"
VOLUME_SURGE_CSV   = DATA_DIR / "volume_surge_history.csv"
FLOW_HISTORY_CSV   = DATA_DIR / "flow_history.csv"
CUSIP_CACHE_JSON   = DATA_DIR / "cusip_cache.json"
OUTPUT_HTML        = ROOT / "index.html"

TIMEZONE = timezone(timedelta(hours=9), name="KST")

# ── NextFY 레이블 ──────────────────────────────
_NOW = datetime.now(TIMEZONE)
_NEXT_FY_YEAR = _NOW.year + 1 if _NOW.month >= 4 else _NOW.year
NEXT_FY_LABEL = f"'{str(_NEXT_FY_YEAR)[2:]}년E"

# ── 필터 기준 ──────────────────────────────────
MIN_PRICE_USD = 10.0
MIN_PRICE_KRW = 10_000
MIN_MKTCAP_USD = 300e6
MIN_MKTCAP_KRW = 300e8

# ── 가중치 (13개 차원, 합=100) ─────────────────
DEEP_WEIGHTS = {
    "밸류에이션": 7,     "성장/컨센서스": 18,  "수익성/재무품질": 9,
    "현금흐름": 8,       "외국인수급": 10,     "기관수급": 5,
    "선행매매": 13,      "미래산업": 9,         "수출/해외확장": 8,
    "장기안정성": 5,     "모멘텀": 4,           "투자의견": 3,
    "거래량": 1,
}
SCORE_FIELD_BY_WEIGHT = {
    "밸류에이션": "밸류점수",       "성장/컨센서스": "성장점수",
    "수익성/재무품질": "품질점수",   "현금흐름": "현금흐름점수",
    "외국인수급": "외국인수급점수",  "기관수급": "기관수급점수",
    "선행매매": "선행매매점수",      "미래산업": "미래산업점수",
    "수출/해외확장": "수출해외점수", "장기안정성": "장기투자점수",
    "모멘텀": "모멘텀점수",          "투자의견": "투자의견점수",
    "거래량": "거래량점수",
}

# ── 수출 섹터 보너스 ───────────────────────────
EXPORT_SECTOR_BONUS = {
    "반도체": 25, "Semiconductors": 25, "배터리": 22, "방산": 22, "Defense": 22,
    "조선": 18, "Shipbuilding": 18, "전기차": 20, "자동차부품": 16,
    "제약/바이오": 14, "Biotechnology": 14, "화학": 12, "기계": 12, "철강": 10,
    "디스플레이": 12, "소프트웨어": 8, "전자": 18, "IT": 10,
}
KOREAN_EXPORTERS = {
    "005930": 25, "000660": 25, "042700": 22,
    "373220": 22, "006400": 22, "247540": 20,
    "047810": 22, "012450": 22, "042660": 20,
    "329180": 20, "267250": 18, "009540": 16,
    "005380": 18, "000270": 18, "207940": 20,
    "005490": 18, "066570": 20, "009150": 20,
    "035420": 10, "035720": 8,  "105560": 8,
    "055550": 8,  "051910": 16, "086520": 18,
    "183300": 12, "115500": 10, "079550": 20,
    "277810": 18, "267260": 20, "196170": 14,
    "128940": 14, "003230": 10,
}

# ── 미래산업 테마 ──────────────────────────────
FUTURE_THEMES = {
    "AI반도체":    ["nvidia", "amd", "broadcom", "하이닉스", "삼성전자", "리노공업", "sk하이닉스"],
    "AI인프라":    ["microsoft", "alphabet", "amazon", "meta", "oracle", "arista", "vertiv"],
    "로봇/자동화": ["intuitive", "fanuc", "두산로보틱스", "레인보우로보틱스", "현대로템"],
    "방산":        ["lockheed", "rtx", "northrop", "한화", "현대로템", "lig넥스원", "한국항공우주"],
    "전기차/배터리": ["tesla", "byd", "lg에너지솔루션", "삼성sdi", "에코프로비엠"],
    "바이오신약":  ["eli lilly", "novo nordisk", "regeneron", "셀트리온", "삼성바이오로직스"],
    "에너지전환":  ["enphase", "firstsolar", "두산퓨얼셀", "hd현대일렉트릭"],
    "우주/위성":   ["planet labs", "iridium", "한화시스템"],
    "수출제조":    ["posco", "현대중공업", "hd현대", "삼성중공업"],
}
_HIGH_GROWTH_THEMES = {"AI반도체", "AI인프라", "로봇/자동화", "방산", "바이오신약"}

# ── 유명기관 13F ────────────────────────────────
FAMOUS_MANAGERS = [
    ("Berkshire Hathaway",              "0001067983"),  # Warren Buffett
    ("Bridgewater Associates",          "0001350694"),  # Ray Dalio
    ("Citadel Advisors",                "0001423053"),  # Ken Griffin
    ("Gates Foundation Trust",          "0001166559"),  # Bill Gates
    ("Tiger Global",                    "0001167483"),  # Chase Coleman
    ("Wellington Management",           "0000902219"),
    ("Renaissance Technologies",        "0001037389"),  # Jim Simons
    ("Pershing Square",                 "0001336528"),  # Bill Ackman
    ("Soros Fund Management",           "0001029160"),  # George Soros
    ("Duquesne Family Office",          "0001536411"),  # Stanley Druckenmiller
    ("Third Point LLC",                 "0001040273"),  # Dan Loeb
    ("D. E. Shaw",                      "0001179821"),  # David Shaw
    ("Baupost Group",                   "0001061768"),  # Seth Klarman
    ("Appaloosa Management",            "0001006438"),  # David Tepper
    ("Viking Global Investors",         "0001341439"),  # Andreas Halvorsen
    ("Coatue Management",               "0001336919"),  # Philippe Laffont
    ("Lone Pine Capital",               "0001061219"),  # Stephen Mandel
    # ── 추가 유명 운용사 (2차) ──────────────────────
    ("Scion Asset Management",          "0001331287"),  # Michael Burry
    ("Paulson & Co",                    "0001035173"),  # John Paulson
    ("Greenlight Capital",              "0001079294"),  # David Einhorn
    ("Elliott Investment Management",   "0001039399"),  # Paul Singer
    ("Starboard Value",                 "0001371174"),  # Jeff Smith
    ("ValueAct Capital",                "0001129816"),  # Mason Morfit
    ("Highbridge Capital",              "0001199392"),
    ("Glenview Capital",                "0001228454"),  # Larry Robbins
    # ── 추가 유명 운용사 (3차) ──────────────────────
    ("Icahn Capital",                   "0000921669"),  # Carl Icahn
    ("Trian Fund Management",           "0001418135"),  # Nelson Peltz
    ("Point72 Asset Management",        "0001603466"),  # Steve Cohen
    ("Two Sigma Investments",           "0001179392"),  # David Siegel
    ("Oaktree Capital Management",      "0001061165"),  # Howard Marks
    ("Harris Associates",               "0000778070"),  # Bill Nygren (Oakmark)
    ("Gotham Asset Management",         "0001336215"),  # Joel Greenblatt
    ("Sachem Head Capital",             "0001594686"),  # Scott Ferguson
    # ── 추가 유명 운용사 (4차) ──────────────────────
    ("AQR Capital Management",          "0001307748"),  # Cliff Asness — 퀀트 선구자
    ("Maverick Capital",                "0001102263"),  # Lee Ainslie — Tiger Cub
    ("TCI Fund Management",             "0001326706"),  # Christopher Hohn — 행동주의 유럽
    ("Dragoneer Investment Group",      "0001558838"),  # Marc Stad — 테크 성장
    ("Durable Capital Partners",        "0001766948"),  # Henry Ellenbogen — T.Rowe 출신
    ("Tiger Eye Capital",               "0001562214"),
    ("Whale Rock Capital",              "0001479222"),  # Alex Sacerdote — 테크 성장
    ("Alkeon Capital",                  "0001509986"),  # Panayotis Sparaggis
    ("Steadfast Capital",               "0001056239"),  # Robert Pitts — Tiger Cub
    ("Akre Capital Management",         "0001112520"),  # Chuck Akre — 복리 장인
    ("Wedgewood Partners",              "0001097898"),  # David Rolfe — 집중 가치
    # ── 추가 유명 운용사 (5차) ──────────────────────
    ("GAMCO Investors",                 "0000790301"),  # Mario Gabelli — 가치 투자 거장
    ("Baron Capital",                   "0000813672"),  # Ron Baron — 성장주 장기투자
    ("Fisher Asset Management",         "0000799235"),  # Ken Fisher — 세계 3대 운용사
    ("Fairholme Capital",               "0001112100"),  # Bruce Berkowitz — 집중 가치
    ("Dodge & Cox",                     "0000029250"),  # 1930년대 창립 가치 운용사
    ("Polen Capital",                   "0001441612"),  # 집중 성장 (CAGR 집착)
    ("Artisan Partners",                "0001379785"),  # 다전략 성장 운용사
    ("Davis Selected Advisers",         "0000275563"),  # Christopher Davis — 가치
    ("Edgewood Management",             "0001000273"),  # 집중 대형 성장
    ("Sequoia Fund",                    "0000088525"),  # Buffett 추천 집중 가치
    ("Ruane Cunniff",                   "0000085613"),  # Sequoia 운용사 (Buffett 파트너)
    ("First Eagle Investment",          "0000036020"),  # Jean-Marie Eveillard 전통
    ("GQG Partners",                    "0001706946"),  # Rajiv Jain — 신흥 성장 구루
    ("Vulcan Value Partners",           "0001457655"),  # C.T. Fitzpatrick — 집중 가치
    ("Giverny Capital",                 "0001569126"),  # Francois Rochon — 캐나다 가치
    ("Fundsmith",                       "0001843588"),  # Terry Smith — 영국 버핏
    ("Lindsell Train",                  "0001565083"),  # Nick Train — 영국 장기 성장
    ("Rowan Street Capital",            "0001730826"),  # 집중 비공개 성장
]

# ── 한국 기업명 사전 ──────────────────────────
_INVALID_KR_NAMES = frozenset({
    "기업정보","회사정보","기업개요","종목정보","주식정보","재무정보",
    "딥다이브","기업분석","컨센서스","투자정보","전략","요약",
    "null","none","undefined","nan",
})
_KNOWN_KR_NAMES = {
    # 반도체
    "005930": "삼성전자",       "000660": "SK하이닉스",      "042700": "한미반도체",
    "009150": "삼성전기",       "023590": "다이나믹디자인",   "336370": "솔브레인홀딩스",
    "357780": "솔브레인",       "102710": "이오테크닉스",     "240210": "어보브반도체",
    "112610": "씨에스윈드",     "095720": "웨이브일렉트로",   "166090": "하나머티리얼즈",
    "108490": "로체시스템즈",   "036090": "에스엔유",         "139480": "이마트",
    # 2차전지/EV
    "373220": "LG에너지솔루션", "006400": "삼성SDI",          "247540": "에코프로비엠",
    "086520": "에코프로",       "096530": "씨젠",             "003670": "포스코퓨처엠",
    "043270": "에코프로에이치엔","018880": "한온시스템",
    # 방산/항공
    "047810": "한국항공우주",   "012450": "한화에어로스페이스","079550": "LIG넥스원",
    "064350": "현대로템",       "015210": "OCI홀딩스",        "272210": "한화시스템",
    # 조선/중공업
    "042660": "한화오션",       "329180": "HD현대중공업",      "267250": "HD한국조선해양",
    "009540": "HD현대",         "010140": "삼성중공업",        "006260": "LS",
    # 자동차
    "005380": "현대자동차",     "000270": "기아",              "012330": "현대모비스",
    "018880": "한온시스템",     "204320": "현대위아",
    # 소재/철강
    "005490": "POSCO홀딩스",    "051910": "LG화학",            "010950": "S-Oil",
    "011170": "롯데케미칼",     "006490": "인산철",
    # 바이오/제약
    "207940": "삼성바이오로직스","068270": "셀트리온",          "196170": "알테오젠",
    "128940": "한미약품",        "326030": "SK바이오팜",        "214150": "클래시스",
    "145020": "휴젤",            "009290": "광동제약",
    # IT/플랫폼
    "035420": "NAVER",           "035720": "카카오",            "259960": "크래프톤",
    "036570": "엔씨소프트",      "251270": "넷마블",            "293490": "카카오게임즈",
    # 금융
    "105560": "KB금융",          "055550": "신한지주",          "086790": "하나금융지주",
    "316140": "우리금융지주",    "138930": "BNK금융지주",       "029780": "삼성카드",
    # 전자/IT부품
    "066570": "LG전자",          "183300": "코미코",            "115500": "케이씨에스",
    "006110": "삼아알미늄",      "267260": "HD현대일렉트릭",
    # 로봇/자동화
    "277810": "레인보우로보틱스","090355": "노루홀딩스",        "335890": "비올",
    # 기타 대형주
    "003230": "삼양식품",        "004170": "신세계",            "028260": "삼성물산",
    "000830": "삼성화재",        "018260": "삼성에스디에스",    "009830": "한화솔루션",
    "373220": "LG에너지솔루션",  "011780": "금호석유",          "006800": "미래에셋증권",
    "030200": "KT",              "017670": "SK텔레콤",          "032640": "LG유플러스",
    "015760": "한국전력",        "036460": "한국가스공사",
}

TV_COLUMNS = [
    "name","description","exchange","close","high","low","change","volume",
    "average_volume_30d_calc","relative_volume_10d_calc",
    "price_52_week_high","price_52_week_low","market_cap_basic","sector","industry",
    "price_earnings_ttm","price_earnings_forward_fy","price_earnings_growth_ttm",
    "price_sales_current","price_book_fq","enterprise_value_ebitda_ttm",
    "earnings_per_share_diluted_ttm","earnings_per_share_diluted_yoy_growth_fq",
    "earnings_per_share_diluted_qoq_growth_fq",
    "total_revenue_yoy_growth_fq","total_revenue_qoq_growth_fq",
    "net_income_yoy_growth_fq","net_income_qoq_growth_fq",
    "gross_margin_ttm","operating_margin_ttm","net_margin_ttm",
    "return_on_equity_fy","return_on_assets_fy","return_on_invested_capital_fy",
    "debt_to_equity_fq","current_ratio_fq","free_cash_flow_ttm","free_cash_flow_margin_ttm",
    "total_revenue_ttm","capital_expenditures_ttm","beta_1_year","beta_3_year",
    "cash_n_short_term_invest_fq","total_debt_fq",
    "earnings_per_share_forecast_fq","earnings_per_share_forecast_next_fy",
    "revenue_forecast_fq","revenue_forecast_next_fq","revenue_forecast_next_fy",
    "price_target_average","price_target_high","price_target_low",
    "recommendation_mark","Recommend.All",
    "RSI","Perf.W","Perf.1M","Perf.3M","Perf.6M","Perf.Y","Perf.YTD",
    "earnings_release_next_date","revenue_surprise_percent_fq",
    "buyback_yield","earnings_per_share_fq","float_shares_outstanding_current",
    "dividends_yield_current","number_of_employees",
]


def _bare_kr_code(ticker: str) -> str:
    return ticker.split(":")[-1].strip() if ":" in ticker else ticker.strip()


def _safe(val, default=None):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    return val


def _pct(val, default=None):
    v = _safe(val, default)
    if v is None:
        return None
    try:
        return float(v) * 100
    except Exception:
        return default


# ───────────────────────────────────────────────
# 섹션 2: TradingView 수집
# ───────────────────────────────────────────────
_TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}


def _tv_scan(market: str, filters: list, sort_by: str = "market_cap_basic",
             range_end: int = 200) -> list[dict]:
    url = f"https://scanner.tradingview.com/{market}/scan"
    payload = {
        "columns": TV_COLUMNS,
        "sort": {"sortBy": sort_by, "sortOrder": "desc"},
        "range": [0, range_end],
        "filter": filters,
    }
    try:
        resp = requests.post(url, json=payload, headers=_TV_HEADERS, timeout=20, verify=False)
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for item in data.get("data", []):
            ticker = item.get("s", "")
            vals = item.get("d", [])
            row = {"_ticker": ticker}
            for i, col in enumerate(TV_COLUMNS):
                row[col] = vals[i] if i < len(vals) else None
            rows.append(row)
        return rows
    except Exception as e:
        print(f"[TV] {market} scan error: {e}")
        return []


def fetch_tradingview_by_tickers(tickers: list[str]) -> list[dict]:
    """특정 티커 리스트로 TradingView 데이터를 조회.

    persistent universe (어제 추적했지만 오늘 신고가 아닌 종목)의 시세·펀더멘털을
    최신화하기 위해 사용. 미국/한국 자동 라우팅.
    """
    if not tickers:
        return []
    us_tk = [t for t in tickers if not (t.startswith("KRX:") or t.startswith("KOSDAQ:"))]
    kr_tk = [t for t in tickers if t.startswith("KRX:") or t.startswith("KOSDAQ:")]
    out: list[dict] = []
    for market, tks in (("america", us_tk), ("korea", kr_tk)):
        if not tks:
            continue
        # 200개 단위로 분할
        for i in range(0, len(tks), 200):
            chunk = tks[i:i+200]
            filters = [{"left": "name", "operation": "in_range",
                        "right": [t.split(":")[-1] for t in chunk]}]
            rows = _tv_scan(market, filters, range_end=len(chunk))
            out.extend(rows)
    return out


def fetch_tradingview_highs(market: str) -> list[dict]:
    """52주 신고가 = 현재가가 52주 최고가와 같은 종목"""
    filters = [
        {"left": "price_52_week_high", "operation": "equal", "right": "close"},
    ]
    if market == "america":
        filters.append({"left": "exchange", "operation": "in_range",
                         "right": ["NASDAQ", "NYSE", "AMEX"]})
    elif market == "korea":
        filters.append({"left": "exchange", "operation": "in_range",
                         "right": ["KRX", "KOSDAQ"]})
    return _tv_scan(market, filters, range_end=200)


def fetch_tradingview_volume_surge(market: str) -> list[dict]:
    """상대거래량(10일) > 2배 거래량 급증 종목"""
    filters = [
        {"left": "relative_volume_10d_calc", "operation": "greater", "right": 2.0},
    ]
    if market == "america":
        filters.append({"left": "exchange", "operation": "in_range",
                         "right": ["NASDAQ", "NYSE", "AMEX"]})
    elif market == "korea":
        filters.append({"left": "exchange", "operation": "in_range",
                         "right": ["KRX", "KOSDAQ"]})
    return _tv_scan(market, filters, sort_by="relative_volume_10d_calc", range_end=100)


# ───────────────────────────────────────────────
# 섹션 3: 외부 데이터 수집
# ───────────────────────────────────────────────

def _fetch_yfinance_one(ticker: str) -> dict:
    if not _HAS_YF:
        return {}
    try:
        tk_obj = yf.Ticker(ticker, session=_YF_SESSION)
        info = tk_obj.info or {}

        # fast_info 보조 (일부 필드는 fast_info가 더 안정적)
        try:
            fi = tk_obj.fast_info
            fi_mktcap   = getattr(fi, "market_cap", None)
            fi_shares   = getattr(fi, "shares", None)
        except Exception:
            fi_mktcap = fi_shares = None

        # 단기매도 데이터 — shortPercentOfFloat 없으면 sharesShortPreviousMonthDate 비율 계산
        short_pct = _pct(info.get("shortPercentOfFloat"))
        if short_pct is None:
            short_pct = _pct(info.get("shortPercent"))  # alias
        short_ratio = _safe(info.get("shortRatio")) or _safe(info.get("daysToConverShort"))

        # 기관·내부자 보유율 — 여러 키 시도
        inst_pct    = (_pct(info.get("heldPercentInstitutions"))
                       or _pct(info.get("institutionsPercentHeld")))
        insider_pct = (_pct(info.get("heldPercentInsiders"))
                       or _pct(info.get("insidersPercentHeld")))

        # 목표주가 — analyst info fallback
        tgt_mean = (_safe(info.get("targetMeanPrice"))
                    or _safe(info.get("targetPrice")))
        tgt_high = _safe(info.get("targetHighPrice"))
        tgt_low  = _safe(info.get("targetLowPrice"))

        # 배당 수익률 보조
        div_yld = (_pct(info.get("dividendYield"))
                   or _pct(info.get("trailingAnnualDividendYield")))

        # 사업 요약 — 다국어 필드 시도
        biz = (info.get("longBusinessSummary") or
               info.get("description") or "")[:500]
        # html 엔티티 정규화
        biz = _html.unescape(biz)

        # 최근 뉴스 (1-2일 내) — yfinance Ticker.news
        recent_news = ""
        try:
            news_items = tk_obj.news or []
            from datetime import datetime as _dt2
            now_ts = _dt2.now().timestamp()
            two_days_ago = now_ts - (2 * 86400)
            fresh = []
            for n in news_items[:10]:
                # yfinance 신구 포맷 모두 지원
                title = (n.get("title")
                         or n.get("content", {}).get("title", ""))
                pub_ts = (n.get("providerPublishTime")
                          or n.get("content", {}).get("pubDate", 0))
                if isinstance(pub_ts, str):
                    try:
                        pub_ts = _dt2.fromisoformat(
                            pub_ts.replace("Z","+00:00")
                        ).timestamp()
                    except Exception:
                        pub_ts = 0
                if title and pub_ts >= two_days_ago:
                    days_ago = max(0, int((now_ts - pub_ts) / 86400))
                    age = "오늘" if days_ago == 0 else f"{days_ago}일전"
                    fresh.append(f"[{age}] {_html.unescape(title)[:100]}")
                if len(fresh) >= 3:
                    break
            recent_news = " | ".join(fresh)
        except Exception:
            pass

        # 애널리스트 평가 변경 (upgrades/downgrades 30일 이내)
        rec_changes = ""
        try:
            up_dn = tk_obj.upgrades_downgrades
            if up_dn is not None and not up_dn.empty:
                from datetime import datetime as _dt3
                cutoff = _dt3.now().timestamp() - (60 * 86400)
                # DataFrame index = GradeDate (Timestamp)
                rows_sorted = up_dn.head(8)  # 최신 8건
                items = []
                for idx, row in rows_sorted.iterrows():
                    try:
                        ts = idx.timestamp() if hasattr(idx, "timestamp") else 0
                    except Exception:
                        ts = 0
                    if ts < cutoff:
                        continue
                    firm = str(row.get("Firm", "") or "")[:18]
                    action = str(row.get("Action", "") or "")
                    to_g = str(row.get("ToGrade", "") or "")
                    from_g = str(row.get("FromGrade", "") or "")
                    date_str = idx.strftime("%m/%d") if hasattr(idx, "strftime") else ""
                    if to_g:
                        if from_g and from_g != to_g:
                            items.append(f"[{date_str}] {firm}: {from_g}→{to_g}")
                        else:
                            items.append(f"[{date_str}] {firm}: {to_g}")
                    if len(items) >= 4:
                        break
                rec_changes = " | ".join(items)
        except Exception:
            pass

        # 최근 이익 발표 서프라이즈 (Earnings history)
        eps_history = ""
        try:
            eh = tk_obj.earnings_history
            if eh is not None and not eh.empty:
                items = []
                for idx, row in eh.tail(4).iterrows():
                    est  = row.get("epsEstimate")
                    act  = row.get("epsActual")
                    sur  = row.get("surprisePercent")
                    if act is not None and est is not None:
                        try:
                            sur_s = f"+{sur*100:.1f}%" if sur > 0 else f"{sur*100:.1f}%"
                        except Exception:
                            sur_s = ""
                        items.append(f"{idx.strftime('%y/%m')} EPS {act:.2f}(예상{est:.2f},{sur_s})")
                eps_history = " | ".join(items[-3:])
        except Exception:
            pass

        # 애널리스트 컨센서스 추천도 (1=Strong Buy ~ 5=Strong Sell)
        rec_mean = _safe(info.get("recommendationMean"))
        rec_key  = info.get("recommendationKey", "")
        rec_num_analysts = _safe(info.get("numberOfAnalystOpinions"))

        return {
            "yf_forwardPE":        _safe(info.get("forwardPE")),
            "yf_pegRatio":         _safe(info.get("pegRatio")),
            "yf_shortRatio":       short_ratio,
            "yf_shortPct":         short_pct,
            "yf_instPct":          inst_pct,
            "yf_insiderPct":       insider_pct,
            "yf_price_target_mean":tgt_mean,
            "yf_price_target_high":tgt_high,
            "yf_price_target_low": tgt_low,
            "yf_dividendYield":    div_yld,
            "yf_mktcap":           fi_mktcap,
            "yf_shares":           fi_shares,
            "yf_bizSummary":       biz,
            "yf_recentNews":       recent_news,
            "yf_recChanges":       rec_changes,
            "yf_epsHistory":       eps_history,
            "yf_recMean":          rec_mean,
            "yf_recKey":           rec_key,
            "yf_recNumAnalysts":   rec_num_analysts,
        }
    except Exception:
        return {}


def fetch_yfinance_batch(tickers: list) -> dict:
    result = {}
    if not tickers:
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_fetch_yfinance_one, tk): tk for tk in tickers}
        for fut in concurrent.futures.as_completed(futs):
            tk = futs[fut]
            try:
                result[tk] = fut.result()
            except Exception:
                result[tk] = {}
    return result


def fetch_krx_foreign_flow(kr_codes: list) -> dict:
    """Naver Finance에서 외국인/기관 수급 파싱"""
    result = {}
    if not _HAS_BS4:
        return result

    def _fetch_naver_news(code: str) -> str:
        """Naver Finance 종목 뉴스 — 최근 2일 이내 헤드라인 최대 3건."""
        try:
            url = f"https://finance.naver.com/item/news_news.naver?code={code}&page=1&sm=title_entity_id.basic&clusterId="
            r = requests.get(url, timeout=6, verify=False,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "lxml")
            rows = soup.select("table.type5 tr")
            from datetime import datetime as _dt4
            cutoff = _dt4.now() - timedelta(days=2)
            items = []
            for tr in rows:
                title_el = tr.select_one("td.title a")
                date_el  = tr.select_one("td.date")
                src_el   = tr.select_one("td.info")
                if not (title_el and date_el):
                    continue
                date_s = date_el.get_text(strip=True).split()[0]
                try:
                    dt = _dt4.strptime(date_s, "%Y.%m.%d")
                except Exception:
                    continue
                if dt < cutoff:
                    continue
                title = title_el.get_text(strip=True)
                src   = src_el.get_text(strip=True) if src_el else ""
                age   = (_dt4.now() - dt).days
                age_s = "오늘" if age == 0 else f"{age}일전"
                items.append(f"[{age_s}/{src}] {title[:80]}")
                if len(items) >= 3:
                    break
            return " | ".join(items)
        except Exception:
            return ""

    def _fetch_one(code: str) -> dict:
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        try:
            resp = requests.get(url, timeout=8, verify=False,
                                headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "lxml")

            # 한국 기업명: <title>삼성전자 : 외국인/기관 : ...</title>
            kr_name = ""
            title_el = soup.select_one("title")
            if title_el:
                raw_title = title_el.get_text(strip=True)
                # "삼성전자 : 외국인/기관 : ..." → "삼성전자"
                part = raw_title.split(":")[0].strip()
                if part and part not in _INVALID_KR_NAMES and len(part) <= 30:
                    kr_name = part

            rows = soup.select("table.type2 tr")
            frgn_vals, inst_vals = [], []
            for tr in rows[1:22]:
                tds = tr.select("td")
                if len(tds) < 8:
                    continue
                def _num(s):
                    s = re.sub(r"[^\d\-]", "", s.replace(",", ""))
                    return float(s) if s else 0.0
                frgn_vals.append(_num(tds[4].get_text()))
                inst_vals.append(_num(tds[5].get_text()))

            # 지분율
            frgn_pct = None
            pct_el = soup.select_one("em#lrate")
            if pct_el:
                try:
                    frgn_pct = float(pct_el.get_text().replace("%","").strip())
                except Exception:
                    pass

            f5  = sum(frgn_vals[:5])   / 1e8
            f20 = sum(frgn_vals[:20])  / 1e8
            i5  = sum(inst_vals[:5])   / 1e8
            i20 = sum(inst_vals[:20])  / 1e8
            news = _fetch_naver_news(code)
            return {
                "naver_기업명":       kr_name,
                "외국인_순매수_5일":  f5,
                "외국인_순매수_20일": f20,
                "외국인_지분율%":     frgn_pct,
                "외국인_지분율_변화": None,
                "기관_순매수_5일":    i5,
                "기관_순매수_20일":   i20,
                "naver_최근뉴스":     news,
            }
        except Exception:
            return {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_one, c): c for c in kr_codes}
        for fut in concurrent.futures.as_completed(futs):
            code = futs[fut]
            try:
                result[code] = fut.result()
            except Exception:
                result[code] = {}
    return result


def fetch_fnguide_info(code: str) -> dict:
    """FnGuide 컨센서스 + 사업개요 (타임아웃 5초)"""
    out = {}
    if not _HAS_BS4:
        return out
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # 컨센서스
        url_c = f"http://comp.fnguide.com/SVO2/ASP/SVD_Consensus.asp?pGB=1&gicode=A{code}"
        r = requests.get(url_c, timeout=5, verify=False, headers=headers)
        soup = BeautifulSoup(r.text, "lxml")
        tgt_el = soup.select_one("span#rptTgtPrc")
        if tgt_el:
            try:
                out["fnguide_목표주가"] = float(re.sub(r"[^\d.]", "", tgt_el.get_text()))
            except Exception:
                pass
        rep_rows = soup.select("table#rpt_consensus tr")
        if len(rep_rows) > 1:
            tds = rep_rows[1].select("td")
            if tds:
                out["최근리포트일"]    = tds[0].get_text(strip=True)
                out["최근리포트증권사"] = tds[1].get_text(strip=True) if len(tds) > 1 else ""
                out["최근리포트의견"]  = tds[2].get_text(strip=True) if len(tds) > 2 else ""
                out["최근리포트제목"]  = tds[3].get_text(strip=True) if len(tds) > 3 else ""
        cnt_el = soup.select_one("span#rptCnt")
        if cnt_el:
            try:
                out["컨센서스_증권사수"] = int(re.sub(r"[^\d]", "", cnt_el.get_text()))
            except Exception:
                pass
    except Exception:
        pass
    try:
        # 사업개요
        url_m = f"http://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}"
        r = requests.get(url_m, timeout=5, verify=False, headers=headers)
        soup = BeautifulSoup(r.text, "lxml")
        biz_el = soup.select_one("div#bizSummary")
        if biz_el:
            out["fnguide_사업개요"] = biz_el.get_text(separator=" ", strip=True)[:300]
    except Exception:
        pass
    return out


def enrich_korean_rows_with_fnguide(rows: list):
    kr_rows = [r for r in rows if r.get("_country") == "KR"]
    if not kr_rows:
        return
    def _enrich(row):
        code = _bare_kr_code(row.get("_ticker", ""))
        if not re.fullmatch(r"\d{6}", code):
            return
        info = fetch_fnguide_info(code)
        row.update(info)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(_enrich, kr_rows))


def _load_cusip_cache() -> dict[str, str]:
    """CUSIP → 티커 캐시 로드 (data/cusip_cache.json)"""
    if CUSIP_CACHE_JSON.exists():
        try:
            return json.loads(CUSIP_CACHE_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cusip_cache(cache: dict[str, str]) -> None:
    try:
        CUSIP_CACHE_JSON.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# 13F 빈출 CUSIP 정적 매핑 — OpenFIGI 미응답 시 폴백
# 미국 시가총액 상위 종목 + 유명 펀드 빈출 보유 종목 기준
_STATIC_CUSIP_MAP = {
    "037833100":"AAPL","594918104":"MSFT","023135106":"AMZN",
    "67066G104":"NVDA","02079K305":"GOOG","02079K107":"GOOGL",
    "30303M102":"META","88160R101":"TSLA","11135F101":"BRK.B",
    "084670702":"BRK.B","084670108":"BRK.A","91324P102":"UNH",
    "92826C839":"V","57636Q104":"MA","478160104":"JNJ",
    "742718109":"PG","742718208":"PG","00287Y109":"ABBV",
    "46625H100":"JPM","17275R102":"CSCO","166764100":"CVX",
    "30231G102":"XOM","532457108":"LLY","79466L302":"CRM",
    "874039100":"TXN","458140100":"INTC","68389X105":"ORCL",
    "00206R102":"T","58933Y105":"MRK","716941104":"PFE",
    "037411105":"AMD","20825C104":"COP","20030N101":"CMCSA",
    "00130H105":"NEE","007903107":"AMR","254687106":"DIS",
    "92343V104":"VZ","580135101":"MCD","191216100":"KO",
    "717081103":"PEP","87612E106":"TGT","931142103":"WMT",
    "742718IP1":"PG","02079K10":"GOOGL","49271V100":"KEYS",
    "G0750C108":"BABA","00287Y10":"ABBV","78462F103":"SPY",
    "92826C83":"V","81141R100":"SCHW","911312106":"UPS",
    "92556H206":"VICI","L8681T102":"TSM","N07059210":"ASML",
    "G06242104":"BLK","370334104":"GS","060505104":"BAC",
    "949746101":"WFC","00724F101":"ADBE","68389X10":"ORCL",
    "G2519Y108":"COIN","68902V107":"OXY","127190304":"CAH",
    "025537101":"AAL","254709108":"DIS","375558103":"GLD",
    "29786A106":"ETSY","30161N101":"AAL","375558206":"GLD",
    "G2554F113":"SPOT","458140208":"INTC","741503403":"PSA",
    "595112103":"MID","002824100":"ABT","254709309":"DIS",
    "20825C10":"COP","00724F10":"ADBE","56035L104":"ROST",
    "70450Y103":"PYPL","82968B103":"SIRI","16934Q205":"CHK",
    "747525103":"QCOM","747525203":"QCOM","68233D106":"OMC",
    "00206R10":"T","742718IP":"PG","30303M10":"META",
    "74739P101":"PRU","91912E105":"VLO","532457104":"LLY",
    "53807F108":"LBRDA","260543103":"DRI","743315103":"PG",
    "G7945E105":"RYAAY","11135F10":"BRK.B","11135F40":"BRK.B",
}


def _resolve_cusips(cusips: list[str]) -> dict[str, str]:
    """OpenFIGI API로 CUSIP → 티커 변환 (배치 25개, 캐시 우선).

    무료 티어: 25개/요청, ~5 req/min → 2초 딜레이.
    최초 실행 시 최대 200개 조회; 이후 캐시로 즉시 처리.
    실패 시 _STATIC_CUSIP_MAP 폴백.
    """
    cache = _load_cusip_cache()
    # 정적 매핑 선반영
    for c, tk in _STATIC_CUSIP_MAP.items():
        if c not in cache or not cache.get(c):
            cache[c] = tk
    unknown = list(dict.fromkeys(
        c for c in cusips if c and not cache.get(c)
    ))[:200]

    if unknown:
        batch_size = 25
        for i in range(0, len(unknown), batch_size):
            batch = unknown[i:i + batch_size]
            try:
                r = requests.post(
                    "https://api.openfigi.com/v3/mapping",
                    json=[{"idType": "ID_CUSIP", "idValue": c} for c in batch],
                    headers={"Content-Type": "application/json"},
                    timeout=15, verify=False,
                )
                if r.status_code == 200:
                    for cusip, item in zip(batch, r.json()):
                        found = ""
                        if isinstance(item, dict):
                            for entry in (item.get("data") or []):
                                tk   = entry.get("ticker", "")
                                sec2 = entry.get("securityType2", "")
                                if tk and sec2 == "Common Stock":
                                    found = tk
                                    break
                                elif tk and not found:
                                    found = tk
                        cache[cusip] = found
            except Exception:
                pass
            time.sleep(2)
        _save_cusip_cache(cache)

    return cache


def _parse_13f_xml(xml_text: str) -> list[dict]:
    """정규식으로 13F infotable XML 파싱 (네임스페이스 무관)"""
    # 네임스페이스 접두어 제거
    clean = re.sub(r'<(/?)[\w]+:', r'<\1', xml_text)
    entries = re.findall(r'<infoTable>(.*?)</infoTable>', clean,
                         re.DOTALL | re.IGNORECASE)
    if not entries:
        entries = re.findall(r'<InfoTable>(.*?)</InfoTable>', xml_text,
                             re.DOTALL | re.IGNORECASE)

    def _g(text, tag):
        m = re.search(rf'<{tag}[^>]*>\s*(.*?)\s*</{tag}>', text,
                      re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    out = []
    for e in entries:
        val_str = _g(e, "value")
        try:
            val = float(re.sub(r"[^\d.]", "", val_str)) * 1000
        except Exception:
            val = 0.0
        out.append({
            "name":   _g(e, "nameOfIssuer"),
            "cusip":  _g(e, "cusip"),
            "ticker": _g(e, "ticker"),
            "value":  val,
            "shares": _g(e, "sshPrnamt"),
            "class":  _g(e, "titleOfClass"),
        })
    out.sort(key=lambda x: -x["value"])
    return out[:150]


def _fetch_one_13f_xml(cik: str, acc_nodash: str, acc_orig: str,
                        ua: dict) -> list[dict]:
    """단일 13F 신고의 infotable XML을 파싱하여 holdings 반환."""
    base = (f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{acc_nodash}/")
    xml_file = None
    try:
        idx = requests.get(f"{base}{acc_orig}-index.json",
                           timeout=10, verify=False, headers=ua)
        items = idx.json().get("directory", {}).get("item", [])
        for it in items:
            n = it.get("name", "")
            if n.lower().endswith(".xml") and "infotable" in n.lower():
                xml_file = n
                break
        if not xml_file:
            for it in items:
                n = it.get("name", "")
                if n.lower().endswith(".xml") and "primary" not in n.lower():
                    xml_file = n
                    break
    except Exception:
        pass
    if not xml_file and _HAS_BS4:
        try:
            dir_r = requests.get(base, timeout=10, verify=False, headers=ua)
            soup  = BeautifulSoup(dir_r.text, "lxml")
            for a in soup.select("a[href]"):
                h = a["href"]
                if h.endswith(".xml") and "infotable" in h.lower():
                    xml_file = h.split("/")[-1]
                    break
        except Exception:
            pass
    if not xml_file:
        return []
    try:
        xml_r = requests.get(f"{base}{xml_file}", timeout=20,
                              verify=False, headers=ua)
        return _parse_13f_xml(xml_r.text)
    except Exception:
        return []


def fetch_famous_manager_rows() -> list[dict]:
    """SEC EDGAR 13F 수집 + 전분기 대비 포지션 변화 계산.

    각 기관의 최근 2개 13F-HR 신고를 비교해 신규/증가/감소/유지 판정.
    한국 6자리 티커 완전 제외.
    """
    result = []
    ua = {"User-Agent": "deepdive-research/1.0 contact@example.com"}
    for mgr_name, cik in FAMOUS_MANAGERS:
        try:
            cik_padded = cik.zfill(10)
            sub_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
            resp = requests.get(sub_url, timeout=12, verify=False, headers=ua)
            if resp.status_code != 200:
                continue
            recent   = resp.json().get("filings", {}).get("recent", {})
            forms    = recent.get("form", [])
            acc_nums = recent.get("accessionNumber", [])
            dates    = (recent.get("filingDate") or
                        recent.get("reportDate") or
                        [""] * len(forms))

            # 최근 2개 13F-HR 찾기 (현재 분기 + 전분기)
            filings_13f = []
            for form, acc, dt in zip(forms, acc_nums, dates):
                if form in ("13F-HR", "13F-HR/A"):
                    filings_13f.append((acc, dt))
                    if len(filings_13f) >= 2:
                        break
            if not filings_13f:
                continue

            curr_acc_orig   = filings_13f[0][0]
            curr_acc_nodash = curr_acc_orig.replace("-", "")
            curr_date       = filings_13f[0][1]

            curr_holdings = _fetch_one_13f_xml(cik, curr_acc_nodash,
                                                curr_acc_orig, ua)

            # 전분기 파싱 → ticker → shares 맵
            prev_shares_map: dict[str, float] = {}
            if len(filings_13f) >= 2:
                prev_acc_orig   = filings_13f[1][0]
                prev_acc_nodash = prev_acc_orig.replace("-", "")
                prev_h = _fetch_one_13f_xml(cik, prev_acc_nodash,
                                             prev_acc_orig, ua)
                for ph in prev_h:
                    tk = ph["ticker"]
                    if tk and not re.fullmatch(r"\d{6}", tk):
                        prev_shares_map[tk] = float(ph["shares"] or 0)

            for h in curr_holdings:
                tk = h["ticker"]
                if re.fullmatch(r"\d{6}", tk):
                    continue
                curr_sh = float(h["shares"] or 0)
                prev_sh = prev_shares_map.get(tk)

                if prev_sh is None:
                    change_type = "신규"
                    change_pct  = None
                elif curr_sh > prev_sh * 1.01:
                    change_type = "증가"
                    change_pct  = round((curr_sh - prev_sh) / prev_sh * 100, 1) \
                                  if prev_sh else None
                elif curr_sh < prev_sh * 0.99:
                    change_type = "감소"
                    change_pct  = round((curr_sh - prev_sh) / prev_sh * 100, 1) \
                                  if prev_sh else None
                else:
                    change_type = "유지"
                    change_pct  = 0.0

                result.append({
                    "기관명":        mgr_name,
                    "보고일":        curr_date or "",
                    "종목명":        h["name"],
                    "티커":          tk or h["cusip"],
                    "CUSIP":         h["cusip"],
                    "보유가치_USD":   h["value"],
                    "주식수":        curr_sh,
                    "전분기_주식수":  prev_sh,
                    "주식수_변화율%": change_pct,
                    "포지션변화":    change_type,
                    "주식종류":      h["class"],
                    "_수집일":       _NOW.strftime("%Y-%m-%d"),
                })
        except Exception as e:
            print(f"[13F] {mgr_name}: {e}")
    result.sort(key=lambda x: -(x.get("보유가치_USD") or 0))

    # ── CUSIP → 실제 티커 변환 (캐시 + OpenFIGI) ──────────────────────
    all_cusips = [r.get("CUSIP", "") for r in result]
    cusip_map  = _resolve_cusips(all_cusips)
    for r in result:
        cusip    = r.get("CUSIP", "")
        resolved = cusip_map.get(cusip, "")
        if resolved:
            r["티커"] = resolved
        elif not r.get("티커"):
            r["티커"] = cusip

    # ── 기관별 포트폴리오 총액 → 포트폴리오비중% ────────────────────────
    from collections import defaultdict as _dd
    _inst_total: dict[str, float] = _dd(float)
    for r in result:
        _inst_total[r["기관명"]] += float(r.get("보유가치_USD") or 0)
    for r in result:
        total = _inst_total[r["기관명"]]
        val   = float(r.get("보유가치_USD") or 0)
        r["포트폴리오비중%"] = round(val / total * 100, 2) if total > 0 else None

    save_13f_history(result, DATA_DIR / "13f_history.csv")
    if not result:
        # SEC 응답 실패 — 최근 캐시 폴백 (가장 최근 _수집일)
        cached = _read_csv_as_list(DATA_DIR / "13f_history.csv")
        if cached:
            latest_date = max((c.get("_수집일","") for c in cached), default="")
            if latest_date:
                fallback = [c for c in cached if c.get("_수집일","") == latest_date]
                print(f"[13F] 수집 실패 — 캐시 폴백 ({latest_date}, {len(fallback)}건)")
                return fallback
    return result


def fetch_institutional_overlap(all_holdings: list[dict]) -> list[dict]:
    """다수 기관의 포지션 변화 집계 (2개 이상 기관 보유 종목).

    신규/증가 기관이 많을수록 컨센서스점수 높음.
    컨센서스점수 = 신규기관수×3 + 증가기관수×2 - 감소기관수
    """
    from collections import defaultdict
    ticker_map: dict[str, dict] = defaultdict(lambda: {
        "기관목록": [], "신규기관목록": [], "증가기관목록": [], "감소기관목록": [],
        "총보유가치_USD": 0.0, "종목명": "", "티커": "",
        "보고일_set": set(),
    })
    for h in all_holdings:
        tk = h.get("티커", "")
        # 유효한 티커만 (CUSIP-스타일 8~9자리 숫자, 한국 6자리 숫자 제외)
        if not tk:
            continue
        if re.fullmatch(r"\d{6,9}", tk):
            continue
        if re.fullmatch(r"[0-9]{1,3}[A-Z]{1,3}[0-9]{0,3}", tk):
            # CUSIP 잔여 패턴 (예: 037833100)
            continue
        if len(tk) > 8:  # 정상 티커는 최대 6자
            continue
        d   = ticker_map[tk]
        mgr = h.get("기관명", "")
        chg = h.get("포지션변화", "유지")
        dt  = h.get("보고일", "")
        if mgr and mgr not in d["기관목록"]:
            d["기관목록"].append(mgr)
        if chg == "신규" and mgr not in d["신규기관목록"]:
            d["신규기관목록"].append(mgr)
        elif chg == "증가" and mgr not in d["증가기관목록"]:
            d["증가기관목록"].append(mgr)
        elif chg == "감소" and mgr not in d["감소기관목록"]:
            d["감소기관목록"].append(mgr)
        d["총보유가치_USD"] += float(h.get("보유가치_USD", 0) or 0)
        if not d["종목명"]:
            d["종목명"] = h.get("종목명", "")
        d["티커"] = tk
        if dt:
            d["보고일_set"].add(dt)

    rows = []
    for tk, d in ticker_map.items():
        n_inst = len(d["기관목록"])
        if n_inst < 2:
            continue
        n_new  = len(d["신규기관목록"])
        n_inc  = len(d["증가기관목록"])
        n_dec  = len(d["감소기관목록"])
        score  = n_new * 3 + n_inc * 2 - n_dec
        latest = max(d["보고일_set"]) if d["보고일_set"] else ""
        rows.append({
            "티커":          tk,
            "종목명":        d["종목명"],
            "기관수":        n_inst,
            "신규기관수":    n_new,
            "증가기관수":    n_inc,
            "감소기관수":    n_dec,
            "컨센서스점수":  score,
            "신규기관":      " / ".join(d["신규기관목록"]) if d["신규기관목록"] else "",
            "증가기관":      " / ".join(d["증가기관목록"]) if d["증가기관목록"] else "",
            "기관목록":      " / ".join(sorted(d["기관목록"])),
            "총보유가치_USD": round(d["총보유가치_USD"], 0),
            "보고일":        latest,
        })
    rows.sort(key=lambda x: (-x["컨센서스점수"], -x["총보유가치_USD"]))
    return rows


def save_13f_history(rows: list[dict], csv_path: Path):
    """13F 결과 누적 저장 (날짜+기관+티커 복합키, 기존 데이터 유지)"""
    existing = _read_csv_as_list(csv_path)
    today = _NOW.strftime("%Y-%m-%d")
    today_keys = {f"{today}|{r.get('기관명','')}|{r.get('티커','')}": True for r in rows}
    kept = [r for r in existing
            if f"{r.get('_수집일','')}|{r.get('기관명','')}|{r.get('티커','')}" not in today_keys]
    merged = kept + [dict(r, _수집일=today) for r in rows if r.get("티커")]
    _write_csv(csv_path, merged)


# ───────────────────────────────────────────────
# 섹션 3b: 시장지표 / 내부자거래 수집
# ───────────────────────────────────────────────

def fetch_fear_greed() -> dict:
    """CNN Fear & Greed Index — curl_cffi 로 봇 감지 우회. 실패시 캐시 폴백."""
    import json as _json2
    try:
        if _YF_SESSION is not None:
            r = _YF_SESSION.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                timeout=10)
        else:
            r = requests.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                timeout=8, verify=False,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        d = r.json().get("fear_and_greed", {})
        out = {
            "score":    round(float(d.get("score", 0)), 1),
            "rating":   d.get("rating", ""),
            "prev_1w":  round(float(d.get("previous_1_week", 0) or 0), 1),
            "prev_1m":  round(float(d.get("previous_1_month", 0) or 0), 1),
            "prev_1y":  round(float(d.get("previous_1_year", 0) or 0), 1),
        }
        try:
            FEAR_GREED_JSON.write_text(_json2.dumps(out, ensure_ascii=False),
                                       encoding="utf-8")
        except Exception:
            pass
        return out
    except Exception:
        try:
            if FEAR_GREED_JSON.exists():
                return _json2.loads(FEAR_GREED_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"score": None, "rating": "N/A"}


def fetch_yahoo_market() -> list[dict]:
    """yfinance fast_info 로 주요 시장 지표 수집. 실패시 캐시 폴백."""
    if not _HAS_YF:
        return _load_csv_cache(MARKET_INDEX_CSV)
    symbols = [
        ("^VIX",     "VIX 공포지수"),
        ("^GSPC",    "S&P500"),
        ("^IXIC",    "NASDAQ"),
        ("^DJI",     "Dow Jones"),
        ("^KS11",    "KOSPI"),
        ("^KQ11",    "KOSDAQ"),
        ("KRW=X",    "USD/KRW"),
        ("DX-Y.NYB", "DXY 달러인덱스"),
        ("GC=F",     "Gold 금"),
        ("CL=F",     "WTI 원유"),
        ("BTC-USD",  "Bitcoin"),
    ]
    out = []
    for sym, label in symbols:
        try:
            fi = yf.Ticker(sym).fast_info
            cur  = _safe(fi.get("lastPrice"))
            hi52 = _safe(fi.get("yearHigh"))
            lo52 = _safe(fi.get("yearLow"))
            prev = _safe(fi.get("regularMarketPreviousClose"))
            chg  = None
            if cur is not None and prev and prev != 0:
                chg = round((cur - prev) / prev * 100, 2)
            pos = None
            if cur is not None and hi52 and lo52 and (hi52 - lo52) > 0:
                pos = round((cur - lo52) / (hi52 - lo52) * 100, 1)
            out.append({
                "지표":      label,
                "심볼":      sym,
                "현재가":    cur,
                "전일비%":   chg,
                "52주위치%": pos,
                "52주고가":  hi52,
                "52주저가":  lo52,
            })
        except Exception:
            continue
    if out:
        _save_csv_cache(MARKET_INDEX_CSV, out)
        return out
    return _load_csv_cache(MARKET_INDEX_CSV)


INSIDER_BUYS_CSV    = DATA_DIR / "insider_buys_cache.csv"
ANALYST_RATINGS_CSV = DATA_DIR / "analyst_ratings_cache.csv"
MARKET_INDEX_CSV    = DATA_DIR / "market_index_cache.csv"
SECTOR_PERF_CSV     = DATA_DIR / "sector_perf_cache.csv"
EARNINGS_CAL_CSV    = DATA_DIR / "earnings_cal_cache.csv"
FEAR_GREED_JSON     = DATA_DIR / "fear_greed_cache.json"


def _save_csv_cache(path: Path, rows: list[dict]):
    """범용 CSV 캐시 저장 — 빈 rows는 무시 (기존 캐시 유지)."""
    if not rows:
        return
    try:
        _write_csv(path, rows)
    except Exception as e:
        print(f"[캐시저장] {path.name}: {e}")


def _load_csv_cache(path: Path) -> list[dict]:
    """범용 CSV 캐시 로드."""
    try:
        return _read_csv_as_list(path)
    except Exception:
        return []


def fetch_insider_buys() -> list[dict]:
    """openinsider.com 최근 내부자 매수 (14일 이내, $100K+).

    수집 성공 시 캐시 갱신, 실패 시 기존 캐시 반환.
    """
    if not _HAS_BS4:
        return _load_csv_cache(INSIDER_BUYS_CSV)
    url = ("http://openinsider.com/screener?"
           "s=&o=&pl=10&ph=&ll=&lh=&fd=14&fdr=&td=0&tdr=&xp=1&vl=100"
           "&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0"
           "&sortcol=0&cnt=40&page=1")
    try:
        r = requests.get(url, timeout=15, verify=False,
                         headers={"User-Agent":
                                  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36"})
        if r.status_code != 200:
            print(f"[내부자] HTTP {r.status_code} — 캐시 폴백")
            return _load_csv_cache(INSIDER_BUYS_CSV)
        soup = BeautifulSoup(r.text, "lxml")
        tbl  = soup.select_one("table.tinytable")
        if not tbl:
            return _load_csv_cache(INSIDER_BUYS_CSV)
        hdrs = [th.get_text(strip=True) for th in tbl.select("thead th")]
        rows = []
        for tr in tbl.select("tbody tr")[:60]:
            tds = [td.get_text(strip=True) for td in tr.select("td")]
            if len(tds) < 8:
                continue
            d = dict(zip(hdrs, tds))
            rows.append({
                "신고일":   d.get("Filing\xa0Date") or d.get("Filing Date") or "",
                "거래일":   d.get("Trade\xa0Date")  or d.get("Trade Date")  or "",
                "티커":     d.get("Ticker", ""),
                "회사명":   d.get("Company\xa0Name") or d.get("Company Name") or "",
                "임원명":   d.get("Insider\xa0Name") or d.get("Insider Name") or "",
                "직책":     d.get("Title", ""),
                "거래유형": d.get("Trade\xa0Type")   or d.get("Trade Type")   or "",
                "가격":     d.get("Price", ""),
                "수량":     d.get("Qty", ""),
                "거래금액": d.get("Value", ""),
                "보유주식": d.get("Owned", ""),
            })
        if rows:
            _save_csv_cache(INSIDER_BUYS_CSV, rows)
            return rows
        return _load_csv_cache(INSIDER_BUYS_CSV)
    except Exception as e:
        print(f"[내부자] 예외: {e} — 캐시 폴백")
        return _load_csv_cache(INSIDER_BUYS_CSV)


def fetch_sector_performance() -> list[dict]:
    """미국 섹터 ETF 성과 (XLK, XLF, XLE 등). 실패시 캐시 폴백."""
    if not _HAS_YF:
        return _load_csv_cache(SECTOR_PERF_CSV)
    sectors = [
        ("XLK", "기술"),  ("XLF", "금융"),  ("XLE", "에너지"),
        ("XLV", "헬스케어"),("XLI", "산업"), ("XLY", "소비재"),
        ("XLP", "필수소비"),("XLU", "유틸리티"),("XLB", "소재"),
        ("XLRE","부동산"), ("XLC", "통신"),  ("QQQ", "NASDAQ100"),
        ("IWM", "Russell2000"),("EEM","신흥국"),("EWY","한국ETF"),
    ]
    out = []
    for sym, label in sectors:
        try:
            fi = yf.Ticker(sym).fast_info
            cur  = _safe(fi.get("lastPrice"))
            prev = _safe(fi.get("regularMarketPreviousClose"))
            hi52 = _safe(fi.get("yearHigh"))
            lo52 = _safe(fi.get("yearLow"))
            chg_1d = round((cur - prev) / prev * 100, 2) if cur and prev else None
            pos = round((cur - lo52)/(hi52 - lo52)*100,1) if cur and hi52 and lo52 and (hi52-lo52)>0 else None
            # 1달 수익률 (approx via 1M history)
            try:
                hist = yf.Ticker(sym).history(period="1mo", auto_adjust=True)
                chg_1m = round((hist["Close"].iloc[-1]/hist["Close"].iloc[0]-1)*100,1) if len(hist)>2 else None
            except Exception:
                chg_1m = None
            out.append({
                "섹터":      label,
                "심볼":      sym,
                "현재가":    cur,
                "전일비%":   chg_1d,
                "1개월수익%": chg_1m,
                "52주위치%": pos,
            })
        except Exception:
            continue
    if out:
        _save_csv_cache(SECTOR_PERF_CSV, out)
        return out
    return _load_csv_cache(SECTOR_PERF_CSV)


def fetch_earnings_calendar() -> list[dict]:
    """향후 2주 실적 발표 예정 종목 (TradingView 52주 신고가 목록 기반). 실패시 캐시 폴백."""
    if not _HAS_YF:
        return _load_csv_cache(EARNINGS_CAL_CSV)
    # 52주 신고가 목록에서 티커 추출해 실적일 확인
    # TV 결과가 없을 때는 주요 대형주 고정 목록 사용
    watch = ["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA",
             "AVGO","AMD","ORCL","CRM","NFLX","JPM","GS","BAC",
             "LLY","JNJ","UNH","WMT","COST","HD","V","MA"]
    rows = []
    today = _NOW.date()
    two_weeks = today + timedelta(days=14)
    for sym in watch:
        try:
            info = yf.Ticker(sym).info
            rd = info.get("earningsDate") or info.get("earningsTimestamp")
            if rd:
                if isinstance(rd, (int, float)):
                    from datetime import date as _date
                    rd = datetime.utcfromtimestamp(rd).date()
                elif hasattr(rd, "date"):
                    rd = rd.date()
                if today <= rd <= two_weeks:
                    rows.append({
                        "실적일":     rd.strftime("%m/%d"),
                        "티커":       sym,
                        "회사명":     info.get("shortName",""),
                        "섹터":       info.get("sector",""),
                        "예상EPS":    info.get("epsForward",""),
                        "Forward_PER": info.get("forwardPE",""),
                        "매출_YoY%":  info.get("revenueGrowth",""),
                    })
        except Exception:
            continue
    rows.sort(key=lambda x: x.get("실적일",""))
    if rows:
        _save_csv_cache(EARNINGS_CAL_CSV, rows)
        return rows
    return _load_csv_cache(EARNINGS_CAL_CSV)


# ───────────────────────────────────────────────
# 섹션 4: 스코어링
# ───────────────────────────────────────────────

def detect_flow_pattern(row: dict) -> dict:
    f5  = _safe(row.get("외국인_순매수_5일"),  0.0) or 0.0
    f20 = _safe(row.get("외국인_순매수_20일"), 0.0) or 0.0
    i5  = _safe(row.get("기관_순매수_5일"),   0.0) or 0.0
    pos52 = _safe(row.get("52주고가대비위치%"), 50.0) or 50.0

    if pos52 < 40 and f20 > 0 and i5 > 0:
        pattern = "저점매집"
    elif f5 > 0 and i5 > 0 and f20 <= 0:
        pattern = "기관외국인동반반전"
    elif f5 > 0 and f20 <= 0:
        pattern = "반전신호"
    elif pos52 > 80 and f20 > 0:
        pattern = "고점추세"
    elif pos52 > 80 and f5 < 0:
        pattern = "고점청산"
    else:
        pattern = ""

    abs_f20 = abs(f20) if f20 != 0 else 1.0
    ratio = f5 / abs_f20
    accel = "가속" if ratio > 2 else ("역전" if ratio < -1 else "")

    return {
        "수급패턴":    pattern,
        "수급가속도":  accel,
        "저점매집여부": "Y" if pattern == "저점매집" else "",
        "고점청산여부": "Y" if pattern == "고점청산" else "",
        "수급반전일수": "",
    }


def leading_trade_score(row: dict) -> float:
    pattern = row.get("수급패턴", "")
    accel   = row.get("수급가속도", "")
    f5      = _safe(row.get("외국인_순매수_5일"),  0.0) or 0.0
    i5      = _safe(row.get("기관_순매수_5일"),   0.0) or 0.0
    eps_sur = _safe(row.get("EPS_서프라이즈%"),   0.0) or 0.0
    short   = _safe(row.get("공매도비율%"),        0.0) or 0.0

    score = 50.0
    if pattern == "저점매집":         score += 30
    elif pattern == "기관외국인동반반전": score += 25
    elif pattern == "반전신호":        score += 20
    elif pattern == "고점추세":        score += 10
    elif pattern == "고점청산":        score -= 30

    if accel == "가속":   score += 10
    elif accel == "역전": score -= 15

    if f5 > 0:
        score += min(10, f5 / 10)
    elif f5 < 0:
        score += max(-10, f5 / 10)

    if i5 > 0:
        score += 5

    if eps_sur >= 5:  score += 5
    if short >= 10:   score -= 10

    return max(0.0, min(100.0, score))


def export_overseas_score(row: dict) -> float:
    score = 0.0
    sector  = str(row.get("섹터", "") or "")
    country = row.get("_country", "")
    code    = _bare_kr_code(row.get("_ticker", ""))

    if country == "KR" and code in KOREAN_EXPORTERS:
        score = float(KOREAN_EXPORTERS[code])
    else:
        for key, bonus in EXPORT_SECTOR_BONUS.items():
            if key.lower() in sector.lower():
                score = max(score, float(bonus))

    capex = _safe(row.get("설비투자_TTM"), 0.0) or 0.0
    if capex > 0:
        score += 5

    return min(100.0, score * 4)


def _detect_future_theme(row: dict) -> tuple[str, str]:
    combined = " ".join([
        str(row.get("_ticker", "")),
        str(row.get("기업명", "")),
        str(row.get("섹터", "")),
        str(row.get("산업", "")),
    ]).lower()

    themes_hit = []
    for theme, keywords in FUTURE_THEMES.items():
        for kw in keywords:
            if kw.lower() in combined:
                themes_hit.append(theme)
                break

    if themes_hit:
        return ", ".join(themes_hit), ", ".join(themes_hit[:2])
    return "", ""


def compute_priority(row: dict) -> tuple[float, str]:
    """13개 차원별 0~100 점수 계산 → 가중합 → 등급 반환"""
    scores: dict[str, float] = {}

    # ① 밸류에이션
    fpe = _safe(row.get("Forward_PER")) or _safe(row.get("yf_forwardPE"))
    peg = _safe(row.get("PEG_TTM"))    or _safe(row.get("yf_pegRatio"))
    tgt_upr = _safe(row.get("목표가상승여력%"), 0.0) or 0.0
    v = 50.0
    if fpe is not None:
        if   fpe < 15: v += 30
        elif fpe < 25: v += 20
        elif fpe < 40: v += 5
        elif fpe > 60: v -= 20
    if peg is not None and peg < 1: v += 15
    v += min(20, tgt_upr * 0.4)
    scores["밸류점수"] = max(0, min(100, v))

    # ② 성장/컨센서스
    rev_nfy = _safe(row.get("예상매출성장률_NextFY%"), 0.0) or 0.0
    eps_nfy = _safe(row.get("예상EPS성장률_NextFY%"), 0.0) or 0.0
    rev_yoy = _safe(row.get("매출성장률_YoY%"), 0.0) or 0.0
    eps_yoy = _safe(row.get("EPS성장률_YoY%"), 0.0) or 0.0
    g = 40.0
    if   rev_nfy >= 30: g += 30
    elif rev_nfy >= 15: g += 20
    elif rev_nfy >= 5:  g += 10
    elif rev_nfy < 0:   g -= 10
    if   eps_nfy >= 20: g += 15
    elif eps_nfy >= 5:  g += 8
    if rev_yoy > 0:  g += 5
    if eps_yoy > 0:  g += 5
    if row.get("컨센서스_증권사수", 0) or 0 >= 3: g += 5
    scores["성장점수"] = max(0, min(100, g))

    # ③ 수익성/재무품질
    op_mg = _safe(row.get("영업이익률%"), 0.0) or 0.0
    roe   = _safe(row.get("ROE%"),        0.0) or 0.0
    q = 30.0
    if   op_mg >= 25: q += 25
    elif op_mg >= 15: q += 15
    elif op_mg >= 5:  q += 5
    if   roe >= 20: q += 20
    elif roe >= 10: q += 10
    scores["품질점수"] = max(0, min(100, q))

    # ④ 현금흐름
    fcf_mg  = _safe(row.get("FCF마진%"),  0.0) or 0.0
    roic    = _safe(row.get("ROIC%"),     0.0) or 0.0
    net_csh = _safe(row.get("순현금/시총%"), 0.0) or 0.0
    c = 40.0
    if   fcf_mg >= 20: c += 30
    elif fcf_mg >= 10: c += 20
    elif fcf_mg >= 5:  c += 10
    elif fcf_mg < 0:   c -= 20
    if roic >= 15: c += 10
    if net_csh > 0: c += 10
    scores["현금흐름점수"] = max(0, min(100, c))

    # ⑤ 외국인수급
    f5  = _safe(row.get("외국인_순매수_5일"),  0.0) or 0.0
    f20 = _safe(row.get("외국인_순매수_20일"), 0.0) or 0.0
    fpct_chg = _safe(row.get("외국인_지분율_변화"), 0.0) or 0.0
    ff = 50.0
    ff += 15 if f5 > 0 else -15
    ff += 20 if f20 > 0 else -20
    if fpct_chg >= 1: ff += 15
    scores["외국인수급점수"] = max(0, min(100, ff))

    # ⑥ 기관수급
    i5  = _safe(row.get("기관_순매수_5일"),  0.0) or 0.0
    i20 = _safe(row.get("기관_순매수_20일"), 0.0) or 0.0
    inst_pct = _safe(row.get("기관_보유%"),  0.0) or 0.0
    ii = 30.0
    if i5  > 0: ii += 20
    if i20 > 0: ii += 25
    if inst_pct >= 70: ii += 15
    scores["기관수급점수"] = max(0, min(100, ii))

    # ⑦ 선행매매
    scores["선행매매점수"] = _safe(row.get("선행매매점수"), 50.0) or 50.0

    # ⑧ 미래산업
    theme = row.get("미래산업테마", "")
    mt = 0.0
    if theme:
        mt = 70.0
        for t in str(theme).split(","):
            if t.strip() in _HIGH_GROWTH_THEMES:
                mt = min(100, mt + 20)
                break
    scores["미래산업점수"] = mt

    # ⑨ 수출/해외
    scores["수출해외점수"] = _safe(row.get("수출해외점수"), 0.0) or 0.0

    # ⑩ 장기안정성
    de   = _safe(row.get("부채비율"),   1.0) or 1.0
    cr   = _safe(row.get("유동비율"),   1.0) or 1.0
    div  = _safe(row.get("배당수익률%"), 0.0) or 0.0
    lt = 50.0
    if   de < 0.5: lt += 20
    elif de > 3:   lt -= 25
    if cr > 2: lt += 10
    if div > 2: lt += 10
    scores["장기투자점수"] = max(0, min(100, lt))

    # ⑪ 모멘텀
    rsi  = _safe(row.get("RSI"), 50.0) or 50.0
    p3m  = _safe(row.get("3개월수익률%"), 0.0) or 0.0
    mo = 30.0
    if 50 <= rsi <= 70: mo += 20
    elif rsi > 80:      mo -= 10
    if p3m >= 20: mo += 20
    elif p3m >= 10: mo += 10
    scores["모멘텀점수"] = max(0, min(100, mo))

    # ⑫ 투자의견
    rec = _safe(row.get("투자의견점수_raw"), 3.0) or 3.0
    inv_map = {1: 90, 2: 75, 3: 50, 4: 20, 5: 10}
    inv_score = inv_map.get(round(rec), 50)
    scores["투자의견점수"] = float(inv_score)

    # ⑬ 거래량
    rvol = _safe(row.get("상대거래량"), 1.0) or 1.0
    if   rvol >= 5: vv = 90.0
    elif rvol >= 3: vv = 75.0
    elif rvol >= 2: vv = 65.0
    else:           vv = 30.0
    scores["거래량점수"] = vv

    # 가중합
    total = 0.0
    for dim, wt in DEEP_WEIGHTS.items():
        field = SCORE_FIELD_BY_WEIGHT[dim]
        total += scores.get(field, 50.0) * wt / 100.0

    # 리스크 페널티
    short_pct = _safe(row.get("공매도비율%"), 0.0) or 0.0
    beta1y    = _safe(row.get("Beta_1Y"),   1.0) or 1.0
    if short_pct >= 15: total -= 5
    if beta1y   >= 2.0: total -= 3

    final = max(0.0, min(100.0, total))

    if   final >= 62: grade = "A"
    elif final >= 50: grade = "B"
    elif final >= 38: grade = "C"
    elif final >= 25: grade = "D"
    else:             grade = "F"

    scores["리스크페널티"] = -5.0 * (short_pct >= 15) - 3.0 * (beta1y >= 2.0)
    return final, grade, scores


# ───────────────────────────────────────────────
# 섹션 5: enrich_row
# ───────────────────────────────────────────────

def enrich_row(raw: dict, yf_data: dict, flow_data: dict,
               first_seen: dict) -> dict | None:
    """
    TradingView 원시 행 + yfinance + FnGuide + Naver 데이터를 통합하여
    ~80개 컬럼 dict 반환. 가격/시총 필터 미통과시 None.
    """
    ticker  = raw.get("_ticker", "")
    country = raw.get("_country", "")
    bare_code = _bare_kr_code(ticker)

    # 가격/시총 필터 (persistent 행은 면제)
    close  = _safe(raw.get("close"), 0.0) or 0.0
    mktcap = _safe(raw.get("market_cap_basic"), 0.0) or 0.0
    is_persistent = bool(raw.get("_persistent"))

    if not is_persistent:
        if country == "US":
            if close < MIN_PRICE_USD or mktcap < MIN_MKTCAP_USD:
                return None
        else:
            if close < MIN_PRICE_KRW or mktcap < MIN_MKTCAP_KRW:
                return None

    yf  = yf_data.get(ticker, {})
    flw = flow_data.get(bare_code, {})

    # 기업명 정제 (한국은 항상 한글명 우선)
    desc = raw.get("description") or raw.get("name") or ""
    if country == "KR":
        # _KNOWN_KR_NAMES → Naver 한글명 → TV description → 티커
        naver_nm = flw.get("naver_기업명", "")
        name = (_KNOWN_KR_NAMES.get(bare_code)
                or (naver_nm if naver_nm and naver_nm not in _INVALID_KR_NAMES else None)
                or (desc if desc and desc.lower() not in _INVALID_KR_NAMES else None)
                or bare_code)
    else:
        name = desc or ticker
    name = _truncate_name(name, 36)

    # 52주 위치
    hi52 = _safe(raw.get("price_52_week_high"), close) or close
    lo52 = _safe(raw.get("price_52_week_low"),  close) or close
    rng52 = hi52 - lo52
    pos52 = (close - lo52) / rng52 * 100 if rng52 > 0 else 100.0

    # 목표가 상승여력
    tgt_mean = (_safe(raw.get("price_target_average"))
                or _safe(yf.get("yf_price_target_mean")))
    tgt_upr  = (tgt_mean / close - 1) * 100 if tgt_mean and close > 0 else None

    # EPS 서프라이즈
    eps_act  = _safe(raw.get("earnings_per_share_fq"))
    eps_est  = _safe(raw.get("earnings_per_share_forecast_fq"))
    eps_sur  = (eps_act - eps_est) / abs(eps_est) * 100 if (
        eps_act and eps_est and eps_est != 0) else None

    # 매출 서프라이즈
    rev_sur = _safe(raw.get("revenue_surprise_percent_fq"))

    # 예상 성장률 (NextFY)
    rev_nfy_e = _safe(raw.get("revenue_forecast_next_fy"))
    rev_cur   = _safe(raw.get("total_revenue_ttm"))
    rev_nfy_g = (rev_nfy_e / rev_cur - 1) * 100 if (rev_nfy_e and rev_cur and rev_cur > 0) else None

    eps_nfy_e = _safe(raw.get("earnings_per_share_forecast_next_fy"))
    eps_ttm   = _safe(raw.get("earnings_per_share_diluted_ttm"))
    eps_nfy_g = (eps_nfy_e / abs(eps_ttm) - 1) * 100 if (
        eps_nfy_e and eps_ttm and eps_ttm != 0) else None

    # 순현금
    cash  = _safe(raw.get("cash_n_short_term_invest_fq"), 0.0) or 0.0
    debt  = _safe(raw.get("total_debt_fq"), 0.0) or 0.0
    net_cash = cash - debt
    net_cash_pct = net_cash / mktcap * 100 if mktcap > 0 else None

    # FCF 수익률
    fcf = _safe(raw.get("free_cash_flow_ttm"), 0.0) or 0.0
    fcf_yield = fcf / mktcap * 100 if mktcap > 0 else None

    # 섹터/산업
    sector   = raw.get("sector")   or ""
    industry = raw.get("industry") or ""

    # 미래산업 테마
    theme, theme_basis = _detect_future_theme({
        "_ticker": ticker, "기업명": name,
        "섹터": sector, "산업": industry,
    })

    # 투자의견 숫자 (TradingView: 1=Strong Buy … 5=Strong Sell)
    rec_mark = _safe(raw.get("recommendation_mark"))
    rec_all  = _safe(raw.get("Recommend.All"))
    inv_raw  = rec_mark or rec_all or 3.0

    out = {
        "수집일":           _NOW.strftime("%Y-%m-%d"),
        "국가":             country,
        "_ticker":          ticker,
        "티커":             bare_code if country == "KR" else ticker.split(":")[-1],
        "기업명":           name,
        "거래소":           raw.get("exchange") or "",
        "섹터":             sector,
        "산업":             industry,
        "미래산업테마":     theme,
        "미래산업근거":     theme_basis,

        # 가격
        "종가":             close,
        "당일고가":         _safe(raw.get("high")),
        "52주고가":         hi52,
        "52주저가":         lo52,
        "변동률%":          _safe(raw.get("change")),
        "시가총액":         mktcap,
        "52주고가대비위치%": round(pos52, 1),

        # 밸류
        "PER_TTM":          _safe(raw.get("price_earnings_ttm")),
        "Forward_PER":      _safe(raw.get("price_earnings_forward_fy")) or yf.get("yf_forwardPE"),
        "PEG_TTM":          _safe(raw.get("price_earnings_growth_ttm")) or yf.get("yf_pegRatio"),
        "P/S":              _safe(raw.get("price_sales_current")),
        "P/B":              _safe(raw.get("price_book_fq")),
        "EV/EBITDA":        _safe(raw.get("enterprise_value_ebitda_ttm")),

        # 성장
        "매출성장률_QoQ%":  _safe(raw.get("total_revenue_qoq_growth_fq")),
        "매출성장률_YoY%":  _safe(raw.get("total_revenue_yoy_growth_fq")),
        "순이익성장률_QoQ%":_safe(raw.get("net_income_qoq_growth_fq")),
        "순이익성장률_YoY%":_safe(raw.get("net_income_yoy_growth_fq")),
        "EPS성장률_QoQ%":   _safe(raw.get("earnings_per_share_diluted_qoq_growth_fq")),
        "EPS성장률_YoY%":   _safe(raw.get("earnings_per_share_diluted_yoy_growth_fq")),
        f"예상매출성장률_NextFY%": rev_nfy_g,
        f"예상EPS성장률_NextFY%":  eps_nfy_g,

        # 컨센서스
        "EPS_FQ_컨센서스":     _safe(raw.get("earnings_per_share_forecast_fq")),
        "EPS_NextFY_컨센서스": eps_nfy_e,
        "매출_FQ_컨센서스":    _safe(raw.get("revenue_forecast_fq")),
        "매출_NextFY_컨센서스":rev_nfy_e,
        "컨센서스출처":        "TradingView",

        # 목표가
        "목표가평균":         tgt_mean,
        "목표가상승여력%":    tgt_upr,
        "목표가최고":         _safe(raw.get("price_target_high")) or yf.get("yf_price_target_high"),
        "목표가최저":         _safe(raw.get("price_target_low"))  or yf.get("yf_price_target_low"),
        "다음실적일":         raw.get("earnings_release_next_date"),

        # 수익성
        "매출총이익률%":      _safe(raw.get("gross_margin_ttm")),
        "영업이익률%":        _safe(raw.get("operating_margin_ttm")),
        "순이익률%":          _safe(raw.get("net_margin_ttm")),
        "ROE%":               _safe(raw.get("return_on_equity_fy")),
        "ROA%":               _safe(raw.get("return_on_assets_fy")),

        # 재무
        "부채비율":           _safe(raw.get("debt_to_equity_fq")),
        "유동비율":           _safe(raw.get("current_ratio_fq")),
        "총부채":             debt,
        "현금및단기투자":     cash,
        "순현금":             net_cash,
        "순현금/시총%":       net_cash_pct,

        # 현금흐름
        "FCF_TTM":            fcf,
        "FCF마진%":           _safe(raw.get("free_cash_flow_margin_ttm")),
        "FCF수익률%":         fcf_yield,
        "ROIC%":              _safe(raw.get("return_on_invested_capital_fy")),
        "설비투자_TTM":       _safe(raw.get("capital_expenditures_ttm")),

        # 리스크/기타
        "Beta_1Y":            _safe(raw.get("beta_1_year")),
        "Beta_3Y":            _safe(raw.get("beta_3_year")),
        "배당수익률%":        (_safe(raw.get("dividends_yield_current"))
                             or yf.get("yf_dividendYield")),
        "RSI":                _safe(raw.get("RSI")),
        "상대거래량":         _safe(raw.get("relative_volume_10d_calc")),

        # 수익률
        "1주수익률%":   _safe(raw.get("Perf.W")),
        "1개월수익률%": _safe(raw.get("Perf.1M")),
        "3개월수익률%": _safe(raw.get("Perf.3M")),
        "6개월수익률%": _safe(raw.get("Perf.6M")),
        "1년수익률%":   _safe(raw.get("Perf.Y")),
        "YTD수익률%":   _safe(raw.get("Perf.YTD")),

        # 수급 (Naver/FnGuide — KR 전용; US는 None)
        "외국인_순매수_5일":   flw.get("외국인_순매수_5일"),
        "외국인_순매수_20일":  flw.get("외국인_순매수_20일"),
        "외국인_지분율%":      flw.get("외국인_지분율%"),
        "외국인_지분율_변화":  flw.get("외국인_지분율_변화"),
        "기관_순매수_5일":     flw.get("기관_순매수_5일"),
        "기관_순매수_20일":    flw.get("기관_순매수_20일"),

        # yfinance 수급
        "기관_보유%":    yf.get("yf_instPct"),
        "내부자_보유%":  yf.get("yf_insiderPct"),
        "공매도비율%":   yf.get("yf_shortPct"),
        "공매도_일수":   yf.get("yf_shortRatio"),

        # 서프라이즈
        "EPS_서프라이즈%":    eps_sur,
        "매출_서프라이즈%":   rev_sur,
        "자사주매입수익률%":  _safe(raw.get("buyback_yield")),

        # 리포트 (FnGuide)
        "컨센서스_증권사수":  raw.get("컨센서스_증권사수"),
        "최근리포트일":       raw.get("최근리포트일"),
        "최근리포트증권사":   raw.get("최근리포트증권사"),
        "최근리포트의견":     raw.get("최근리포트의견"),
        "최근리포트제목":     raw.get("최근리포트제목"),

        # 수출
        "수출섹터여부":   "",
        "수출섹터보너스": 0.0,
        "CAPEX성장여부": "",
        "해외확장근거":   "",

        # 투자의견 raw
        "투자의견점수_raw": inv_raw,

        # 사업개요
        "사업개요": yf.get("yf_bizSummary") or raw.get("fnguide_사업개요") or "",

        # 최근뉴스 (1-2일 내) — KR은 Naver, US는 yfinance
        "최근뉴스":         (flw.get("naver_최근뉴스") if country == "KR"
                              else yf.get("yf_recentNews")) or "",
        # 애널리스트 평가 변경 (최근 60일)
        "애널리스트_평가변경": yf.get("yf_recChanges") or "",
        # EPS 히스토리 (최근 4분기)
        "EPS_히스토리":      yf.get("yf_epsHistory") or "",
        # 애널리스트 컨센서스 추천도
        "추천도_평균":        yf.get("yf_recMean"),
        "추천도_라벨":        yf.get("yf_recKey") or "",
        "애널리스트_수":      yf.get("yf_recNumAnalysts"),

        # 최초수집일·추적일수·신고가여부
        "최초수집일": first_seen.get(ticker, _NOW.strftime("%Y-%m-%d")),
        "신고가여부": "Y" if (
            close > 0 and hi52 > 0 and abs(close - hi52) / hi52 < 0.005
        ) else "N",
        "추적상태": "신규" if is_persistent and ticker not in first_seen
                      else "추적중" if is_persistent
                      else "오늘신고가",
    }

    # 수출 점수 계산
    out["수출해외점수"] = export_overseas_score(out)
    out["수출섹터여부"] = "Y" if out["수출해외점수"] > 0 else ""
    out["수출섹터보너스"] = out["수출해외점수"]
    _exp_parts = []
    if out["수출섹터여부"] == "Y":
        _exp_parts.append(f"수출섹터({out.get('섹터','')})")
    if country == "KR" and _bare_kr_code(ticker) in KOREAN_EXPORTERS:
        _exp_parts.append("한국대표수출주")
    _rev_g = out.get("매출성장률_YoY%")
    if _rev_g and _rev_g > 10:
        _exp_parts.append(f"매출YoY+{_rev_g:.0f}%")
    if out.get("CAPEX성장여부") == "Y":
        _exp_parts.append("CAPEX확대")
    out["수출_해설"] = " / ".join(_exp_parts) if _exp_parts else ""

    # 수급 패턴
    flow_pat = detect_flow_pattern(out)
    out.update(flow_pat)

    # 선행매매 점수
    out["선행매매점수"] = leading_trade_score(out)

    # 투자우선점수 (13차원)
    priority, grade, dim_scores = compute_priority(out)
    out["투자우선점수"] = round(priority, 1)
    out["등급"] = grade
    out.update(dim_scores)
    out["데이터충분성%"] = _calc_completeness(out)

    # 텍스트 해석
    out["신고가_정량해석"]         = _make_high_interpretation(out)
    out["미래_컨센서스_긍정요인"]  = _make_consensus_positive(out)
    out["리스크_확인사항"]         = _make_risk_text(out)
    out["장기투자_체크리스트"]     = _make_longterm_text(out)
    out["수급_종합해석"]           = _make_flow_text(out)

    return out


def _calc_completeness(row: dict) -> float:
    key_fields = [
        "Forward_PER", "예상매출성장률_NextFY%", "영업이익률%",
        "FCF마진%", "외국인_순매수_5일", "기관_순매수_5일", "선행매매점수",
    ]
    filled = sum(1 for f in key_fields if row.get(f) is not None)
    return round(filled / len(key_fields) * 100, 1)


def _make_high_interpretation(row: dict) -> str:
    parts = []
    pos = row.get("52주고가대비위치%")
    if pos is not None:
        parts.append(f"52주 위치 {pos:.0f}%")
    rv = row.get("상대거래량")
    if rv:
        parts.append(f"상대거래량 {rv:.1f}x")
    mkt = row.get("시가총액")
    if mkt:
        parts.append(f"시총 {mkt/1e8:.0f}억" if row.get("나라") == "KR" else f"시총 ${mkt/1e9:.1f}B")
    return " | ".join(parts)


def _make_consensus_positive(row: dict) -> str:
    parts = []
    rev = row.get("예상매출성장률_NextFY%")
    if rev and rev > 5:
        parts.append(f"매출성장 {rev:.1f}%")
    eps = row.get("예상EPS성장률_NextFY%")
    if eps and eps > 5:
        parts.append(f"EPS성장 {eps:.1f}%")
    tgt = row.get("목표가상승여력%")
    if tgt and tgt > 10:
        parts.append(f"목표가상승여력 {tgt:.1f}%")
    return " | ".join(parts)


def _make_risk_text(row: dict) -> str:
    risks = []
    short = row.get("공매도비율%") or 0
    if short >= 10:
        risks.append(f"공매도 {short:.1f}%")
    de = row.get("부채비율") or 0
    if de > 3:
        risks.append(f"부채비율 {de:.2f}")
    beta = row.get("Beta_1Y") or 0
    if beta >= 2:
        risks.append(f"Beta {beta:.2f}")
    return " | ".join(risks)


def _make_longterm_text(row: dict) -> str:
    parts = []
    roic = row.get("ROIC%")
    if roic:
        parts.append(f"ROIC {roic:.1f}%")
    fcf = row.get("FCF마진%")
    if fcf and fcf > 5:
        parts.append(f"FCF마진 {fcf:.1f}%")
    div = row.get("배당수익률%")
    if div and div > 1:
        parts.append(f"배당 {div:.1f}%")
    return " | ".join(parts)


def _make_flow_text(row: dict) -> str:
    parts = []
    pat = row.get("수급패턴")
    if pat:
        parts.append(f"패턴: {pat}")
    f5 = row.get("외국인_순매수_5일")
    if f5 is not None:
        sign = "+" if f5 >= 0 else ""
        parts.append(f"외국인5일: {sign}{f5:.1f}억")
    i5 = row.get("기관_순매수_5일")
    if i5 is not None:
        sign = "+" if i5 >= 0 else ""
        parts.append(f"기관5일: {sign}{i5:.1f}억")
    acc = row.get("수급가속도")
    if acc:
        parts.append(f"가속도: {acc}")
    return " | ".join(parts)


# ───────────────────────────────────────────────
# 섹션 6: CSV 히스토리 관리
# ───────────────────────────────────────────────
import csv

def _read_csv_as_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def update_daily_history(rows: list[dict], csv_path: Path):
    today = _NOW.strftime("%Y-%m-%d")
    existing = _read_csv_as_list(csv_path)
    idx: dict[str, dict] = {}
    for r in existing:
        key = f"{r.get('수집일', '')}|{r.get('_ticker', '')}"
        idx[key] = r

    for r in rows:
        key = f"{today}|{r.get('_ticker', '')}"
        # 기업명 오염 정리
        bare = _bare_kr_code(r.get("_ticker", ""))
        nm = str(r.get("기업명", ""))
        if nm.lower() in _INVALID_KR_NAMES and bare in _KNOWN_KR_NAMES:
            r["기업명"] = _KNOWN_KR_NAMES[bare]
        idx[key] = r

    merged = sorted(idx.values(),
                    key=lambda x: (x.get("수집일", ""), x.get("_ticker", "")))
    _write_csv(csv_path, merged)


def build_flow_history_records(rows: list[dict]) -> list[dict]:
    today = _NOW.strftime("%Y-%m-%d")
    return [
        {
            "날짜": today,
            "_ticker": r.get("_ticker", ""),
            "기업명": r.get("기업명", ""),
            "외국인_순매수_5일":  r.get("외국인_순매수_5일"),
            "외국인_순매수_20일": r.get("외국인_순매수_20일"),
            "기관_순매수_5일":    r.get("기관_순매수_5일"),
            "기관_순매수_20일":   r.get("기관_순매수_20일"),
            "수급패턴":           r.get("수급패턴", ""),
            "투자우선점수":       r.get("투자우선점수"),
        }
        for r in rows
    ]


def save_flow_history(records: list[dict], csv_path: Path):
    existing = _read_csv_as_list(csv_path)
    today = _NOW.strftime("%Y-%m-%d")
    kept = [r for r in existing if r.get("날짜", "") != today]
    merged = kept + records
    _write_csv(csv_path, merged)


def load_first_seen(csv_path: Path) -> dict[str, str]:
    rows = _read_csv_as_list(csv_path)
    first: dict[str, str] = {}
    for r in rows:
        tk = r.get("_ticker", "")
        dt = r.get("수집일", "")
        if tk and dt:
            if tk not in first or dt < first[tk]:
                first[tk] = dt
    return first


def load_persistent_universe(csv_path: Path, lookback_days: int = 14) -> list[dict]:
    """기존 추적 종목 유니버스 로드 — 최근 N일 내 한 번이라도 수집된 종목 반환.

    각 종목당 최신 행(가장 최근 수집일) 1개씩 반환. _ticker, _country, 기업명 등 기본 필드 포함.
    오늘 TV 신고가에 없어도 기존 종목은 데이터 업데이트 대상으로 유지.
    """
    rows = _read_csv_as_list(csv_path)
    if not rows:
        return []
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    latest: dict[str, dict] = {}
    for r in rows:
        tk = r.get("_ticker", "")
        dt = r.get("수집일", "")
        if not tk or not dt:
            continue
        if dt < cutoff:
            continue
        if tk not in latest or dt > latest[tk].get("수집일", ""):
            latest[tk] = r
    out = []
    for tk, r in latest.items():
        country = r.get("국가", "")
        if country not in ("US", "KR"):
            country = "KR" if tk.startswith(("KRX:", "KOSDAQ:")) else "US"
        out.append({
            "_ticker": tk,
            "_country": country,
            "description": r.get("기업명", ""),
            "_persistent": True,
            "_first_seen": r.get("최초수집일") or r.get("수집일", ""),
        })
    return out


# ───────────────────────────────────────────────
# 섹션 7: HTML 생성
# ───────────────────────────────────────────────

_GRADE_COLORS = {
    "A": ("#1E6B00", "#FFFFFF"),
    "B": ("#70AD47", "#FFFFFF"),
    "C": ("#FFC000", "#000000"),
    "D": ("#FF6600", "#FFFFFF"),
    "F": ("#C00000", "#FFFFFF"),
}
_PAT_COLORS = {
    "저점매집":       ("#00B0F0", "#FFFFFF"),
    "기관외국인동반반전": ("#5B9BD5", "#FFFFFF"),
    "반전신호":       ("#7030A0", "#FFFFFF"),
    "고점추세":       ("#00B050", "#FFFFFF"),
    "고점청산":       ("#C00000", "#FFFFFF"),
}
_HDR_BG = {
    "base":    "#2F2F2F",
    "flow":    "#1F4E79",
    "lead":    "#7B0080",
    "export":  "#005F00",
    "growth":  "#1E6B00",
    "value":   "#7B3F00",
    "score":   "#4A0080",
    "risk":    "#8B0000",
    "quality": "#00416A",
    "theme":   "#005F73",
}
_COL_CATEGORY: dict[str, str] = {
    # 기본
    "수집일": "base", "국가": "base", "나라": "base", "티커": "base", "기업명": "base",
    "거래소": "base", "최초수집일": "base",
    # 가격
    "종가": "base", "당일고가": "base", "52주고가": "base", "52주저가": "base",
    "변동률%": "base", "시가총액": "base", "52주고가대비위치%": "base",
    # 수급
    "외국인_순매수_5일": "flow", "외국인_순매수_20일": "flow",
    "외국인_지분율%": "flow", "외국인_지분율_변화": "flow",
    "기관_순매수_5일": "flow", "기관_순매수_20일": "flow",
    "기관_보유%": "flow", "내부자_보유%": "flow",
    "수급패턴": "flow", "수급가속도": "flow",
    "저점매집여부": "flow", "고점청산여부": "flow",
    "수급반전일수": "flow", "수급_종합해석": "flow",
    # 13F 포지션 변화
    "포지션변화": "flow", "포트폴리오비중%": "score", "주식수_변화율%": "flow",
    "신규기관수": "flow", "증가기관수": "flow", "감소기관수": "risk",
    "컨센서스점수": "score", "신규기관": "flow", "증가기관": "flow",
    # 선행매매
    "선행매매점수": "lead",
    # 수출
    "수출섹터여부": "export", "수출섹터보너스": "export",
    "해외확장근거": "export", "수출해외점수": "export", "수출_해설": "export",
    "설비투자_TTM": "export",
    # 성장
    "매출성장률_QoQ%": "growth", "매출성장률_YoY%": "growth",
    "순이익성장률_QoQ%": "growth", "순이익성장률_YoY%": "growth",
    "EPS성장률_QoQ%": "growth", "EPS성장률_YoY%": "growth",
    "예상매출성장률_NextFY%": "growth", "예상EPS성장률_NextFY%": "growth",
    # 밸류
    "PER_TTM": "value", "Forward_PER": "value", "PEG_TTM": "value",
    "P/S": "value", "P/B": "value", "EV/EBITDA": "value",
    "목표가평균": "value", "목표가상승여력%": "value",
    "목표가최고": "value", "목표가최저": "value",
    "다음실적일": "value",
    # FnGuide 컨센서스 (한국)
    "컨센서스_증권사수": "score", "최근리포트의견": "flow",
    "최근리포트증권사": "base", "최근리포트일": "base", "최근리포트제목": "base",
    # 품질 추가
    "FCF_TTM": "quality", "순현금": "quality", "순현금/시총%": "quality",
    "FCF수익률%": "quality",
    "데이터충분성%": "score",
    # 점수
    "투자우선점수": "score", "등급": "score",
    "밸류점수": "score", "성장점수": "score", "품질점수": "score",
    "현금흐름점수": "score", "외국인수급점수": "score", "기관수급점수": "score",
    "미래산업점수": "score", "수출해외점수": "score", "장기투자점수": "score",
    "모멘텀점수": "score", "투자의견점수": "score", "거래량점수": "score",
    # 리스크
    "공매도비율%": "risk", "공매도_일수": "risk", "Beta_1Y": "risk", "Beta_3Y": "risk",
    "리스크페널티": "risk",
    # 품질
    "매출총이익률%": "quality", "영업이익률%": "quality",
    "순이익률%": "quality", "ROE%": "quality", "ROA%": "quality",
    "ROIC%": "quality", "FCF마진%": "quality", "FCF수익률%": "quality",
    "부채비율": "quality", "유동비율": "quality",
}
_FLOW_COLS   = {"외국인_순매수_5일","외국인_순매수_20일","기관_순매수_5일","기관_순매수_20일"}
_GROWTH_COLS = {
    "매출성장률_QoQ%","매출성장률_YoY%","순이익성장률_QoQ%","순이익성장률_YoY%",
    "EPS성장률_QoQ%","EPS성장률_YoY%","예상매출성장률_NextFY%","예상EPS성장률_NextFY%",
    "FCF수익률%","ROE%","ROA%","ROIC%",
}
_WRAP_COLS   = {"수급_종합해석","신고가_정량해석","미래_컨센서스_긍정요인",
                "리스크_확인사항","장기투자_체크리스트","사업개요","미래산업근거","해외확장근거",
                "수출_해설","최근리포트제목","최근뉴스",
                "애널리스트_평가변경","EPS_히스토리",
                "신규기관","증가기관","기관목록"}
_SCORE_COLS  = {
    "투자우선점수","밸류점수","성장점수","품질점수","현금흐름점수",
    "외국인수급점수","기관수급점수","선행매매점수","미래산업점수",
    "수출해외점수","장기투자점수","모멘텀점수","투자의견점수","거래량점수",
}


def _esc(s: object) -> str:
    """HTML 이스케이프 — 이미 이스케이프된 엔티티를 먼저 풀고 재처리해 이중 이스케이프 방지."""
    return _html.escape(_html.unescape(str(s)), quote=True)


def _truncate_name(name: str, max_len: int = 36) -> str:
    """긴 기업명 정제 — 후미 부가설명 제거, 길면 말줄임.

    예: 'NextEra Energy, Inc. Corporate Unit Const of 1 Deb...' →
         'NextEra Energy, Inc.'
    """
    if not name or not isinstance(name, str):
        return name or ""
    s = _html.unescape(name).strip()
    # 후미 부가설명을 잘라낼 키워드 (수식어/사채/우선주 등)
    _CUT = (" Corporate Unit", " Corp Unit", " Const of",
            " Composed of", " Cons of", " Deb 1", " Debenture",
            " Preferred Stock", " Pref Stk", " Series ",
            " Notes due", " Warrant", " Right ", " - Class ")
    for kw in _CUT:
        idx = s.find(kw)
        if idx > 0:
            s = s[:idx].rstrip(" ,;")
            break
    # 마지막 ", Inc." 직후로 컷
    for marker in (", Inc.", ", Ltd.", ", LLC", " Inc.", " Ltd.", " Corp."):
        idx = s.find(marker)
        if 0 < idx < max_len:
            cut_at = idx + len(marker)
            if cut_at + 5 < len(s):
                s = s[:cut_at]
                break
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0]
    return (cut or s[:max_len]).rstrip(" ,;") + "…"


def _fmt_val(v: object) -> str:
    """셀 값 포맷. _esc는 _make_table_html에서 한 번만 처리."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        if v == int(v):
            return f"{int(v):,}"
        return f"{v:.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


_BIG_NUM_COLS = frozenset({
    "시가총액", "FCF_TTM", "설비투자_TTM", "순현금",
    "보유가치_USD", "총보유가치_USD",
})


def _fmt_big(v: object) -> str:
    """큰 숫자를 K/M/B/T 단위로 축약"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    try:
        f = float(v)
        a = abs(f)
        sign = "-" if f < 0 else ""
        if a >= 1e12: return f"{sign}{a/1e12:.1f}T"
        if a >= 1e9:  return f"{sign}{a/1e9:.1f}B"
        if a >= 1e6:  return f"{sign}{a/1e6:.1f}M"
        if a >= 1e3:  return f"{sign}{a/1e3:.0f}K"
        return f"{f:.0f}"
    except Exception:
        return str(v)


def _date_short(v: object) -> str:
    """YYYY-MM-DD → M/D 형식으로 변환"""
    s = str(v or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{int(m.group(2))}/{int(m.group(3))}"
    return s


def _cell_style(col: str, val: object, odd: bool) -> tuple[str, str]:
    # CSS 변수 → 다크모드 자동 전환 (no !important conflict)
    base = "background:var(--cell-odd);" if odd else "background:var(--cell-even);"
    R = "text-align:right;"
    C = "text-align:center;"
    S = "font-size:9px;"
    B = "font-weight:700;"

    # ── 수집일 / 날짜 컬럼 M/D 형식 ────────────────
    if col in ("수집일", "최초수집일", "보고일", "다음실적일"):
        return base + S + "color:#64748B;text-align:center;", _date_short(val)

    # ── 큰 숫자 K/M/B 단위 ────────────────────────
    if col in _BIG_NUM_COLS:
        return base + R + S, _fmt_big(val)

    def _fv():
        try:
            return float(val)
        except Exception:
            return None

    def _c(bg, fg, bold=True, align="right", fs="9px"):
        b = "font-weight:700;" if bold else ""
        return (f"background:{bg};color:{fg};{b}text-align:{align};font-size:{fs};",
                _fmt_val(val))

    # ── 등급 pill ──────────────────────────────────
    if col == "등급":
        g = str(val or "").strip()
        gmap = {"A": ("#155724","#D4EDDA"), "B": ("#1E6B00","#C3E6CB"),
                "C": ("#856404","#FFF3CD"), "D": ("#7B3300","#FFE0C0"),
                "F": ("#7B0000","#FFD0D0")}
        if g in gmap:
            fg, bg = gmap[g]
            return (f"background:{bg};color:{fg};font-weight:900;text-align:center;"
                    f"font-size:10px;letter-spacing:0.5px;"), g
        return base + C + S, str(val or "")

    # ── 수급패턴 pill ───────────────────────────────
    if col == "수급패턴":
        pat = str(val or "")
        pmap = {
            "저점매집":          ("#ffffff","#0069B4"),
            "기관외국인동반반전": ("#ffffff","#5B2D8E"),
            "반전신호":          ("#ffffff","#6A0DAD"),
            "고점추세":          ("#ffffff","#1B6B1B"),
            "고점청산":          ("#ffffff","#A30000"),
        }
        for key, (fg, bg) in pmap.items():
            if key in pat:
                return (f"background:{bg};color:{fg};font-weight:700;text-align:center;"
                        f"font-size:8.5px;white-space:nowrap;border-radius:3px;"), key
        return base + C + S, pat

    # ── 투자우선점수 — 강한 heatmap ────────────────
    if col == "투자우선점수":
        fv = _fv()
        if fv is not None:
            if fv >= 75: return _c("#155724","#D4F5DC", fs="11px")
            if fv >= 60: return _c("#1E6B00","#E8F5E9", fs="11px")
            if fv >= 45: return _c("#856404","#FFF9E6", fs="11px")
            if fv >= 30: return _c("#7B3300","#FFF0E0", fs="11px")
            return _c("#7B0000","#FFE8E8", fs="11px")
        return base + B + R + "font-size:11px;", _fmt_val(val)

    # ── 점수 컬럼 heatmap ───────────────────────────
    if col in _SCORE_COLS:
        fv = _fv()
        if fv is not None:
            if fv >= 70: return base + B + R + S + "color:#155724;background:#D4EDDA;", _fmt_val(val)
            if fv >= 50: return base + B + R + S + "color:#1E6B00;", _fmt_val(val)
            if fv >= 30: return base + B + R + S + "color:#856404;", _fmt_val(val)
            return base + B + R + S + "color:#7B0000;background:#FFF0F0;", _fmt_val(val)
        return base + B + R + S, _fmt_val(val)

    # ── 티커 / 기업명 ────────────────────────────────
    if col == "티커":
        return base + B + S + "color:#0D47A1;letter-spacing:0.3px;", _fmt_val(val)
    if col == "기업명":
        return base + B + S, _fmt_val(val)

    # ── 섹터/산업 ────────────────────────────────────
    if col in ("섹터", "산업"):
        return base + S + "color:var(--t2);font-style:italic;", _fmt_val(val)
    if col == "미래산업테마":
        return base + B + S + "color:var(--ac);", _fmt_val(val)

    # ── 수급 flow 컬럼 — bg 포함 ────────────────────
    if col in _FLOW_COLS:
        fv = _fv()
        if fv is not None:
            if fv > 0:
                return f"background:#EBF5FF;{B}color:#003399;{R}{S}", _fmt_val(val)
            if fv < 0:
                return f"background:#FFF0F0;{B}color:#CC0000;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 성장률 컬럼 — bg 포함 ────────────────────────
    if col in _GROWTH_COLS:
        fv = _fv()
        if fv is not None:
            if fv >= 30:  return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv >= 10:  return f"background:#EAF7EA;{B}color:#1E6B00;{R}{S}", _fmt_val(val)
            if fv >= 0:   return base + "color:#2E7D32;" + R + S, _fmt_val(val)
            if fv >= -10: return base + B + "color:#CC0000;" + R + S, _fmt_val(val)
            return f"background:#FFF0F0;{B}color:#AA0000;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 변동률% ──────────────────────────────────────
    if col == "변동률%":
        fv = _fv()
        if fv is not None:
            if fv >= 5:   return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv > 0:    return base + B + "color:#1E6B00;" + R + S, _fmt_val(val)
            if fv <= -5:  return f"background:#FFF0F0;{B}color:#AA0000;{R}{S}", _fmt_val(val)
            return base + B + "color:#CC0000;" + R + S, _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 52주 고가 대비 위치% ─────────────────────────
    if col == "52주고가대비위치%":
        fv = _fv()
        if fv is not None:
            if fv >= 95:  return f"background:#FFD700;{B}color:#3D2800;{R}{S}", _fmt_val(val)
            if fv >= 80:  return f"background:#FFF9C4;{B}color:#6D4C00;{R}{S}", _fmt_val(val)
            if fv <= 30:  return f"background:#E8F5E9;{B}color:#1E6B00;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── RSI ──────────────────────────────────────────
    if col == "RSI":
        fv = _fv()
        if fv is not None:
            if fv >= 70: return f"background:#FFE0E0;{B}color:#AA0000;{R}{S}", _fmt_val(val)
            if fv <= 30: return f"background:#E0F5E0;{B}color:#1E6B00;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 공매도비율% ──────────────────────────────────
    if col == "공매도비율%":
        fv = _fv()
        if fv is not None:
            if fv >= 20: return f"background:#FFD0D0;{B}color:#7B0000;{R}{S}", _fmt_val(val)
            if fv >= 10: return f"background:#FFE8CC;{B}color:#7B3300;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 목표가상승여력% ──────────────────────────────
    if col == "목표가상승여력%":
        fv = _fv()
        if fv is not None:
            if fv >= 30: return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv >= 10: return base + B + "color:#1E6B00;" + R + S, _fmt_val(val)
            if fv < 0:   return f"background:#FFF0F0;{B}color:#AA0000;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 주식수_변화율% (13F 분기 변화) ──────────────
    if col == "주식수_변화율%":
        fv = _fv()
        if fv is not None:
            if fv >= 100: return f"background:#D4EDDA;{B}color:#0069B4;{R}{S}", f"+{_fmt_val(val)}%"
            if fv > 0:    return base + B + "color:#1B6B1B;" + R + S, f"+{_fmt_val(val)}%"
            if fv < 0:    return base + B + "color:#A30000;" + R + S, f"{_fmt_val(val)}%"
        return base + R + S, _fmt_val(val)

    # ── EPS/매출 서프라이즈 ──────────────────────────
    if col in ("EPS_서프라이즈%", "매출_서프라이즈%"):
        fv = _fv()
        if fv is not None:
            if fv > 5:  return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv > 0:  return base + B + "color:#1E6B00;" + R + S, _fmt_val(val)
            return base + B + "color:#CC0000;" + R + S, _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 상대거래량 ───────────────────────────────────
    if col == "상대거래량":
        fv = _fv()
        if fv is not None:
            if fv >= 5: return f"background:#E3F2FD;{B}color:#0D47A1;{R}{S}", _fmt_val(val)
            if fv >= 2: return base + B + "color:#1565C0;" + R + S, _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 수익률% ──────────────────────────────────────
    if col in {"1주수익률%","1개월수익률%","3개월수익률%",
               "6개월수익률%","1년수익률%","YTD수익률%"}:
        fv = _fv()
        if fv is not None:
            if fv >= 20: return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv > 0:   return base + "color:#1E6B00;" + R + S, _fmt_val(val)
            return base + "color:#CC0000;" + R + S, _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 품질 지표 (수익성) ───────────────────────────
    if col in {"영업이익률%","ROE%","ROA%","ROIC%",
               "FCF마진%","FCF수익률%","매출총이익률%","순이익률%"}:
        fv = _fv()
        if fv is not None:
            if fv >= 25: return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv >= 10: return base + "color:#1E6B00;" + R + S, _fmt_val(val)
            if fv < 0:   return base + "color:#CC0000;" + R + S, _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── PER / Forward_PER ────────────────────────────
    if col in ("Forward_PER", "PER_TTM"):
        fv = _fv()
        if fv is not None and fv > 0:
            if fv <= 15: return f"background:#D4EDDA;{B}color:#155724;{R}{S}", _fmt_val(val)
            if fv >= 50: return f"background:#FFF0F0;color:#AA0000;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 수출섹터여부 ──────────────────────────────────
    if col == "수출섹터여부":
        if str(val or "") == "Y":
            return f"background:#E8F5E9;{B}color:#1E6B00;{C}{S}", "Y"
        return base + C + S, _fmt_val(val)

    # ── 전일비% (시장지표) ───────────────────────────
    if col == "전일비%":
        fv = _fv()
        if fv is not None:
            disp = f"+{_fmt_val(val)}" if fv > 0 else _fmt_val(val)
            if fv > 0: return base + B + "color:#1E6B00;" + R + S, disp
            if fv < 0: return base + B + "color:#CC0000;" + R + S, disp
        return base + R + S, _fmt_val(val)

    # ── 포지션변화 (13F 분기 비교) ──────────────────
    if col == "포지션변화":
        pat = str(val or "")
        pmap = {
            "신규": ("#fff", "#0069B4"),
            "증가": ("#fff", "#1B6B1B"),
            "감소": ("#fff", "#A30000"),
            "유지": ("#555", "#E8E8E8"),
        }
        fg2, bg2 = pmap.get(pat, ("#555", "#F0F0F0"))
        return (f"background:{bg2};color:{fg2};font-weight:700;text-align:center;"
                f"font-size:8.5px;border-radius:3px;"), pat

    # ── 포트폴리오비중% ──────────────────────────────
    if col == "포트폴리오비중%":
        fv = _fv()
        if fv is not None:
            if fv >= 10: return f"background:#0069B4;color:#fff;{B}{R}{S}", _fmt_val(val)
            if fv >= 5:  return f"background:#1565C0;color:#fff;{R}{S}", _fmt_val(val)
            if fv >= 2:  return base + B + "color:#1565C0;" + R + S, _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 컨센서스점수 (기관중복보유) ─────────────────
    if col == "컨센서스점수":
        fv = _fv()
        if fv is not None:
            if fv >= 9:  return f"background:#0069B4;color:#fff;{B}{R}{S}", _fmt_val(val)
            if fv >= 6:  return f"background:#1B6B1B;color:#fff;{B}{R}{S}", _fmt_val(val)
            if fv >= 3:  return base + B + "color:#1E6B00;" + R + S, _fmt_val(val)
            if fv <= 0:  return base + "color:#888;" + R + S, _fmt_val(val)
        return base + B + R + S, _fmt_val(val)

    # ── 저점매집여부 / 고점청산여부 ──────────────────
    if col == "저점매집여부":
        if str(val or "") == "Y":
            return f"background:#E3F2FD;{B}color:#003399;{C}{S}", "Y"
        return base + C + S, _fmt_val(val)
    if col == "고점청산여부":
        if str(val or "") == "Y":
            return f"background:#FFF0F0;{B}color:#AA0000;{C}{S}", "Y"
        return base + C + S, _fmt_val(val)

    # ── 부채비율 ─────────────────────────────────────
    if col == "부채비율":
        fv = _fv()
        if fv is not None:
            if fv >= 200: return f"background:#FFF0F0;{B}color:#AA0000;{R}{S}", _fmt_val(val)
            if fv <= 50:  return f"background:#E8F5E9;color:#1E6B00;{R}{S}", _fmt_val(val)
        return base + R + S, _fmt_val(val)

    # ── 텍스트 wrap 컬럼 ────────────────────────────
    if col in _WRAP_COLS:
        return base + "white-space:normal;word-break:break-all;font-size:9px;max-width:200px;", _fmt_val(val)

    # ── 나머지 숫자 오른쪽 정렬 ─────────────────────
    try:
        float(val)
        return base + R + S, _fmt_val(val)
    except Exception:
        pass

    return base + S, _fmt_val(val)


def _has_data(v) -> bool:
    """셀에 실질 데이터가 있는지 판단."""
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    if v == "" or v == "-":
        return False
    return True


def _filter_empty_headers(rows: list[dict], headers: list[str]) -> list[str]:
    """모든 행에서 값이 없는(None/"") 컬럼을 헤더 목록에서 제거."""
    if not rows:
        return headers
    return [h for h in headers if any(_has_data(r.get(h)) for r in rows)]


def _make_table_html(rows: list[dict], headers: list[str],
                     freeze_col: int = 3, title: str = "") -> str:
    if not rows:
        return f'<div class="empty-msg">데이터 없음</div>'

    # 데이터 없는 컬럼 자동 제거
    headers = _filter_empty_headers(rows, headers)

    # 헤더 행
    hdr_cells = []
    for h in headers:
        cat = _COL_CATEGORY.get(h, "base")
        bg  = _HDR_BG.get(cat, "#2F2F2F")
        hdr_cells.append(
            f'<th style="background:{bg};color:#fff;font-weight:700;'
            f'font-size:9px;white-space:nowrap;padding:5px 7px;'
            f'border-bottom:2px solid rgba(255,255,255,0.2);border-right:1px solid rgba(255,255,255,0.15);'
            f'text-align:center;vertical-align:middle;'
            f'position:sticky;top:0;z-index:2;letter-spacing:0.2px;">{_esc(h)}</th>'
        )

    # 데이터 행
    body_rows = []
    for i, row in enumerate(rows):
        odd = (i % 2 == 1)
        cells = []
        for h in headers:
            val = row.get(h, "")
            style, disp = _cell_style(h, val, odd)
            cells.append(f'<td style="{style}padding:4px 6px;border-bottom:1px solid #E2E8F0;border-right:1px solid #E2E8F0;">{_esc(disp)}</td>')
        body_rows.append(f'<tr class="drow">{"".join(cells)}</tr>')

    return f'''
<div class="tbl-wrap">
<table class="dtbl">
<thead><tr>{"".join(hdr_cells)}</tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
</div>'''


# ── 탭별 헤더 정의 (탭마다 고유 컬럼, 중복 최소화) ──────────────

# 신고가_미국: 미국 전용 상세 — 수집된 모든 미국 정량 데이터
US_DETAIL_HEADERS = [
    "수집일","티커","기업명","거래소","섹터","산업","미래산업테마",
    "종가","52주고가대비위치%","변동률%","시가총액","데이터충분성%",
    "투자우선점수","등급",
    # 밸류에이션 전체
    "PER_TTM","Forward_PER","PEG_TTM","P/S","P/B","EV/EBITDA",
    # 성장 — 분기·연간·예상 전체
    "매출성장률_QoQ%","매출성장률_YoY%","EPS성장률_QoQ%","EPS성장률_YoY%",
    "예상매출성장률_NextFY%","예상EPS성장률_NextFY%",
    # 수익성 전체
    "매출총이익률%","영업이익률%","순이익률%","ROE%","ROA%","ROIC%",
    # 현금흐름
    "FCF_TTM","FCF마진%","FCF수익률%","설비투자_TTM",
    # 재무건전성
    "부채비율","유동비율","순현금/시총%","Beta_1Y","Beta_3Y",
    # 주주 구성
    "외국인_지분율%","기관_보유%","내부자_보유%",
    # 수급·신호
    "수급패턴","선행매매점수","수출섹터여부",
    # 기술적
    "RSI","1주수익률%","1개월수익률%","3개월수익률%","6개월수익률%","1년수익률%",
    # 목표가·실적일
    "목표가평균","목표가최고","목표가최저","목표가상승여력%","다음실적일",
    # 서프라이즈·공매도
    "EPS_서프라이즈%","매출_서프라이즈%","공매도비율%","공매도_일수",
    # 배당·자사주
    "배당수익률%","자사주매입수익률%",
    # 애널리스트 평가 (yfinance upgrades/downgrades)
    "추천도_평균","추천도_라벨","애널리스트_수","애널리스트_평가변경","EPS_히스토리",
    # 텍스트 분석 (수집·생성 데이터 전부)
    "신고가_정량해석","미래_컨센서스_긍정요인","리스크_확인사항","수급_종합해석","최근뉴스","사업개요",
]

# 신고가_한국: 한국 전용 상세 — FnGuide·Naver 고유 데이터 포함
KR_DETAIL_HEADERS = [
    "수집일","티커","기업명","거래소","섹터","산업","미래산업테마",
    "종가","52주고가대비위치%","변동률%","시가총액","데이터충분성%",
    "투자우선점수","등급",
    # 밸류에이션
    "PER_TTM","Forward_PER","P/B","EV/EBITDA",
    # 성장 — QoQ·YoY·예상
    "매출성장률_QoQ%","매출성장률_YoY%","EPS성장률_QoQ%","EPS성장률_YoY%",
    "예상매출성장률_NextFY%","예상EPS성장률_NextFY%",
    # 수익성·현금흐름
    "영업이익률%","순이익률%","ROE%","ROA%","ROIC%","FCF마진%",
    # 재무건전성
    "부채비율","유동비율","순현금/시총%","FCF_TTM","Beta_1Y",
    # 한국 전용 수급 (Naver Finance)
    "외국인_순매수_5일","외국인_순매수_20일","외국인_지분율%","외국인_지분율_변화",
    "기관_순매수_5일","기관_순매수_20일","기관_보유%",
    # 수급·신호
    "수급패턴","선행매매점수","수출섹터여부","수출해외점수","수출_해설",
    # 기술적
    "RSI","1주수익률%","1개월수익률%","3개월수익률%","YTD수익률%",
    # FnGuide 컨센서스 (한국 전용)
    "컨센서스_증권사수","최근리포트의견","최근리포트증권사","최근리포트일","최근리포트제목",
    "목표가평균","목표가최고","목표가최저","목표가상승여력%",
    # 애널리스트 평가 (해외 ADR/공통)
    "추천도_평균","추천도_라벨","애널리스트_수","애널리스트_평가변경","EPS_히스토리",
    # 텍스트 분석
    "신고가_정량해석","미래_컨센서스_긍정요인","리스크_확인사항","수급_종합해석","최근뉴스","사업개요",
]

DETAIL_HEADERS = US_DETAIL_HEADERS  # 하위 호환

# 우선순위_TOP: 13차원 점수 스코어카드 — 다른 탭에 없는 점수 컬럼 집중
TOP_HEADERS = [
    "국가","티커","기업명","섹터","미래산업테마",
    "투자우선점수","등급",
    # 13차원 점수 전부 (이 탭에만)
    "밸류점수","성장점수","품질점수","현금흐름점수",
    "외국인수급점수","기관수급점수","선행매매점수",
    "미래산업점수","수출해외점수","장기투자점수","모멘텀점수","투자의견점수","거래량점수",
    # 점수 근거 핵심 지표
    "52주고가대비위치%","Forward_PER","PEG_TTM",
    "예상매출성장률_NextFY%","예상EPS성장률_NextFY%","영업이익률%",
    # 리스크·타겟
    "목표가상승여력%","공매도비율%","RSI","EPS_서프라이즈%",
    # 긍정요인 요약 (1줄)
    "미래_컨센서스_긍정요인",
]

# 선행매매_시그널: 수급 패턴·타이밍 신호 — 패턴 분류 + 반전 신호 집중
LEAD_HEADERS = [
    "국가","티커","기업명",
    "수급패턴","수급가속도","저점매집여부","고점청산여부",
    "선행매매점수","외국인수급점수","기관수급점수",
    "외국인_순매수_5일","외국인_순매수_20일","기관_순매수_5일","기관_순매수_20일",
    "외국인_지분율%","외국인_지분율_변화","기관_보유%","내부자_보유%",
    "공매도비율%","공매도_일수",
    "종가","변동률%","최초수집일",
    "수급_종합해석",
]

# 외국인_수급: 플로우 규모 + 소유 구조 + 공매도 — 수급량 중심 탭
FLOW_HEADERS = [
    "국가","티커","기업명","섹터",
    "외국인_순매수_5일","외국인_순매수_20일","외국인_지분율%","외국인_지분율_변화",
    "기관_순매수_5일","기관_순매수_20일","기관_보유%","내부자_보유%",
    "수급패턴","수급가속도","저점매집여부","고점청산여부",
    "공매도비율%","공매도_일수",
    "투자우선점수","등급","종가","변동률%","52주고가대비위치%",
]

# 수출해외_상위: 수출·글로벌 확장 고유 지표 — 수급 컬럼 없음
EXPORT_HEADERS = [
    "국가","티커","기업명","섹터","산업",
    "수출해외점수","수출섹터여부","수출섹터보너스","수출_해설",
    "매출성장률_QoQ%","매출성장률_YoY%","예상매출성장률_NextFY%",
    "순이익성장률_YoY%","EPS성장률_YoY%",
    "영업이익률%","ROIC%","FCF마진%","P/S",
    "설비투자_TTM",
    "투자우선점수","등급","52주고가대비위치%",
]

# 거래량급증_미국: 거래량+모멘텀+TV 펀더멘털 — 수급 플로우 없음
US_VOLUME_HEADERS = [
    "티커","기업명","섹터","미래산업테마",
    "상대거래량","변동률%","종가","시가총액","RSI",
    "52주고가대비위치%",
    "1주수익률%","1개월수익률%","3개월수익률%",
    "Forward_PER","영업이익률%","부채비율","FCF마진%",
    "매출성장률_YoY%","EPS성장률_YoY%",
    "투자우선점수","등급",
]

# 거래량급증_한국: 거래량+Naver 수급 플로우 — 미국과 차별화
KR_VOLUME_HEADERS = [
    "티커","기업명","섹터","미래산업테마",
    "상대거래량","변동률%","종가","시가총액","RSI",
    "52주고가대비위치%",
    "외국인_순매수_5일","기관_순매수_5일","외국인_지분율%",
    "외국인_순매수_20일","기관_순매수_20일",
    "1주수익률%","1개월수익률%","3개월수익률%",
    "매출성장률_YoY%","EPS성장률_YoY%",
    "투자우선점수","등급",
]

# 하위 호환 (generate_html 외부 참조 방지)
VOLUME_HEADERS = US_VOLUME_HEADERS

# 장기투자_후보: 품질·해자·현금흐름 — 수급 플로우 없음, 소유구조·배당 집중
LONG_TERM_HEADERS = [
    "국가","티커","기업명","섹터","미래산업테마",
    "투자우선점수","등급","장기투자점수",
    # FCF·수익성·해자
    "FCF_TTM","FCF마진%","FCF수익률%","ROIC%","ROE%","ROA%",
    "매출총이익률%","영업이익률%",
    # 밸류에이션 (품질주 관점)
    "EV/EBITDA","P/B",
    # 재무건전성
    "부채비율","유동비율","순현금","순현금/시총%",
    # 주주환원
    "배당수익률%","자사주매입수익률%","내부자_보유%",
    # 리스크
    "Beta_1Y","Beta_3Y",
    # 컨텍스트
    "최초수집일",
    # 텍스트
    "장기투자_체크리스트","미래_컨센서스_긍정요인",
]

# 테마_요약: 테마 집계
THEME_HEADERS = [
    "미래산업테마","종목수","평균_투자우선점수",
    "A등급수","B등급수","C이하등급수","수급신호_종목수",
    "평균_선행매매점수","평균_성장점수","평균_미래산업점수","평균_수출해외점수",
    "평균_RSI","평균_52주위치%","평균_1개월수익률%",
    "대표종목","최고점수종목","최고점수",
]

# 유명기관_13F (CUSIP은 내부용, 표시하지 않음)
SEC_HEADERS = [
    "기관명","보고일","종목명","티커",
    "포지션변화","포트폴리오비중%","주식수_변화율%","보유가치_USD","주식수","전분기_주식수","주식종류",
]

# 일별_트래킹
TRACKING_HEADERS = [
    "수집일","국가","티커","기업명","등급","투자우선점수",
    "종가","변동률%","52주고가대비위치%","상대거래량",
    "외국인_순매수_5일","기관_순매수_5일","수급패턴","선행매매점수",
    "Forward_PER","예상매출성장률_NextFY%","미래산업테마","최초수집일",
]

# 시장지표
MARKET_HEADERS = ["지표","심볼","현재가","전일비%","52주위치%","52주고가","52주저가"]
INSIDER_HEADERS = ["신고일","티커","회사명","임원명","직책","거래유형","가격","수량","거래금액","보유주식"]


# ── 대시보드 HTML ─────────────────────────────

def _make_dashboard_html(enriched: list[dict], collected_at: str) -> str:
    n_total = len(enriched)
    grade_cnt = Counter(r.get("등급", "F") for r in enriched)
    country_cnt = Counter(r.get("국가", "?") for r in enriched)

    def _grade_badge(g: str, cnt: int) -> str:
        gmap = {"A": ("#155724","#D4EDDA"), "B": ("#1E6B00","#C3E6CB"),
                "C": ("#856404","#FFF3CD"), "D": ("#7B3300","#FFE0C0"), "F": ("#7B0000","#FFD0D0")}
        fg, bg = gmap.get(g, ("#555","#EEE"))
        return (f'<span style="display:inline-block;background:{bg};color:{fg};'
                f'font-weight:900;border-radius:999px;padding:3px 12px;margin:2px;'
                f'font-size:0.8rem;letter-spacing:0.5px;">'
                f'{g}: {cnt}</span>')

    grades_html = "".join(_grade_badge(g, grade_cnt.get(g, 0)) for g in ["A","B","C","D","F"])
    country_html = "".join(
        f'<span style="background:#333;color:#eee;border-radius:6px;'
        f'padding:2px 8px;margin:2px;font-size:0.8rem;">{c}: {n}</span>'
        for c, n in country_cnt.most_common()
    )

    def _top5_section(title: str, icon: str, sorted_rows: list[dict], score_field: str) -> str:
        top5 = sorted_rows[:5]
        rows_html = ""
        gmap = {"A": ("#155724","#D4EDDA"), "B": ("#1E6B00","#C3E6CB"),
                "C": ("#856404","#FFF3CD"), "D": ("#7B3300","#FFE0C0"), "F": ("#7B0000","#FFD0D0")}
        for i, r in enumerate(top5):
            grade = r.get("등급", "F")
            gfg, gbg = gmap.get(grade, ("#555","#EEE"))
            sc = r.get(score_field) or r.get("투자우선점수") or 0
            row_bg = "#FAFBFF" if i % 2 == 0 else "#FFFFFF"
            rows_html += (
                f'<tr style="background:{row_bg};">'
                f'<td style="padding:5px 8px;font-weight:700;font-size:0.82rem;">'
                f'<span style="color:#888;font-size:0.75rem;margin-right:5px;">#{i+1}</span>'
                f'{_esc(r.get("기업명",""))}</td>'
                f'<td style="padding:5px 6px;font-size:0.78rem;color:#0D47A1;font-weight:700;">{_esc(r.get("티커",""))}</td>'
                f'<td style="padding:5px 6px;text-align:center;">'
                f'<span style="background:{gbg};color:{gfg};font-weight:900;border-radius:999px;'
                f'padding:1px 9px;font-size:0.78rem;">{grade}</span>'
                f'</td>'
                f'<td style="padding:5px 8px;text-align:right;font-weight:900;font-size:0.88rem;color:#1F3864;">{sc:.1f}</td>'
                f'</tr>'
            )
        return f'''
<div class="dash-card">
  <div class="dash-card-title">{icon} {_esc(title)}</div>
  <table style="width:100%;border-collapse:collapse;">
    <thead>
      <tr style="background:#1F2937;color:#fff;">
        <th style="padding:5px 8px;text-align:left;font-size:0.78rem;">기업명</th>
        <th style="padding:5px 6px;font-size:0.78rem;">티커</th>
        <th style="padding:5px 6px;font-size:0.78rem;">등급</th>
        <th style="padding:5px 8px;font-size:0.78rem;">점수</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>'''

    by_priority = sorted(enriched, key=lambda x: x.get("투자우선점수", 0) or 0, reverse=True)
    by_lead     = sorted(enriched, key=lambda x: x.get("선행매매점수", 0) or 0, reverse=True)
    by_export   = sorted([r for r in enriched if r.get("수출섹터여부") == "Y"],
                         key=lambda x: x.get("투자우선점수", 0) or 0, reverse=True)
    if len(by_export) < 5:
        by_export = sorted(enriched, key=lambda x: x.get("수출해외점수", 0) or 0, reverse=True)
    by_lt       = sorted(enriched, key=lambda x: x.get("장기투자점수", 0) or 0, reverse=True)
    by_flow     = sorted(enriched, key=lambda x: x.get("외국인수급점수", 0) or 0, reverse=True)

    tops = [
        ("투자우선 TOP 5",  "🏆", by_priority, "투자우선점수"),
        ("선행매매 신호 TOP 5", "📈", by_lead, "선행매매점수"),
        ("수출/해외 TOP 5", "🌏", by_export, "수출해외점수"),
        ("장기투자 후보 TOP 5","🏛", by_lt,   "장기투자점수"),
        ("외국인수급 TOP 5","💰", by_flow,    "외국인수급점수"),
    ]
    tops_html = "".join(_top5_section(t, i, r, f) for t, i, r, f in tops)

    return f'''
<div class="dash-header">
  <div>
    <span style="font-size:1.1rem;font-weight:900;">52주 신고가 딥다이브</span>
    <span style="margin-left:12px;font-size:0.8rem;color:#888;">수집: {_esc(collected_at)} | 총 {n_total}개 종목</span>
  </div>
  <div style="margin-top:8px;">{grades_html}</div>
  <div style="margin-top:6px;">{country_html}</div>
</div>
<div class="dash-grid">{tops_html}</div>'''


def _make_theme_summary_html(enriched: list[dict]) -> str:
    theme_map: dict[str, list[dict]] = {}
    # 1. 미래산업 키워드 테마
    for r in enriched:
        for t in str(r.get("미래산업테마", "")).split(","):
            t = t.strip()
            if t:
                theme_map.setdefault(t, []).append(r)

    # 2. 성장 정량 테마 (★ 접두어로 상위 표시)
    _GROWTH_RULES = [
        ("★ 매출급성장(NextFY≥20%)",
         lambda r: (r.get("예상매출성장률_NextFY%") or 0) >= 20),
        ("★ 이익급성장(NextFY≥25%)",
         lambda r: (r.get("예상EPS성장률_NextFY%") or 0) >= 25),
        ("★ 고수익성(OPM≥20%)",
         lambda r: (r.get("영업이익률%") or 0) >= 20),
        ("★ 저평가성장(0<PEG<1)",
         lambda r: 0 < (r.get("PEG_TTM") or 0) < 1),
        ("★ 강수급(외국인점수≥65)",
         lambda r: (r.get("외국인수급점수") or 0) >= 65),
        ("★ 고현금흐름(FCF마진≥15%)",
         lambda r: (r.get("FCF마진%") or 0) >= 15),
    ]
    for gtheme, rule in _GROWTH_RULES:
        for r in enriched:
            try:
                if rule(r):
                    lst = theme_map.setdefault(gtheme, [])
                    if r not in lst:
                        lst.append(r)
            except Exception:
                pass

    rows = []
    for theme, members in sorted(theme_map.items(), key=lambda x: -len(x[1])):
        n = len(members)
        avg_score = sum(m.get("투자우선점수", 0) or 0 for m in members) / n
        avg_lead  = sum(m.get("선행매매점수", 0) or 0 for m in members) / n
        avg_grow  = sum(m.get("성장점수", 0) or 0 for m in members) / n
        avg_mt    = sum(m.get("미래산업점수", 0) or 0 for m in members) / n
        avg_exp   = sum(m.get("수출해외점수", 0) or 0 for m in members) / n
        avg_rsi   = sum((m.get("RSI") or 50) for m in members) / n
        avg_pos   = sum((m.get("52주고가대비위치%") or 50) for m in members) / n
        avg_mom   = sum((m.get("1개월수익률%") or 0) for m in members) / n
        best      = max(members, key=lambda x: x.get("투자우선점수", 0) or 0)
        reps      = ", ".join(m.get("티커", "") for m in members[:4])
        cnt_a = sum(1 for m in members if m.get("등급") == "A")
        cnt_b = sum(1 for m in members if m.get("등급") == "B")
        cnt_c = sum(1 for m in members if m.get("등급") not in ("A", "B") and m.get("등급"))
        # 수급 신호 있는 종목 수
        flow_cnt = sum(1 for m in members if m.get("수급패턴") and "청산" not in str(m.get("수급패턴","")))
        rows.append({
            "미래산업테마":      theme,
            "종목수":            n,
            "평균_투자우선점수": round(avg_score, 1),
            "A등급수":           cnt_a,
            "B등급수":           cnt_b,
            "C이하등급수":       cnt_c,
            "수급신호_종목수":   flow_cnt,
            "평균_선행매매점수": round(avg_lead, 1),
            "평균_성장점수":     round(avg_grow, 1),
            "평균_미래산업점수": round(avg_mt, 1),
            "평균_수출해외점수": round(avg_exp, 1),
            "평균_RSI":          round(avg_rsi, 1),
            "평균_52주위치%":    round(avg_pos, 1),
            "평균_1개월수익률%": round(avg_mom, 1),
            "대표종목":          reps,
            "최고점수종목":      best.get("티커", ""),
            "최고점수":          round(best.get("투자우선점수", 0) or 0, 1),
        })

    # 테이블 요약
    tbl_html = _make_table_html(rows, THEME_HEADERS, title="테마 요약")

    # 테마 카드 (상위 8개)
    cards = []
    gmap = {"A":("#155724","#D4EDDA"),"B":("#1E6B00","#C3E6CB"),
            "C":("#856404","#FFF3CD"),"D":("#7B3300","#FFE0C0"),"F":("#7B0000","#FFD0D0")}
    for r in rows[:8]:
        theme_name = r["미래산업테마"]
        members_sorted = sorted(theme_map[theme_name],
                                key=lambda x: x.get("투자우선점수",0) or 0, reverse=True)[:6]
        member_html = ""
        for m in members_sorted:
            g = m.get("등급","F")
            gfg, gbg = gmap.get(g, ("#555","#EEE"))
            sc = m.get("투자우선점수",0) or 0
            pat = m.get("수급패턴","") or ""
            pat_color = "#0069B4" if "매집" in pat else ("#6A0DAD" if "반전" in pat else ("#AA0000" if "청산" in pat else "#1B6B1B" if "추세" in pat else "#888"))
            member_html += (
                f'<div style="display:flex;align-items:center;gap:6px;'
                f'padding:5px 8px;border-bottom:1px solid #f0f0f0;">'
                f'<span style="font-weight:700;font-size:0.78rem;min-width:60px;">{_esc(m.get("티커",""))}</span>'
                f'<span style="font-size:0.75rem;color:#555;flex:1;">{_esc(m.get("기업명",""))}</span>'
                f'<span style="background:{gbg};color:{gfg};font-weight:900;border-radius:999px;'
                f'padding:1px 7px;font-size:0.72rem;">{g}</span>'
                f'<span style="font-weight:700;font-size:0.78rem;color:#1A3A2A;min-width:32px;text-align:right;">{sc:.0f}</span>'
                + (f'<span style="font-size:0.68rem;color:{pat_color};min-width:50px;text-align:right;">{_esc(pat[:5])}</span>' if pat else '')
                + '</div>'
            )
        score_color = "#155724" if r["평균_투자우선점수"] >= 60 else ("#856404" if r["평균_투자우선점수"] >= 40 else "#7B0000")
        cards.append(f'''
<div style="background:#fff;border:1px solid #BBF7D0;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
  <div style="background:#1A3A2A;color:#fff;padding:8px 12px;display:flex;align-items:center;gap:8px;">
    <span style="font-weight:900;font-size:0.85rem;">{_esc(theme_name)}</span>
    <span style="margin-left:auto;font-size:0.75rem;opacity:0.8;">{r["종목수"]}종목</span>
    <span style="background:#D4EDDA;color:{score_color};font-weight:900;border-radius:6px;padding:2px 8px;font-size:0.75rem;">{r["평균_투자우선점수"]:.1f}점</span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0;border-bottom:1px solid #f0f0f0;">
    <div style="padding:6px 8px;text-align:center;border-right:1px solid #f0f0f0;">
      <div style="font-size:0.65rem;color:#888;">A등급</div>
      <div style="font-weight:900;color:#155724;font-size:0.9rem;">{r["A등급수"]}</div>
    </div>
    <div style="padding:6px 8px;text-align:center;border-right:1px solid #f0f0f0;">
      <div style="font-size:0.65rem;color:#888;">수급신호</div>
      <div style="font-weight:900;color:#0069B4;font-size:0.9rem;">{r["수급신호_종목수"]}</div>
    </div>
    <div style="padding:6px 8px;text-align:center;border-right:1px solid #f0f0f0;">
      <div style="font-size:0.65rem;color:#888;">RSI</div>
      <div style="font-weight:700;font-size:0.85rem;">{r["평균_RSI"]:.0f}</div>
    </div>
    <div style="padding:6px 8px;text-align:center;">
      <div style="font-size:0.65rem;color:#888;">1M수익률</div>
      <div style="font-weight:700;font-size:0.85rem;color:{"#1E6B00" if r["평균_1개월수익률%"]>=0 else "#AA0000"};">{r["평균_1개월수익률%"]:+.1f}%</div>
    </div>
  </div>
  <div style="max-height:200px;overflow-y:auto;">{member_html}</div>
</div>''')

    cards_html = f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem;margin-bottom:1.5rem;">{"".join(cards)}</div>'
    return cards_html + tbl_html


def _make_13f_html(sec_rows: list[dict]) -> str:
    """업체(종목)별 그룹 — 많은 기관이 매수/증가 중인 종목이 상단."""
    if not sec_rows:
        return '<div class="empty-msg">13F 데이터 없음 (SEC EDGAR 미수집)</div>'

    # ── 업체별 그룹 집계 ─────────────────────────────
    from collections import defaultdict
    company_map: dict[str, list[dict]] = defaultdict(list)
    for r in sec_rows:
        tk = r.get("티커","") or r.get("종목명","")
        if not tk:
            continue
        # 한국 6자리, CUSIP 8~9자리 숫자, 비정상 길이 제외
        if re.fullmatch(r"\d{6,9}", tk):
            continue
        if len(tk) > 8:
            continue
        company_map[tk].append(r)

    _chg_order = {"신규": 0, "증가": 1, "유지": 2, "감소": 3}
    _chg_color  = {
        "신규": ("#fff", "#0069B4"),
        "증가": ("#fff", "#1B6B1B"),
        "감소": ("#fff", "#A30000"),
        "유지": ("#555", "#E0E0E0"),
    }

    # ── 컨센서스 점수로 정렬 ─────────────────────────
    def _company_score(holdings: list[dict]) -> tuple:
        n_new = sum(1 for h in holdings if h.get("포지션변화") == "신규")
        n_inc = sum(1 for h in holdings if h.get("포지션변화") == "증가")
        n_dec = sum(1 for h in holdings if h.get("포지션변화") == "감소")
        n_inst = len({h.get("기관명","") for h in holdings})
        score  = n_new * 3 + n_inc * 2 - n_dec
        total  = sum(h.get("보유가치_USD", 0) or 0 for h in holdings)
        return (score, n_inst, total)

    ranked = sorted(
        company_map.items(),
        key=lambda kv: _company_score(kv[1]),
        reverse=True,
    )

    # 상단: 신규/증가/유지 위주 (score>=0) — 매수·홀딩 컨센서스 종목
    # 하단: 감소·청산만 있는 종목 (score<0) — 매도 컨센서스
    positive_companies, negative_companies = [], []
    for tk, holdings in ranked:
        score, _, _ = _company_score(holdings)
        if score >= 0:
            positive_companies.append((tk, holdings))
        else:
            negative_companies.append((tk, holdings))

    def _render_one(tk, holdings):
        name   = _truncate_name(holdings[0].get("종목명","") or tk, 40)
        score, n_inst, total_val = _company_score(holdings)
        n_new  = sum(1 for h in holdings if h.get("포지션변화") == "신규")
        n_inc  = sum(1 for h in holdings if h.get("포지션변화") == "증가")
        n_dec  = sum(1 for h in holdings if h.get("포지션변화") == "감소")
        n_hold = sum(1 for h in holdings if h.get("포지션변화") == "유지")

        # 헤더 색상 — 점수 기반
        hdr_bg = ("#1B5E20" if score >= 12 else
                  "#1565C0" if score >= 6 else
                  "#1F4E79" if score >= 0 else
                  "#7B1A1A")

        # 포지션 변화 뱃지
        badge = lambda label, fg, bg, n: (
            f'<span style="background:{bg};color:{fg};font-weight:700;'
            f'border-radius:4px;padding:1px 7px;font-size:0.7rem;">{label} {n}</span>'
        ) if n > 0 else ""

        badges = (
            badge("신규", "#fff", "#0069B4", n_new) +
            badge("증가", "#fff", "#1B6B1B", n_inc) +
            badge("유지", "#555", "#D0D0D0", n_hold) +
            badge("감소", "#fff", "#A30000", n_dec)
        )

        # 컨센서스 점수 색
        sc_color = ("#D4EDDA" if score >= 12 else
                    "#BBDEFB" if score >= 6 else
                    "#FFF9C4" if score >= 0 else
                    "#FFCDD2")
        sc_tc    = ("#155724" if score >= 12 else
                    "#0D47A1" if score >= 6 else
                    "#856404" if score >= 0 else
                    "#B71C1C")

        # 기관별 행 정렬: 신규 > 증가 > 유지 > 감소, 그 다음 포트폴리오비중% 높은 순
        sorted_h = sorted(
            holdings,
            key=lambda x: (
                _chg_order.get(x.get("포지션변화","유지"), 2),
                -(x.get("포트폴리오비중%") or 0),
                -(x.get("보유가치_USD") or 0),
            ),
        )

        # 기관 행 테이블 (기관명, 포지션변화, 포트폴리오비중%, 주식수_변화율%, 보유가치_USD, 전분기_주식수, 주식수, 보고일)
        row_html = []
        for h in sorted_h:
            chg   = h.get("포지션변화","유지")
            cfx, cbg = _chg_color.get(chg, ("#555","#E0E0E0"))
            wt    = h.get("포트폴리오비중%")
            pct   = h.get("주식수_변화율%")
            val   = h.get("보유가치_USD") or 0
            sh    = h.get("주식수") or 0
            prev  = h.get("전분기_주식수")
            dt    = h.get("보고일","")
            mgr   = h.get("기관명","")

            wt_s  = f"{wt:.2f}%" if wt is not None else "-"
            pct_s = (f"+{pct:.1f}%" if pct and pct > 0 else f"{pct:.1f}%" if pct is not None else "-")
            val_s = (f"${val/1e9:.2f}B" if val >= 1e9 else f"${val/1e6:.0f}M" if val >= 1e6 else f"${val:,.0f}")
            sh_s  = f"{int(sh):,}" if sh else "-"
            prev_s= f"{int(prev):,}" if prev else "-"
            pct_c = "#1B6B1B" if (pct or 0) > 0 else "#A30000"

            row_html.append(
                f'<tr style="border-bottom:1px solid #f0f4f8;">'
                f'<td style="padding:4px 8px;font-weight:700;font-size:0.8rem;white-space:nowrap;">{_esc(mgr)}</td>'
                f'<td style="padding:4px 8px;text-align:center;">'
                f'<span style="background:{cbg};color:{cfx};font-weight:700;font-size:0.72rem;'
                f'border-radius:4px;padding:1px 7px;">{chg}</span></td>'
                f'<td style="padding:4px 8px;text-align:right;font-size:0.78rem;">{wt_s}</td>'
                f'<td style="padding:4px 8px;text-align:right;font-size:0.78rem;'
                f'font-weight:700;color:{pct_c};">{pct_s}</td>'
                f'<td style="padding:4px 8px;text-align:right;font-size:0.78rem;">{val_s}</td>'
                f'<td style="padding:4px 8px;text-align:right;font-size:0.78rem;color:#666;">{sh_s}</td>'
                f'<td style="padding:4px 8px;text-align:right;font-size:0.78rem;color:#999;">{prev_s}</td>'
                f'<td style="padding:4px 8px;text-align:center;font-size:0.72rem;color:#888;">{_esc(dt)}</td>'
                f'</tr>'
            )

        tbl = (
            f'<table style="width:100%;border-collapse:collapse;background:var(--card);">'
            f'<thead><tr style="background:#2F4F6F;color:#fff;font-size:0.72rem;">'
            f'<th style="padding:4px 8px;text-align:left;">기관명</th>'
            f'<th style="padding:4px 8px;">포지션변화</th>'
            f'<th style="padding:4px 8px;text-align:right;">포트폴리오비중%</th>'
            f'<th style="padding:4px 8px;text-align:right;">주식수변화율%</th>'
            f'<th style="padding:4px 8px;text-align:right;">보유가치</th>'
            f'<th style="padding:4px 8px;text-align:right;">주식수</th>'
            f'<th style="padding:4px 8px;text-align:right;">전분기주식수</th>'
            f'<th style="padding:4px 8px;text-align:center;">13F신고일</th>'
            f'</tr></thead>'
            f'<tbody>{"".join(row_html)}</tbody>'
            f'</table>'
        )

        return (
            f'<div style="margin-bottom:18px;border:1px solid #CBD5E1;border-radius:6px;overflow:hidden;">'
            f'<div style="background:{hdr_bg};color:#fff;padding:7px 12px;'
            f'display:flex;gap:10px;align-items:center;flex-wrap:wrap;">'
            f'<span style="font-weight:900;font-size:0.92rem;">{_esc(name)}</span>'
            f'<span style="font-size:0.78rem;opacity:0.9;">[{_esc(tk)}]</span>'
            f'<span style="background:{sc_color};color:{sc_tc};font-weight:900;'
            f'border-radius:5px;padding:1px 8px;font-size:0.75rem;">컨센서스 {score:+d}</span>'
            f'<span style="font-size:0.75rem;opacity:0.85;">{n_inst}개 기관 | '
            f'총 {"${:.1f}B".format(total_val/1e9) if total_val>=1e9 else "${:.0f}M".format(total_val/1e6)}</span>'
            f'{badges}'
            f'</div>'
            f'{tbl}'
            f'</div>'
        )

    out = []
    if positive_companies:
        out.append(
            '<div style="margin:0 0 12px 0;padding:8px 12px;background:#E8F5E9;'
            'border-left:4px solid #1B5E20;border-radius:4px;">'
            '<span style="font-weight:900;color:#1B5E20;font-size:0.95rem;">'
            f'🟢 매수·홀딩 컨센서스 ({len(positive_companies)}개)</span> '
            '<span style="color:#555;font-size:0.78rem;">'
            '신규/증가 기관 우세 — 컨센서스 점수 ≥ 0</span></div>'
        )
        out.extend(_render_one(tk, h) for tk, h in positive_companies)

    if negative_companies:
        out.append(
            '<div style="margin:20px 0 12px 0;padding:8px 12px;background:#FFEBEE;'
            'border-left:4px solid #B71C1C;border-radius:4px;">'
            '<span style="font-weight:900;color:#B71C1C;font-size:0.95rem;">'
            f'🔴 매도·청산 컨센서스 ({len(negative_companies)}개)</span> '
            '<span style="color:#555;font-size:0.78rem;">'
            '감소·청산 기관 우세 — 컨센서스 점수 &lt; 0</span></div>'
        )
        out.extend(_render_one(tk, h) for tk, h in negative_companies)

    return "".join(out) if out else '<div class="empty-msg">13F 데이터 없음</div>'


_HTML_CSS = '''
:root {
  --ac:#16A34A; --acL:#DCFCE7;
  --bg:#F5F7FA; --card:#FFFFFF;
  --card2:#F0FDF4; --t1:#111827; --t2:#475569; --t3:#94A3B8;
  --bd:#BBF7D0; --hdr:#1A3A2A; --tbg:rgba(255,255,255,0.92);
  --tgB:#BBF7D0; --tgK:#16A34A; --glow:rgba(22,163,74,0.4);
  --shadow:0 4px 18px rgba(15,23,42,0.08);
  --cell-odd:#F2F6FC; --cell-even:#FFFFFF;
  --hover-bg:#DCFCE7; --hover-fg:#1A3A2A;
}
[data-t=dark] {
  --ac:#4ADE80; --acL:rgba(74,222,128,0.12); --bg:#071510; --card:#0D1F15;
  --card2:#122B1C; --t1:#E2E8F0; --t2:#94A3B8; --t3:#475569;
  --bd:#1A3A2A; --tbg:rgba(7,21,16,0.92); --tgB:#1A3A2A; --tgK:#4ADE80;
  --cell-odd:#141e2e; --cell-even:#0f1824;
  --hover-bg:#1A3A2A; --hover-fg:#D4F5DC;
}
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;font-size:14px;}
body{font-family:"Noto Sans KR","Malgun Gothic",system-ui,sans-serif;
  background:var(--bg);color:var(--t1);min-height:100vh;}
/* topbar */
.topbar{position:sticky;top:0;z-index:50;background:var(--tbg);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border-bottom:1px solid var(--bd);padding:0.35rem 0.9rem;
  display:flex;align-items:center;gap:0.4rem;overflow-x:auto;
  -webkit-overflow-scrolling:touch;}
.topbar-title{font-size:0.72rem;font-weight:900;white-space:nowrap;
  color:var(--t1);margin-right:0.5rem;}
.nav{display:flex;gap:0.2rem;overflow-x:auto;flex:1;min-width:0;}
.tab-btn{border:1px solid var(--bd);background:var(--card);color:var(--t2);
  border-radius:999px;padding:0.28rem 0.65rem;font-size:0.68rem;
  font-weight:800;white-space:nowrap;cursor:pointer;transition:all 0.2s;
  font-family:inherit;}
.tab-btn:hover{background:var(--acL);border-color:var(--ac);color:var(--ac);}
.tab-btn.on{background:var(--ac);border-color:var(--ac);color:#fff;}
/* dark toggle */
.tt{display:flex;align-items:center;gap:0.3rem;cursor:pointer;
  user-select:none;margin-left:0.5rem;}
.tt-label{font-size:0.65rem;color:var(--t3);}
.tk{width:32px;height:16px;border-radius:999px;background:var(--tgB);position:relative;}
.kn{width:12px;height:12px;border-radius:50%;background:var(--tgK);
  position:absolute;top:2px;left:2px;transition:transform 0.3s,background 0.3s;}
[data-t=dark] .kn{transform:translateX(16px);background:#60a5fa;}
/* main */
main{padding:0.8rem 1rem;max-width:100%;}
/* panel */
.panel{display:none;}
.panel.on{display:block;}
/* dashboard */
.dash-header{background:var(--card);border:1px solid var(--bd);
  border-radius:12px;padding:1rem 1.2rem;margin-bottom:1rem;
  box-shadow:var(--shadow);}
.dash-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem;margin-bottom:1rem;}
.dash-card{background:var(--card);border:1px solid var(--bd);
  border-radius:10px;overflow:hidden;box-shadow:var(--shadow);}
.dash-card-title{background:var(--hdr);color:#fff;padding:6px 12px;
  font-size:0.82rem;font-weight:800;}
.dash-card table{width:100%;}
.dash-card td{border-bottom:1px solid var(--bd);font-size:0.8rem;color:var(--t1);}
/* table panel */
.panel-head{padding:0.7rem 1rem;border-bottom:1px solid var(--bd);
  background:var(--card2);display:flex;align-items:center;gap:0.5rem;
  border-radius:12px 12px 0 0;}
.panel-head h2{font-size:0.9rem;font-weight:900;color:var(--t1);}
.panel-count{font-size:0.7rem;color:var(--t3);margin-left:auto;}
.panel-body{background:var(--card);border:1px solid var(--bd);
  border-radius:0 0 12px 12px;overflow:hidden;box-shadow:var(--shadow);}
.tbl-wrap{overflow-x:auto;max-height:76vh;overflow-y:auto;
  scrollbar-width:thin;scrollbar-color:var(--bd) transparent;}
.tbl-wrap::-webkit-scrollbar{width:6px;height:6px;}
.tbl-wrap::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px;}
table.dtbl{border-collapse:collapse;width:max-content;min-width:100%;}
table.dtbl thead tr th{position:sticky;top:0;z-index:2;}
/* 행 전체 호버 하이라이트 */
table.dtbl tr.drow:hover td{
  background:var(--hover-bg) !important;
  color:var(--hover-fg) !important;
  transition:background 0.1s;}
/* 다크모드 셀 테두리 */
[data-t=dark] table.dtbl td{
  border-bottom-color:#2a3444 !important;
  border-right-color:#2a3444 !important;}
/* 다크모드 컬러 셀 오버라이드 (CSS 변수 기반 기본 셀은 자동 전환) */
[data-t=dark] td[style*="background:#D4EDDA"]{background:#1a3a25 !important;color:#86efac !important;}
[data-t=dark] td[style*="background:#D4F5DC"]{background:#1a3a25 !important;color:#86efac !important;}
[data-t=dark] td[style*="background:#EAF7EA"]{background:#152a1e !important;color:#6ee7b7 !important;}
[data-t=dark] td[style*="background:#E8F5E9"]{background:#0d2218 !important;color:#86efac !important;}
[data-t=dark] td[style*="background:#E0F5E0"]{background:#0d2218 !important;color:#86efac !important;}
[data-t=dark] td[style*="background:#FFF0F0"]{background:#3a1515 !important;color:#fca5a5 !important;}
[data-t=dark] td[style*="background:#FFD0D0"]{background:#3a1515 !important;color:#fca5a5 !important;}
[data-t=dark] td[style*="background:#EBF5FF"]{background:#0f2040 !important;color:#93c5fd !important;}
[data-t=dark] td[style*="background:#E3F2FD"]{background:#0c1f38 !important;color:#93c5fd !important;}
[data-t=dark] td[style*="background:#FFE0E0"]{background:#3a1010 !important;color:#fca5a5 !important;}
[data-t=dark] td[style*="background:#FFF9C4"]{background:#2a2200 !important;color:#fde68a !important;}
[data-t=dark] td[style*="background:#FFD700"]{background:#2a2200 !important;color:#fde68a !important;}
[data-t=dark] td[style*="background:#FFE8CC"]{background:#2a1800 !important;color:#fcd34d !important;}
[data-t=dark] td[style*="background:#155724"]{background:#1a4a2e !important;color:#d4f5dc !important;}
[data-t=dark] td[style*="background:#1E6B00"]{background:#1a4a2e !important;color:#86efac !important;}
[data-t=dark] td[style*="background:#0069B4"]{background:#0a2d50 !important;color:#93c5fd !important;}
[data-t=dark] td[style*="background:#1B6B1B"]{background:#0d3b0d !important;color:#86efac !important;}
[data-t=dark] td[style*="background:#A30000"]{background:#3b0d0d !important;color:#fca5a5 !important;}
/* 다크모드 텍스트 색상 오버라이드 (배경 없는 컬러 텍스트) */
[data-t=dark] td[style*="color:#1E6B00"]:not([style*="background:#"]){color:#6ee7b7 !important;}
[data-t=dark] td[style*="color:#155724"]:not([style*="background:#"]){color:#86efac !important;}
[data-t=dark] td[style*="color:#1B6B1B"]:not([style*="background:#"]){color:#6ee7b7 !important;}
[data-t=dark] td[style*="color:#CC0000"]{color:#fca5a5 !important;}
[data-t=dark] td[style*="color:#AA0000"]{color:#fca5a5 !important;}
[data-t=dark] td[style*="color:#A30000"]{color:#fca5a5 !important;}
[data-t=dark] td[style*="color:#0D47A1"]{color:#93c5fd !important;}
[data-t=dark] td[style*="color:#003399"]{color:#93c5fd !important;}
[data-t=dark] td[style*="color:#0069B4"]{color:#93c5fd !important;}
[data-t=dark] td[style*="color:#856404"]{color:#fde68a !important;}
[data-t=dark] td[style*="color:#7B3300"]{color:#fcd34d !important;}
[data-t=dark] td[style*="color:#7B0000"]{color:#fca5a5 !important;}
.empty-msg{padding:2rem;text-align:center;color:var(--t3);font-size:0.9rem;}
/* search bar */
.search-bar{padding:0.5rem 1rem;background:var(--card2);
  border-bottom:1px solid var(--bd);display:flex;gap:0.4rem;align-items:center;}
.search-bar input{border:1px solid var(--bd);border-radius:6px;
  padding:0.3rem 0.6rem;font-size:0.75rem;background:var(--bg);
  color:var(--t1);outline:none;font-family:inherit;}
.search-bar input:focus{border-color:var(--ac);box-shadow:0 0 0 2px var(--acL);}
@media(max-width:600px){
  .topbar{padding:0.3rem 0.5rem;}
  .tab-btn{font-size:0.62rem;padding:0.22rem 0.5rem;}
  main{padding:0.5rem;}
}
'''

_HTML_JS = '''
(function(){
  // ── 탭 전환 ──
  const tabs=[...document.querySelectorAll('.tab-btn')];
  const panels=[...document.querySelectorAll('.panel')];
  function switchTab(id){
    tabs.forEach(b=>b.classList.toggle('on',b.dataset.tab===id));
    panels.forEach(p=>p.classList.toggle('on',p.id==='panel-'+id));
    history.replaceState(null,'','#'+id);
  }
  tabs.forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
  const hash=(location.hash||'').replace('#','');
  const initial=tabs.find(b=>b.dataset.tab===hash)||tabs[0];
  if(initial) switchTab(initial.dataset.tab);

  // ── 다크모드 ──
  const dm=document.getElementById('dm-toggle');
  if(dm){
    const stored=localStorage.getItem('theme');
    if(stored) document.documentElement.dataset.t=stored;
    dm.addEventListener('click',()=>{
      const cur=document.documentElement.dataset.t||'light';
      const next=cur==='dark'?'light':'dark';
      document.documentElement.dataset.t=next;
      localStorage.setItem('theme',next);
    });
  }

  // ── 테이블 검색 ──
  document.querySelectorAll('.tbl-search').forEach(inp=>{
    inp.addEventListener('input',()=>{
      const q=inp.value.toLowerCase();
      const tbl=document.getElementById(inp.dataset.tbl);
      if(!tbl) return;
      tbl.querySelectorAll('tbody tr').forEach(tr=>{
        tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none';
      });
    });
  });

  // ── 컬럼 정렬 ──
  document.querySelectorAll('table.dtbl thead th').forEach(th=>{
    th.title='클릭하여 정렬';
    th.style.cursor='pointer';
    th.addEventListener('click',()=>{
      const tbody=th.closest('table').querySelector('tbody');
      const idx=[...th.parentNode.children].indexOf(th);
      const asc=th.dataset.asc!=='true';
      th.dataset.asc=String(asc);
      th.textContent=th.textContent.replace(/[▲▼]/g,'')+(asc?' ▲':' ▼');
      [...tbody.querySelectorAll('tr')].sort((a,b)=>{
        const va=a.children[idx]?.textContent.trim()||'';
        const vb=b.children[idx]?.textContent.trim()||'';
        const na=parseFloat(va.replace(/,/g,'')),nb=parseFloat(vb.replace(/,/g,''));
        if(!isNaN(na)&&!isNaN(nb)) return asc?na-nb:nb-na;
        return asc?va.localeCompare(vb,'ko'):vb.localeCompare(va,'ko');
      }).forEach(r=>tbody.appendChild(r));
    });
  });
})();
'''


_TAB_CONFIG = [
    ("dashboard",      "대시보드",         "#155724"),
    ("priority_top",   "우선순위_TOP",      "#1E6B00"),
    ("lead_signal",    "선행매매_시그널",   "#7030A0"),
    ("export_top",     "수출해외_상위",     "#005F00"),
    ("long_term",      "장기투자_후보",     "#1A4A00"),
    ("flow_detail",    "외국인_수급",       "#003399"),
    ("theme_summary",  "테마_요약",         "#6B0080"),
    ("market",         "시장지표",          "#1F4E79"),
    ("inst_overlap",   "기관중복보유",      "#004C6D"),
    ("sec_detail",     "유명기관_13F",      "#006699"),
    ("earnings_cal",   "실적캘린더",        "#7B3300"),
    ("sector_perf",    "섹터성과",          "#2B5219"),
    ("highs_us",       "신고가_미국",       "#555555"),
    ("highs_kr",       "신고가_한국",       "#555555"),
    ("vol_us",         "거래량급증_미국",   "#555555"),
    ("vol_kr",         "거래량급증_한국",   "#555555"),
    ("tracking",       "일별_트래킹",       "#333333"),
]


def _panel_wrap(tab_id: str, title: str, count: int, content: str) -> str:
    return f'''
<section class="panel" id="panel-{tab_id}">
  <div class="panel-head">
    <h2>{_esc(title)}</h2>
    <span class="panel-count">{count}개 종목</span>
  </div>
  <div class="search-bar">
    <input class="tbl-search" data-tbl="tbl-{tab_id}"
           placeholder="🔍 검색 (기업명/티커/섹터)..." style="width:240px;">
  </div>
  <div class="panel-body" id="tbl-{tab_id}">{content}</div>
</section>'''


def _make_market_panel_html(market_data: list[dict], fg: dict,
                             insider_rows: list[dict]) -> str:
    # Fear & Greed 카드
    score = fg.get("score")
    rating = fg.get("rating", "N/A")
    if score is not None:
        if score >= 75:   fg_color, fg_label = "#1E6B00", "극단적 탐욕"
        elif score >= 55: fg_color, fg_label = "#70AD47", "탐욕"
        elif score >= 45: fg_color, fg_label = "#FFC000", "중립"
        elif score >= 25: fg_color, fg_label = "#FF6600", "공포"
        else:             fg_color, fg_label = "#C00000", "극단적 공포"
        fg_html = (f'<div style="background:{fg_color};color:#fff;border-radius:12px;'
                   f'padding:1rem 1.5rem;text-align:center;margin-bottom:1rem;">'
                   f'<div style="font-size:2.5rem;font-weight:900;">{score:.0f}</div>'
                   f'<div style="font-size:1rem;font-weight:700;">{fg_label}</div>'
                   f'<div style="font-size:0.75rem;opacity:0.8;">CNN Fear &amp; Greed Index</div>'
                   f'<div style="font-size:0.7rem;margin-top:4px;">'
                   f'1주전: {fg.get("prev_1w","?")} | 1개월전: {fg.get("prev_1m","?")} | '
                   f'1년전: {fg.get("prev_1y","?")}</div></div>')
    else:
        fg_html = '<div class="empty-msg">Fear &amp; Greed 수집 실패</div>'

    market_tbl = _make_table_html(market_data, MARKET_HEADERS) if market_data else \
                 '<div class="empty-msg">시장 데이터 수집 실패</div>'
    insider_tbl = _make_table_html(insider_rows, INSIDER_HEADERS) if insider_rows else \
                  '<div class="empty-msg">내부자 거래 수집 실패 (openinsider.com)</div>'

    return f'''
<div style="display:grid;grid-template-columns:220px 1fr;gap:1rem;padding:1rem;">
  <div>{fg_html}</div>
  <div>
    <div style="font-weight:800;font-size:0.85rem;margin-bottom:0.5rem;">📈 주요 시장 지표</div>
    {market_tbl}
  </div>
</div>
<div style="padding:0 1rem 1rem;">
  <div style="font-weight:800;font-size:0.85rem;margin-bottom:0.5rem;">
    🏢 최근 내부자 매수 (14일, $100K+, openinsider.com)
  </div>
  {insider_tbl}
</div>'''


def generate_html(enriched: list[dict], volume_us: list[dict],
                  volume_kr: list[dict], sec_rows: list[dict],
                  market_data: list[dict], fg: dict,
                  insider_rows: list[dict], inst_overlap: list[dict],
                  sector_rows: list[dict], earnings_rows: list[dict],
                  collected_at: str, kr_names: dict | None = None) -> str:

    enr_us  = sorted([r for r in enriched if r.get("국가") == "US"],
                     key=lambda x: x.get("투자우선점수", 0) or 0, reverse=True)
    enr_kr  = sorted([r for r in enriched if r.get("국가") == "KR"],
                     key=lambda x: x.get("투자우선점수", 0) or 0, reverse=True)
    enr_all = sorted(enriched, key=lambda x: x.get("투자우선점수", 0) or 0, reverse=True)

    by_priority = enr_all[:120]
    by_lead     = sorted(enriched,
                         key=lambda x: (x.get("선행매매점수",0) or 0,
                                        x.get("투자우선점수",0) or 0),
                         reverse=True)
    by_export   = sorted([r for r in enriched if r.get("수출섹터여부") == "Y"],
                         key=lambda x: x.get("투자우선점수",0) or 0, reverse=True)
    if len(by_export) < 10:
        by_export = sorted(enriched,
                           key=lambda x: x.get("수출해외점수",0) or 0, reverse=True)
    by_lt       = enr_all[:150]
    by_flow     = (sorted(enr_kr, key=lambda x: x.get("투자우선점수",0) or 0, reverse=True)
                   + sorted(enr_us, key=lambda x: x.get("투자우선점수",0) or 0, reverse=True))[:200]
    by_tracking = sorted(enriched,
                         key=lambda x: (x.get("수집일",""), x.get("_ticker","")))

    # "일별_트래킹" 탭 옆에 업데이트 시각 표시
    _m  = str(_NOW.month)
    _d  = str(_NOW.day)
    _ts_short = f"{_m}/{_d} {_NOW.strftime('%H:%M')} KST"
    tab_buttons = []
    for tid, tlabel, tcolor in _TAB_CONFIG:
        if tid == "tracking":
            tab_buttons.append(
                f'<button class="tab-btn" data-tab="{tid}">'
                f'{_esc(tlabel)}'
                f'<span style="margin-left:5px;font-size:0.6rem;font-weight:400;'
                f'opacity:0.85;white-space:nowrap;">{_esc(_ts_short)}</span>'
                f'</button>'
            )
        else:
            tab_buttons.append(
                f'<button class="tab-btn" data-tab="{tid}">{_esc(tlabel)}</button>'
            )

    panels_html = []

    # 대시보드
    panels_html.append(f'''
<section class="panel" id="panel-dashboard">
  <div class="panel-head"><h2>대시보드</h2></div>
  <div class="panel-body" style="padding:1rem;">
    {_make_dashboard_html(enriched, collected_at)}
  </div>
</section>''')

    # 우선순위_TOP
    panels_html.append(_panel_wrap("priority_top", "우선순위 TOP",
                                   len(by_priority),
                                   _make_table_html(by_priority, TOP_HEADERS)))
    # 선행매매_시그널
    panels_html.append(_panel_wrap("lead_signal", "선행매매 시그널",
                                   len(by_lead),
                                   _make_table_html(by_lead, LEAD_HEADERS)))
    # 수출해외_상위
    panels_html.append(_panel_wrap("export_top", "수출/해외 상위",
                                   len(by_export),
                                   _make_table_html(by_export, EXPORT_HEADERS)))
    # 장기투자_후보
    panels_html.append(_panel_wrap("long_term", "장기투자 후보",
                                   len(by_lt),
                                   _make_table_html(by_lt, LONG_TERM_HEADERS)))
    # 외국인_수급
    panels_html.append(_panel_wrap("flow_detail", "외국인 수급",
                                   len(by_flow),
                                   _make_table_html(by_flow, FLOW_HEADERS)))
    # 테마_요약
    panels_html.append(f'''
<section class="panel" id="panel-theme_summary">
  <div class="panel-head"><h2>테마 요약</h2></div>
  <div class="panel-body" style="padding:1rem;">
    {_make_theme_summary_html(enriched)}
  </div>
</section>''')

    # 기관중복보유
    INST_OVERLAP_HEADERS = [
        "컨센서스점수","티커","종목명",
        "기관수","신규기관수","증가기관수","감소기관수",
        "신규기관","증가기관","기관목록",
        "총보유가치_USD","보고일",
    ]
    panels_html.append(f'''
<section class="panel" id="panel-inst_overlap">
  <div class="panel-head"><h2>기관 중복 보유 종목 (다수 기관 동시 포지션)</h2>
    <span class="panel-count">{len(inst_overlap)}종목</span>
  </div>
  <div class="search-bar">
    <input class="tbl-search" data-tbl="tbl-inst_overlap"
           placeholder="🔍 검색 (티커/기관명)..." style="width:240px;">
    <span style="font-size:0.75rem;color:#666;margin-left:8px;">💡 컨센서스점수 = 신규×3 + 증가×2 - 감소 | 전분기 대비 포지션 변화 기반</span>
  </div>
  <div class="panel-body" id="tbl-inst_overlap">
    {_make_table_html(inst_overlap, INST_OVERLAP_HEADERS) if inst_overlap
     else '<div class="empty-msg">13F 데이터 수집 후 표시 (SEC EDGAR 분기 공시)</div>'}
  </div>
</section>''')

    # 유명기관_13F
    panels_html.append(f'''
<section class="panel" id="panel-sec_detail">
  <div class="panel-head"><h2>유명기관 13F 보유 상세</h2>
    <span class="panel-count">{len(sec_rows)}건</span>
  </div>
  <div class="search-bar">
    <input class="tbl-search" data-tbl="tbl-sec_detail"
           placeholder="🔍 검색 (기관명/종목명)..." style="width:240px;">
  </div>
  <div class="panel-body" id="tbl-sec_detail">
    {_make_13f_html(sec_rows)}
  </div>
</section>''')

    # 시장지표
    panels_html.append(f'''
<section class="panel" id="panel-market">
  <div class="panel-head"><h2>시장지표 &amp; 내부자거래</h2></div>
  <div class="panel-body">
    {_make_market_panel_html(market_data, fg, insider_rows)}
  </div>
</section>''')

    # 실적캘린더
    EARNINGS_HEADERS = ["실적일","티커","회사명","섹터","예상EPS","Forward_PER","매출_YoY%"]
    panels_html.append(f'''
<section class="panel" id="panel-earnings_cal">
  <div class="panel-head"><h2>실적 캘린더 (향후 2주)</h2>
    <span class="panel-count">{len(earnings_rows)}건</span>
  </div>
  <div class="panel-body">
    {_make_table_html(earnings_rows, EARNINGS_HEADERS) if earnings_rows
     else '<div class="empty-msg">향후 2주 실적 발표 예정 없음 (또는 수집 실패)</div>'}
  </div>
</section>''')

    # 섹터성과
    SECTOR_HEADERS = ["섹터","심볼","현재가","전일비%","1개월수익%","52주위치%"]
    panels_html.append(f'''
<section class="panel" id="panel-sector_perf">
  <div class="panel-head"><h2>미국 섹터 ETF 성과</h2>
    <span class="panel-count">{len(sector_rows)}개 섹터</span>
  </div>
  <div class="panel-body">
    {_make_table_html(sector_rows, SECTOR_HEADERS) if sector_rows
     else '<div class="empty-msg">섹터 데이터 수집 실패</div>'}
  </div>
</section>''')

    # 신고가_미국
    panels_html.append(_panel_wrap("highs_us", "신고가 미국",
                                   len(enr_us),
                                   _make_table_html(enr_us, US_DETAIL_HEADERS)))
    # 신고가_한국
    panels_html.append(_panel_wrap("highs_kr", "신고가 한국",
                                   len(enr_kr),
                                   _make_table_html(enr_kr, KR_DETAIL_HEADERS)))

    # 거래량급증_미국
    _kr_names = kr_names or {}

    def _score_vol_row(r: dict) -> tuple:
        """거래량 급증 행에 대한 간소화된 투자우선점수 및 등급 계산."""
        score = 50.0
        rv = r.get("relative_volume_10d_calc") or 0
        score += min(15, float(rv) * 3)
        perf1m = r.get("Perf.1M") or 0
        score += max(-10.0, min(10.0, float(perf1m) * 0.5))
        fpe = r.get("price_earnings_forward_fy")
        if fpe is not None:
            fpe = float(fpe)
            if 5 < fpe < 25:
                score += 5
            elif fpe < 0:
                score -= 5
        opm = r.get("operating_margin_ttm") or 0
        opm = float(opm)
        if opm >= 20:   score += 8
        elif opm >= 10: score += 4
        elif opm < 0:   score -= 8
        rev_g = r.get("total_revenue_yoy_growth_fq") or 0
        rev_g = float(rev_g)
        if rev_g >= 20:    score += 8
        elif rev_g >= 10:  score += 4
        elif rev_g < -10:  score -= 5
        debt = r.get("debt_to_equity_fq") or 0
        debt = float(debt)
        if debt > 200:   score -= 5
        elif debt < 50:  score += 3
        fcf = r.get("free_cash_flow_margin_ttm") or 0
        fcf = float(fcf)
        if fcf >= 15:   score += 5
        elif fcf < 0:   score -= 5
        rsi = r.get("RSI") or 50
        rsi = float(rsi)
        if 40 <= rsi <= 70: score += 3
        elif rsi > 80:      score -= 3
        score = max(0.0, min(100.0, score))
        grade = ("A" if score >= 80 else "B" if score >= 65
                 else "C" if score >= 50 else "D" if score >= 35 else "F")
        return round(score, 1), grade

    def _vol_row(r, country):
        code  = _bare_kr_code(r.get("_ticker",""))
        nm    = r.get("description","")
        flow  = _kr_names.get(code, {})
        if country == "KR":
            nm = (_KNOWN_KR_NAMES.get(code)
                  or flow.get("naver_기업명", "")
                  or nm)
        nm = _truncate_name(nm, 36)

        # 52주 위치 계산 (TV 원시 데이터에서)
        hi52  = r.get("price_52_week_high") or 0
        lo52  = r.get("price_52_week_low")  or 0
        close = r.get("close") or 0
        pos52 = (close - lo52) / (hi52 - lo52) * 100 if (hi52 - lo52) > 0 else None

        vol_score, vol_grade = _score_vol_row(r)

        row = {
            "국가":            country,
            "티커":            r.get("_ticker","").split(":")[-1] if country=="US" else code,
            "기업명":          nm,
            "섹터":            r.get("sector",""),
            "미래산업테마":    "",
            "상대거래량":      r.get("relative_volume_10d_calc"),
            "변동률%":         r.get("change"),
            "종가":            close or None,
            "시가총액":        r.get("market_cap_basic"),
            "52주고가대비위치%": pos52,
            "RSI":             r.get("RSI"),
            "1주수익률%":      r.get("Perf.W"),
            "1개월수익률%":    r.get("Perf.1M"),
            "3개월수익률%":    r.get("Perf.3M"),
            "매출성장률_YoY%": r.get("total_revenue_yoy_growth_fq"),
            "EPS성장률_YoY%":  r.get("earnings_per_share_diluted_yoy_growth_fq"),
            # TV 펀더멘털 (US 거래량급증 탭 전용)
            "Forward_PER":    r.get("price_earnings_forward_fy"),
            "영업이익률%":    r.get("operating_margin_ttm"),
            "부채비율":        r.get("debt_to_equity_fq"),
            "FCF마진%":        r.get("free_cash_flow_margin_ttm"),
            "수급패턴":        "",
            "선행매매점수":    0,
            "투자우선점수":    vol_score,
            "등급":            vol_grade,
        }
        # KR 전용: Naver 수급 플로우 추가
        if country == "KR":
            row["외국인_순매수_5일"]  = flow.get("외국인_순매수_5일")
            row["외국인_순매수_20일"] = flow.get("외국인_순매수_20일")
            row["기관_순매수_5일"]    = flow.get("기관_순매수_5일")
            row["기관_순매수_20일"]   = flow.get("기관_순매수_20일")
            row["외국인_지분율%"]     = flow.get("외국인_지분율%")
        return row

    # 신고가 종목과 중복되는 거래량급증 종목 제거
    _high_tickers = {r.get("티커","") for r in enriched}
    _high_raw     = {r.get("_ticker","").split(":")[-1] for r in enriched}
    _is_dup = lambda r, country: (
        (r.get("_ticker","").split(":")[-1] if country=="US" else _bare_kr_code(r.get("_ticker","")))
        in (_high_tickers | _high_raw)
    )
    vol_us_e = [_vol_row(r, "US") for r in volume_us if not _is_dup(r, "US")]
    vol_kr_e = [_vol_row(r, "KR") for r in volume_kr if not _is_dup(r, "KR")]
    # 미국: TV 펀더멘털 헤더 / 한국: Naver 수급 플로우 헤더
    panels_html.append(_panel_wrap("vol_us", "거래량급증 미국",
                                   len(vol_us_e),
                                   _make_table_html(vol_us_e, US_VOLUME_HEADERS)))
    panels_html.append(_panel_wrap("vol_kr", "거래량급증 한국",
                                   len(vol_kr_e),
                                   _make_table_html(vol_kr_e, KR_VOLUME_HEADERS)))

    # 일별_트래킹
    panels_html.append(_panel_wrap("tracking", "일별 트래킹",
                                   len(by_tracking),
                                   _make_table_html(by_tracking, TRACKING_HEADERS)))

    return f'''<!DOCTYPE html>
<html lang="ko" data-t="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>52주 신고가 딥다이브 | {_esc(collected_at)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>{_HTML_CSS}</style>
</head>
<body>
<div class="topbar">
  <nav class="nav">{"".join(tab_buttons)}</nav>
  <div class="tt" id="dm-toggle" style="margin-left:0.4rem;">
    <span class="tt-label">🌙</span>
    <div class="tk"><div class="kn"></div></div>
  </div>
</div>
<main>{"".join(panels_html)}</main>
<script>{_HTML_JS}</script>
</body>
</html>'''


def write_html(html: str, output_path: Path):
    pending = output_path.with_name(output_path.stem + "_pending.html")
    pending.write_text(html, encoding="utf-8")
    try:
        pending.replace(output_path)
    except PermissionError:
        print(f"[HTML] 원자적 교체 실패. pending 파일 사용: {pending}")
        return pending
    return output_path


# ───────────────────────────────────────────────
# 섹션 8: main()
# ───────────────────────────────────────────────

def main():
    print(f"[딥다이브] 시작: {_NOW.strftime('%Y-%m-%d %H:%M:%S KST')}")
    collected_at = _NOW.strftime("%Y-%m-%d %H:%M KST")

    # 1. TradingView 수집
    print("[1] TradingView 52주 신고가 수집...")
    raw_us = fetch_tradingview_highs("america")
    raw_kr = fetch_tradingview_highs("korea")
    print(f"    US: {len(raw_us)}개, KR: {len(raw_kr)}개")

    for r in raw_us: r["_country"] = "US"
    for r in raw_kr: r["_country"] = "KR"
    all_raw = raw_us + raw_kr

    print("[2] TradingView 거래량 급증 수집...")
    vol_us = fetch_tradingview_volume_surge("america")
    vol_kr = fetch_tradingview_volume_surge("korea")
    print(f"    US: {len(vol_us)}개, KR: {len(vol_kr)}개")

    # 2a. Persistent universe — 최근 14일 추적 종목 중 오늘 신고가에 없는 것
    print("[2a] persistent universe 로드...")
    persistent_seed = load_persistent_universe(ENRICHED_HIGH_CSV, lookback_days=14)
    today_tickers = {r["_ticker"] for r in all_raw}
    missing_tk = [p["_ticker"] for p in persistent_seed
                  if p["_ticker"] not in today_tickers]
    if missing_tk:
        # TV에서 최신 시세·펀더멘털 재조회
        refreshed = fetch_tradingview_by_tickers(missing_tk)
        refresh_map = {r["_ticker"]: r for r in refreshed}
        for p in persistent_seed:
            tk = p["_ticker"]
            if tk in today_tickers:
                continue
            tv_r = refresh_map.get(tk)
            if not tv_r:
                continue
            tv_r["_country"] = p["_country"]
            tv_r["_persistent"] = True
            tv_r["_first_seen"] = p.get("_first_seen", "")
            all_raw.append(tv_r)
        print(f"    추가 추적: {len(missing_tk)}개 시도, "
              f"갱신 성공 {sum(1 for t in missing_tk if t in refresh_map)}개")

    # 2. 가격/시총 1차 필터
    def _pass_filter(r: dict) -> bool:
        # persistent 행은 가격필터 면제 (시총 떨어져도 추적 유지)
        if r.get("_persistent"):
            return True
        close  = _safe(r.get("close"), 0.0) or 0.0
        mktcap = _safe(r.get("market_cap_basic"), 0.0) or 0.0
        if r["_country"] == "US":
            return close >= MIN_PRICE_USD and mktcap >= MIN_MKTCAP_USD
        return close >= MIN_PRICE_KRW and mktcap >= MIN_MKTCAP_KRW

    filtered = [r for r in all_raw if _pass_filter(r)]
    us_tickers = [r["_ticker"] for r in filtered if r["_country"] == "US"]
    kr_codes   = [_bare_kr_code(r["_ticker"]) for r in filtered if r["_country"] == "KR"]
    # 거래량 급증 한국 종목도 이름 조회 대상에 포함
    vol_kr_codes = [_bare_kr_code(r.get("_ticker","")) for r in vol_kr]
    kr_codes_all = list(dict.fromkeys(kr_codes + [c for c in vol_kr_codes if c not in kr_codes]))
    print(f"[필터 후] US: {len(us_tickers)}개, KR: {len(kr_codes)}개")

    # 3. 병렬 외부 수집
    print("[3] yfinance / FnGuide / Naver 병렬 수집...")
    yf_data, flow_data = {}, {}

    # FnGuide는 행 내부에서 직접 처리
    def _run_fnguide():
        enrich_korean_rows_with_fnguide(filtered)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut_yf    = ex.submit(fetch_yfinance_batch, us_tickers)
        fut_fn    = ex.submit(_run_fnguide)
        fut_naver = ex.submit(fetch_krx_foreign_flow, kr_codes_all)
        yf_data   = fut_yf.result()
        fut_fn.result()
        flow_data = fut_naver.result()
    print(f"    yfinance: {len(yf_data)}개, Naver수급: {len(flow_data)}개")

    # 4. 수급 히스토리 로드
    print("[4] 수급 히스토리 로드...")
    first_seen = load_first_seen(ENRICHED_HIGH_CSV)

    # 5. enrich_row
    print("[5] enrich_row 처리...")
    enriched = []
    for r in filtered:
        row = enrich_row(r, yf_data, flow_data, first_seen)
        if row:
            enriched.append(row)
    print(f"    처리 완료: {len(enriched)}개")

    # 6. CSV 히스토리 저장
    print("[6] CSV 히스토리 저장...")
    update_daily_history(enriched, ENRICHED_HIGH_CSV)
    flow_recs = build_flow_history_records(enriched)
    save_flow_history(flow_recs, FLOW_HISTORY_CSV)

    # 7. 외부 데이터 병렬 수집 (13F / 시장 / F&G / 내부자 / 섹터 / 실적)
    print("[7] 외부 데이터 병렬 수집...")
    sec_rows, market_data, fg = [], [], {}
    insider_rows, sector_rows, earnings_rows = [], [], []

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        fut_13f      = ex.submit(fetch_famous_manager_rows)
        fut_market   = ex.submit(fetch_yahoo_market)
        fut_fg       = ex.submit(fetch_fear_greed)
        fut_insider  = ex.submit(fetch_insider_buys)
        fut_sector   = ex.submit(fetch_sector_performance)
        fut_earnings = ex.submit(fetch_earnings_calendar)

        def _safe_result(fut, name, timeout=60):
            try:
                return fut.result(timeout=timeout)
            except Exception as e:
                print(f"    {name} 오류: {e}")
                return None

        r13f      = _safe_result(fut_13f,      "13F",     300)
        r_market  = _safe_result(fut_market,   "시장지표",  30)
        r_fg      = _safe_result(fut_fg,       "Fear&Greed",20)
        r_insider = _safe_result(fut_insider,  "내부자거래", 30)
        r_sector  = _safe_result(fut_sector,   "섹터ETF",   60)
        r_earn    = _safe_result(fut_earnings, "실적캘린더", 60)

    if r13f      is not None: sec_rows     = r13f
    if r_market  is not None: market_data  = r_market
    if r_fg      is not None: fg           = r_fg
    if r_insider is not None: insider_rows = r_insider
    if r_sector  is not None: sector_rows  = r_sector
    if r_earn    is not None: earnings_rows = r_earn

    print(f"    13F:{len(sec_rows)} 시장:{len(market_data)} F&G:{fg.get('score','?')} "
          f"내부자:{len(insider_rows)} 섹터:{len(sector_rows)} 실적:{len(earnings_rows)}")

    # 13F 중복 보유 분석 + 히스토리 저장
    inst_overlap = fetch_institutional_overlap(sec_rows)
    if sec_rows:
        save_13f_history(sec_rows, DATA_DIR / "13f_history.csv")
    print(f"    기관중복보유: {len(inst_overlap)}종목")

    # 8. HTML 생성
    print("[8] index.html 생성...")
    html = generate_html(enriched, vol_us, vol_kr, sec_rows,
                         market_data, fg, insider_rows, inst_overlap,
                         sector_rows, earnings_rows, collected_at,
                         kr_names=flow_data)
    out  = write_html(html, OUTPUT_HTML)
    print(f"    저장: {out}")
    print(f"[딥다이브] 완료. 총 {len(enriched)}개 종목 | 파일: {out.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
