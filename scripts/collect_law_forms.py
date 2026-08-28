#------------------------------------------------------------------
# 업종 사전 씨앗 수집기 — 국가법령정보 별표·서식 목록에서 문서 이름 긁어오기
#=> 업종별 유의어 사전(doc_synonyms.<업종>.yaml)을 사람이 상상해서 적으면
#   실제로 안 쓰는 말이 섞이고 정작 쓰는 말은 빠진다. 그래서 "그 업종의 공식
#   문서 이름 목록" 을 먼저 구해 놓고 시작한다.
#
#   [왜 하필 법령 별표·서식인가]
#     「의료법 시행규칙 별지 제○호서식」 같은 서식 이름이 곧 그 업종에서 실제로
#     오가는 문서 이름이다. 진단서·소견서·처방전·개설허가증 … 사람이 앉아서
#     떠올리는 것보다 정확하고, 법제처가 공개 API 로 열어 두었다.
#
#   [무엇을 만들어 내는가]
#     1) forms_<업종>.tsv        — 긁어온 서식 이름 원본과 정규화 결과(사람이 확인용)
#     2) doc_synonyms.<업종>.seed.yaml — 업종 사전 초안(빈 유의어 칸 + 빈도 주석)
#     이 초안은 그대로 쓰는 물건이 아니다. 유의어 칸을 채우는 것이 다음 단계다.
#
#   [공통 사전에 이미 있는 말은 빼고 준다]
#     doc_synonyms.core.yaml 의 꼬리말 그물이 이미 잡아 주는 이름까지 업종 사전에
#     넣으면 사전만 두꺼워지고 얻는 것이 없다. 그래서 '아직 못 잡는 것' 만 남긴다.
#
#   [쓰는 법]
#     python scripts/collect_law_forms.py --industry medical --oc test
#     python scripts/collect_law_forms.py --query 금융,여신,보험 --name finance
#
#   [OC 값] 법제처가 내주는 인증값이다(open.law.go.kr 에서 신청). 시험용으로
#   'test' 가 열려 있으나 건수 제한이 있으므로, 실제 수집은 발급받아 쓴다.
#------------------------------------------------------------------

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "http://www.law.go.kr/DRF/lawSearch.do"
CORE = os.path.join(ROOT, "resources", "policy", "doc_synonyms.core.yaml")

#------------------------------------------------------------------
# 업종별 법령명 검색어
#=> API 의 search=2 는 '관련 법령명' 으로 검색한다. 그래서 업종을 고르는 가장
#   정확한 방법은 소관부처 코드가 아니라 그 업종을 규율하는 법령의 이름이다
#   (실측: 소관부처 코드는 부처 개편 때마다 어긋나고, 같은 부처가 여러 업종을
#    맡기도 한다. 법령명은 그런 흔들림이 없다).
#------------------------------------------------------------------
INDUSTRY_QUERIES = OrderedDict([
    ("finance", ["금융", "여신", "보험", "자본시장", "은행", "신용정보", "자산운용"]),
    ("medical", ["의료", "약사", "감염병", "건강보험", "의료기기", "정신건강", "혈액관리"]),
    ("legal", ["변호사", "공증", "민사소송", "형사소송", "등기", "공탁", "법무사"]),
    ("education", ["교육", "학교", "학원", "유아교육", "평생교육", "학위"]),
    ("public", ["행정", "공공기관", "지방자치", "기록물", "정보공개", "민원"]),
    ("construction", ["건설", "건축", "주택", "국토", "산업안전보건", "시설물"]),
    ("manufacturing", ["산업", "제품안전", "품질경영", "계량", "화학물질", "환경"]),
])

#------------------------------------------------------------------
# 문서 이름의 꼬리말 후보
#=> 서식 이름의 끝에 붙는 말들이다. 이름 전체가 아니라 이 꼬리말을 봐야
#   "공통 사전의 그물이 이미 잡아 주는가" 를 판정할 수 있다.
#   (공통 사전의 tails 키를 그대로 쓰되, 거기에 없는 관공서 특유의 꼬리말을
#    몇 개 더한다 — 신고필증·확인증처럼 사내 문서에는 없는 말들이다.)
#------------------------------------------------------------------
EXTRA_SUFFIXES = (
    "신고필증", "허가증", "등록증", "확인증", "증명서", "지정서", "통지서", "통보서",
    "명령서", "처분서", "결정서", "재결서", "고지서", "납부서", "청구서", "접수증",
    "카드", "일람표", "명세표", "조사표", "집계표", "관리표", "기록부", "관리부",
    "서식", "조서", "각서", "원부", "초본", "등본", "사본",
)


#------------------------------------------------------------------
# 공통 사전 읽기
#=> 업종 사전에 무엇을 남길지 정하려면 공통 사전이 이미 무엇을 잡는지 알아야 한다.
#   파일이 없으면 '아무것도 못 잡는다' 로 보고 전부 후보로 남긴다(안전한 쪽).
#
# -in: path = doc_synonyms.core.yaml 경로
#
# -out: (tails, aliases) = 꼬리말 집합, 통째 별칭 이름 집합
# -out: error = 파일 없음·파싱 실패 시 (빈 집합, 빈 집합)
#------------------------------------------------------------------
def load_core(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return set(), set()
    return set(data.get("tails") or {}), set(data.get("aliases") or {})


#------------------------------------------------------------------
# 법제처 API 한 페이지 부르기
#=> 실패하면 잠깐 쉬었다 다시 부른다. 공개 API 라 순간적으로 막히는 일이 흔한데,
#   한 페이지 실패로 수집을 통째로 버리면 수천 건을 다시 받아야 한다.
#
# -in: oc     = 법제처 인증값
# -in: query  = 검색어(법령명)
# -in: page   = 페이지 번호(1부터)
# -in: tries  = 재시도 횟수(기본 3)
#
# -out: dict = 응답의 licBylSearch 부분(실패하면 빈 dict)
# -out: error = 3번 다 실패하면 {} 를 돌려주고 호출한 쪽이 건너뛴다(예외 없음)
#------------------------------------------------------------------
def fetch_page(oc, query, page, tries=3):
    params = dict(OC=oc, target="licbyl", type="JSON", search=2,
                  query=query, display=100, page=page)
    url = API + "?" + urllib.parse.urlencode(params)
    for n in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            return json.loads(body).get("licBylSearch") or {}
        except Exception:
            # 뒤로 갈수록 더 오래 쉰다 — 막힌 직후에 바로 또 부르면 같이 막힌다.
            time.sleep(1.5 * (n + 1))
    return {}


#------------------------------------------------------------------
# 검색어 하나로 서식 이름 전부 긁어오기
#=> 한 페이지 100건씩 끝까지 넘긴다. API 가 알려 주는 총 건수(totalCnt)를 채우면
#   멈춘다. 총 건수를 못 받으면 빈 페이지가 나올 때까지만 간다.
#
#   [별표는 버리고 서식만 남긴다] '별표' 는 기준표·요율표라 문서 이름이 아니다.
#   우리가 찾는 것은 실제로 오가는 문서, 즉 '서식' 이다.
#
# -in: oc    = 법제처 인증값
# -in: query = 검색어(법령명)
# -in: cap   = 최대 페이지 수(기본 30 = 3000건). 폭주 방지용
#
# -out: list = [{"별표명","관련법령명","소관부처명"}…]
# -out: error = 없음(못 받은 페이지는 조용히 건너뛴다)
#------------------------------------------------------------------
def collect_query(oc, query, cap=30):
    out = []
    total = None
    for page in range(1, cap + 1):
        body = fetch_page(oc, query, page)
        if not body:
            break
        if total is None:
            try:
                total = int(body.get("totalCnt") or 0)
            except ValueError:
                total = 0
        items = body.get("licbyl") or []
        # display=1 이거나 결과가 한 건이면 API 가 리스트가 아니라 dict 를 준다.
        # 이걸 안 맞춰 주면 dict 를 순회해 열쇠 문자열이 섞여 들어온다.
        if isinstance(items, dict):
            items = [items]
        if not items:
            break
        for it in items:
            if str(it.get("별표종류") or "").strip() != "서식":
                continue
            out.append({
                "별표명": str(it.get("별표명") or "").strip(),
                "관련법령명": str(it.get("관련법령명") or "").strip(),
                "소관부처명": str(it.get("소관부처명") or "").strip(),
            })
        if total and page * 100 >= total:
            break
        # 공개 API 예의. 이걸 빼면 몇 페이지 못 가서 막힌다.
        time.sleep(0.4)
    return out


#------------------------------------------------------------------
# 서식 이름에서 군더더기 걷어내기
#=> 법령 서식 이름은 실제 문서 이름 앞뒤에 조문 참조와 단서가 잔뜩 붙어 있다.
#     "(권역응급의료센터, 전문응급의료센터) (재)지정서"  → "지정서"
#     "요양급여비용 청구서(제19조제1항 관련)"            → "요양급여비용 청구서"
#   괄호 안은 거의 전부 군더더기라 통째로 걷어낸다.
#
# -in: name = 별표명 원본
#
# -out: str = 정리된 이름(전부 군더더기였으면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def clean_name(name):
    s = str(name or "")
    # 괄호가 겹쳐 있는 경우가 있어 더 이상 줄지 않을 때까지 반복한다.
    for _ in range(5):
        new = re.sub(r"[(\[{（【〔][^()\[\]{}（）【】〔〕]*[)\]}）】〕]", " ", s)
        if new == s:
            break
        s = new
    # "별지 제3호서식", "제19조제1항 관련" 같은 조문 표기는 문서 이름이 아니다.
    s = re.sub(r"별지\s*제?\s*[0-9가-힣의]+\s*호?\s*서?식?", " ", s)
    s = re.sub(r"제\s*[0-9]+\s*(조|항|호)[의0-9]*", " ", s)
    s = re.sub(r"[·ㆍ‧∙]", " ", s)
    s = re.sub(r"[^가-힣A-Za-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


#------------------------------------------------------------------
# 문서 이름의 끝 글자
#=> "무엇무엇서/증/부/표" 처럼 한국 문서 이름은 끝 한 글자만 봐도 문서인지
#   아닌지 거의 갈린다. 아직 모르는 꼬리말(처방전·소견서 …)을 새로 발견하려면
#   '아는 꼬리말 목록' 대신 이 글자로 1차를 걸러야 한다.
#------------------------------------------------------------------
DOC_LAST_CHARS = frozenset("서증부표장전록지안문첩권")

# YAML 초안을 줄 단위로 이어 붙일 때 쓴다.
NEWLINE = chr(10)


#------------------------------------------------------------------
# 정리된 이름에서 '문서 유형' 만 뽑기
#=> 우리가 사전에 넣고 싶은 것은 "요양급여비용 청구서" 전체가 아니라 그 문서가
#   무슨 종류인지를 말하는 마지막 덩어리다. 앞쪽은 그 업종 안에서도 건건이
#   달라서 사전에 넣으면 한 건짜리 규칙이 된다.
#    1) 뒤에서부터 아는 꼬리말이 붙어 있는지 본다 → 있으면 그것으로 확정
#    2) 없으면 문서 끝 글자(서·증·부 …)로 한 번 더 본다 — 이 갈래가 중요하다.
#       "처방전"·"소견서" 처럼 우리가 아직 모르는 문서 유형이 여기서 나온다.
#       모르는 것을 버리면 새 업종을 만날 때마다 아무것도 못 배운다
#    3) 둘 다 아니면 문서 이름이 아니라고 보고 버린다(신청인·별지 등)
#
# -in: cleaned  = clean_name() 결과
# -in: suffixes = 아는 꼬리말 집합(공통 사전 tails + 관공서 특유 꼬리말)
#
# -out: (유형, 꼬리말, 아는것) = 예: ("품목허가증", "허가증", True)
#                                 모르는 것이면 ("처방전", None, False)
#                                 못 뽑으면 (None, None, False)
# -out: error = 없음
#------------------------------------------------------------------
def extract_type(cleaned, suffixes):
    if not cleaned:
        return None, None, False
    words = cleaned.split()
    if not words:
        return None, None, False
    last = words[-1]
    # 마지막 어절이 "서식"·"등" 처럼 그 자체로는 뜻이 없으면 한 칸 앞을 본다.
    if last in ("서식", "등", "및", "안", "표") and len(words) > 1:
        last = words[-2]
    # 긴 꼬리말이 먼저 걸려야 한다("결과보고서" 가 "보고서" 보다 정확하다).
    for suf in sorted(suffixes, key=len, reverse=True):
        if last.endswith(suf):
            # 꼬리말만 남는 이름(그냥 "신청서")도 문서 유형으로 쓸모가 있다.
            return last, suf, True
    # 아는 꼬리말이 없다 — 새 꼬리말일 수 있으니 버리지 말고 표시해 둔다.
    if 2 <= len(last) <= 12 and last[-1] in DOC_LAST_CHARS:
        return last, None, False
    return None, None, False


#------------------------------------------------------------------
# 수집한 이름들에서 새 꼬리말 찾아내기
#=> "처방전"·"신고서"·"허가증" 이 여러 이름의 끝에 반복해서 나온다면, 그 업종에는
#   공통 사전이 모르는 꼬리말이 있다는 뜻이다. 이걸 찾아내는 것이 이 도구의
#   가장 값어치 있는 산출물이다 — 꼬리말 하나가 이름 수십 개를 한꺼번에 잡는다.
#
#   [아무 데서나 잘라 내면 안 된다]
#     그냥 뒤 2~4글자를 세면 "보고서"·"통고서" 에서 "고서" 가, "검사반원증" 에서
#     "사반원증" 이 나온다. 한국어에 그런 말은 없다. 실제로 이렇게 만들어 보니
#     후보 23개 중 16개가 이런 조각이었다.
#   [그래서 두 가지를 동시에 만족해야 꼬리말로 본다]
#     ① 그 말이 수집 결과에 '홀로' 문서 이름으로 나온 적이 있다
#        ("신고서" 라는 서식이 실제로 있다 / "고서" 라는 서식은 없다)
#     ② 서로 다른 이름 여러 개의 끝에 붙어 있다(한 이름에만 붙으면 그냥 그 이름이다)
#
# -in: 유형들  = Counter({문서유형: 빈도}) — 수집해서 뽑아낸 문서 유형 전부
# -in: core_t  = 공통 사전이 이미 아는 꼬리말 집합(여기 있으면 새것이 아니다)
# -in: min_kind= 몇 종의 이름 끝에 붙어야 꼬리말로 볼지(기본 2)
#
# -out: list = [(꼬리말, 종수, 건수)…] 종수 내림차순, 같으면 긴 것 먼저
# -out: error = 없음
#------------------------------------------------------------------
def discover_tails(유형들, core_t, min_kind=2):
    홀로 = {t for t in 유형들
            if t not in core_t and 2 <= len(t) <= 6
            # 공통 사전 그물이 이미 잡는 말은 제안하지 않는다. '관리대장' 은
            # core 의 '대장' 이 이미 받아 주므로 또 넣어 봐야 사전만 두꺼워진다.
            and not any(t.endswith(c) and t != c for c in core_t)}
    kinds, counts = Counter(), Counter()
    for 유형, cnt in 유형들.items():
        for suf in 홀로:
            # 자기 자신은 세지 않는다 — "신고서" 가 "신고서" 의 끝에 붙었다는
            # 것은 아무 정보도 아니다. 다른 이름을 잡아 줄 때만 꼬리말이다.
            if 유형 != suf and 유형.endswith(suf):
                kinds[suf] += 1
                counts[suf] += cnt
    out = [(suf, k, counts[suf]) for suf, k in kinds.items() if k >= min_kind]
    # 긴 꼬리말이 짧은 것보다 위로 오게 한다 — 사전에 그대로 옮겨 적기 좋게.
    out.sort(key=lambda x: (-x[1], -len(x[0])))
    return out


#------------------------------------------------------------------
# 수집 결과를 표(TSV)로 저장
#=> 사람이 눈으로 훑어보는 용도다. 자동 추출이 엉뚱한 것을 문서 유형으로 잡았을 때
#   원본과 나란히 놓고 봐야 어디서 틀렸는지 알 수 있다.
#
# -in: rows = [(별표명, 정리된이름, 유형, 꼬리말, 관련법령명, 소관부처명)…]
# -in: path = 저장 경로
#
# -out: 없음(파일을 쓴다)
# -out: error = 쓰기 실패 시 예외 전파(수집을 통째로 날리지 않으려면 호출부가 잡는다)
#------------------------------------------------------------------
def save_tsv(rows, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("별표명\t정리된이름\t문서유형\t꼬리말\t관련법령명\t소관부처명\n")
        for r in rows:
            f.write("\t".join("" if v is None else str(v) for v in r) + "\n")


#------------------------------------------------------------------
# 업종 사전 초안(seed) 만들기
#=> 수집 결과를 사전 모양으로 세워 준다. 유의어 칸은 비워 둔다 — 이 파일은
#   그대로 쓰는 물건이 아니라 다음 단계(유의어 채우기)의 입력이다. 그래서 각 줄
#   위에 "몇 건에서 나왔고 근거 서식이 무엇인지" 를 주석으로 남긴다. 유의어를
#   채우는 사람(또는 모델)이 근거를 보고 판단할 수 있어야 하기 때문이다.
#
#   [초안은 두 덩어리다 — 이게 이 도구의 핵심이다]
#     tails   : 그 업종에서 자주 쓰는데 공통 사전이 못 잡는 꼬리말
#               (의료의 '지정서'·'통보서'·'허가증' …) — 이름 하나가 아니라
#               이름 수십 개를 한꺼번에 잡아 주므로 값어치가 가장 크다
#     aliases : 꼬리말로는 안 잡히는, 그 업종 고유의 문서 이름 자체
#
#   [한 건짜리는 버린다] 법령 서식에는 그 법에만 있는 일회성 이름이 많다
#   ("의료급여증기재사항변경통보서"). 사전에 넣어도 한 건짜리 규칙이 될 뿐이라
#   최소 건수에 못 미치면 뺀다.
#
#   [공통 사전이 이미 잡는 것은 뺀다] 꼬리말이 공통 사전 tails 에 있으면 그물이
#   이미 잡아 주므로 업종 사전에 또 넣을 이유가 없다.
#
# -in: types    = Counter({문서유형: 빈도}) — 아는 꼬리말로 잡힌 것
# -in: unknown  = Counter({문서유형: 빈도}) — 아는 꼬리말이 없던 것
# -in: 예시     = {문서유형: [근거 서식명…]}
# -in: core_t   = 공통 사전 꼬리말 집합
# -in: core_a   = 공통 사전 통째 별칭 이름 집합
# -in: industry = 업종 이름(파일 머리말에 적는다)
# -in: min_count= 초안에 남길 최소 건수(기본 2)
# -in: top      = aliases 에 남길 최대 개수(기본 120)
#
# -out: str = YAML 텍스트
# -out: error = 없음
#------------------------------------------------------------------
def build_seed(types, unknown, 예시, core_t, core_a, industry,
               min_count=2, top=120):
    합 = Counter(types)
    합.update(unknown)
    # 아는 꼬리말로 잡힌 것까지 함께 본다. EXTRA_SUFFIXES 는 이 도구가 쓰는
    # 임시 목록일 뿐 공통 사전에 있는 말이 아니라서, 거기서도 새 꼬리말이 나온다
    # (의료의 '허가증'·'신고증'이 그랬다).
    새꼬리 = discover_tails(합, core_t, min_kind=2)
    걸러진 = []
    for 유형, cnt in 합.most_common():
        if cnt < min_count:
            break                         # most_common 은 내림차순이라 여기서 끝
        if 유형 in core_a or 유형 in core_t:
            continue                      # 공통 사전에 이미 있다
        if any(유형.endswith(t) and 유형 != t for t in core_t):
            continue                      # 공통 사전 그물이 이미 잡는다
        if any(유형.endswith(x) for x, _, _ in 새꼬리):
            continue                      # 위 tails 가 잡아 준다(자기 자신 포함)
        걸러진.append((유형, cnt))
        if len(걸러진) >= top:
            break

    out = [
        "# " + "-" * 66,
        "# 업무분류 유의어 사전 — ② 업종 겹(%s) · 초안" % industry,
        "#  · 만든 방법: 국가법령정보 별표·서식 목록에서 자동 수집(collect_law_forms.py)",
        "#  · 상태: **초안이다.** 유의어 칸이 비어 있으면 아무 일도 하지 않는다.",
        "#",
        "#  [다음에 할 일]",
        "#   1) tails 의 각 꼬리말에 '같은 뜻 다른 꼬리말' 을 채운다",
        "#      (예: 지정서 → [지정통보서, 지정확인서])",
        "#   2) aliases 의 각 이름에 '같은 뜻 다른 말' 을 채운다",
        "#   채울 때는 공통 사전(doc_synonyms.core.yaml)의 규칙을 그대로 따른다 —",
        "#   좁은 말부터, 넓은 말은 filename_only 로, 오탐이 뻔한 말은 excludes 로.",
        "#",
        "#  [여기 없는 이름] 공통 사전의 꼬리말 그물이 이미 잡아 주는 이름과,",
        "#  %d건 미만으로 나온 일회성 이름은 일부러 뺐다." % min_count,
        "# " + "-" * 66,
        "version: seed",
        "layer: %s" % industry,
        "",
        "# 이 업종에서 자주 쓰는데 공통 사전이 못 잡는 꼬리말 —",
        "# 이름 하나가 아니라 이름 여러 개를 한꺼번에 잡아 준다. 여기부터 채운다.",
        "tails:",
    ]
    if not 새꼬리:
        out.append("  {}")
    for suf, kind, cnt in 새꼬리:
        out.append("  # %d종 %d건" % (kind, cnt))
        out.append("  %s: []" % suf)

    out += ["",
            "# 꼬리말로는 안 잡히는, 이 업종 고유의 문서 이름",
            "aliases:"]
    if not 걸러진:
        out.append("  {}")
    for 유형, cnt in 걸러진:
        근거 = (예시.get(유형) or [])[:2]
        out.append("  # %d건 · 예: %s" % (cnt, " / ".join(근거)))
        out.append("  %s: []" % 유형)
    out += ["", "filename_only: {}", "excludes: {}"]
    return NEWLINE.join(out) + NEWLINE


#------------------------------------------------------------------
# 명령줄 진입점
#=> 업종 하나를 골라 수집 → 정리 → 초안 생성까지 한 번에 한다.
#
# -in: argv = 명령줄 인자(None 이면 sys.argv)
#
# -out: int = 종료 코드(0 정상, 1 수집 실패)
# -out: error = 네트워크 실패는 안에서 삼키고, 한 건도 못 받으면 1 을 돌려준다
#------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="법령 별표·서식에서 업종 사전 씨앗을 수집한다")
    ap.add_argument("--industry", choices=list(INDUSTRY_QUERIES),
                    help="미리 정해 둔 업종(검색어 묶음)")
    ap.add_argument("--query", help="직접 지정할 법령명 검색어(쉼표로 여러 개)")
    ap.add_argument("--name", help="--query 를 쓸 때 붙일 업종 이름")
    ap.add_argument("--oc", default=os.environ.get("LAW_OC", "test"),
                    help="법제처 인증값(기본: 환경변수 LAW_OC 또는 test)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "law_seed"),
                    help="결과를 저장할 폴더")
    ap.add_argument("--cap", type=int, default=30, help="검색어당 최대 페이지 수")
    ap.add_argument("--from-tsv", dest="from_tsv",
                    help="이미 받아 둔 forms_*.tsv 로 초안만 다시 만든다(수집 생략)")
    ap.add_argument("--min-count", type=int, default=2, dest="min_count",
                    help="초안에 남길 최소 건수(기본 2 — 한 건짜리 일회성 이름 제외)")
    args = ap.parse_args(argv)

    if args.query:
        industry = args.name or "custom"
        queries = [q.strip() for q in args.query.split(",") if q.strip()]
    elif args.industry:
        industry = args.industry
        queries = INDUSTRY_QUERIES[args.industry]
    else:
        ap.error("--industry 또는 --query 중 하나는 있어야 한다")

    core_t, core_a = load_core(CORE)
    suffixes = set(core_t) | set(EXTRA_SUFFIXES)

    본 = []
    seen = set()
    if args.from_tsv:
        # 초안 만드는 규칙을 손볼 때마다 수천 건을 다시 받을 이유가 없다.
        # 이미 받아 둔 표에서 다시 만든다(원본 별표명만 있으면 충분하다).
        with open(args.from_tsv, encoding="utf-8") as f:
            next(f, None)
            for line in f:
                col = line.rstrip("\n").split("\t")
                if len(col) >= 6 and col[0]:
                    본.append({"별표명": col[0], "관련법령명": col[4],
                               "소관부처명": col[5]})
        print("  표에서 다시 읽음: %d건" % len(본))
    for q in (queries if not args.from_tsv else []):
        got = collect_query(args.oc, q, cap=args.cap)
        print("  %-12s %4d건" % (q, len(got)))
        for it in got:
            key = (it["별표명"], it["관련법령명"])
            # 같은 서식이 검색어 여러 개에 걸리는 일이 흔하다. 빈도가 부풀면
            # 초안 순서가 틀어지므로 여기서 한 번만 세도록 걸러 둔다.
            if key in seen:
                continue
            seen.add(key)
            본.append(it)

    if not 본:
        print("한 건도 받지 못했다. OC 값(--oc)과 네트워크를 확인한다.", file=sys.stderr)
        return 1

    rows, types, unknown, 예시 = [], Counter(), Counter(), {}
    for it in 본:
        cleaned = clean_name(it["별표명"])
        유형, 꼬리, 아는것 = extract_type(cleaned, suffixes)
        rows.append((it["별표명"], cleaned, 유형, 꼬리 or "(모름)",
                     it["관련법령명"], it["소관부처명"]))
        if not 유형:
            continue
        # 아는 꼬리말로 잡힌 것과 아닌 것을 갈라 센다. 모르는 쪽에서만
        # 새 꼬리말을 캐낼 수 있어서(discover_tails) 섞으면 안 된다.
        (types if 아는것 else unknown)[유형] += 1
        예시.setdefault(유형, []).append(it["별표명"][:40])

    os.makedirs(args.out, exist_ok=True)
    tsv = os.path.join(args.out, "forms_%s.tsv" % industry)
    seed = os.path.join(args.out, "doc_synonyms.%s.seed.yaml" % industry)
    save_tsv(rows, tsv)
    with open(seed, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_seed(types, unknown, 예시, core_t, core_a, industry,
                           min_count=args.min_count))

    전체 = sum(types.values()) + sum(unknown.values())
    덮임 = sum(c for t, c in types.items()
              if t in core_a or t in core_t
              or any(t.endswith(x) and t != x for x in core_t))
    합계 = Counter(types)
    합계.update(unknown)
    새꼬리 = discover_tails(합계, core_t)
    print("\n서식 %d건 · 문서유형 %d종(아는 꼬리말 %d · 모르는 것 %d)"
          % (len(본), len(types) + len(unknown), len(types), len(unknown)))
    print("  공통 사전이 이미 잡는 것: %d건(%.0f%%)"
          % (덮임, 100.0 * 덮임 / max(1, 전체)))
    if 새꼬리:
        print("  새로 찾은 꼬리말 %d개: %s"
              % (len(새꼬리), ", ".join(x for x, _, _ in 새꼬리[:12])))
    print("  표  : %s" % tsv)
    print("  초안: %s" % seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
