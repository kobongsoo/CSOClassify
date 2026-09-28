#------------------------------------------------------------------
# UI 데모용 샘플 grades.jsonl 생성기
#=> 실제 문서 없이도 분류 검토 UI 를 시연할 수 있게, C/S/O/보류 및 각 신호
#   (rule/sensitive/stamp/name/embed)를 골고루 담은 가짜 분류 레코드를 만든다.
#------------------------------------------------------------------

import json
import os

OUT = os.path.join(os.path.dirname(__file__), "sample_cso_result.jsonl")
V = "cso-2026.08.1"


#------------------------------------------------------------------
# 레코드 한 건 조립 도우미
#=> 공통 필드를 채운 분류 레코드 dict 를 만든다.
#
# -in: file/grade/conf/method/decided_by/signals/seed
# -out: dict = 분류 레코드
# -out: error = 없음
#------------------------------------------------------------------
def rec(file, grade, conf, method, decided_by, signals, seed=False):
    return {
        "file": file, "grade": grade, "confidence": conf, "method": method,
        "decided_by": decided_by, "seed_eligible": seed, "signals": signals,
        "rule_version": V, "ts": "2026-08-12T17:00:00+09:00",
    }


#------------------------------------------------------------------
# 내용 검사(rule) 신호 한 칸
#=> 본문에서 개인정보·기밀 키워드가 걸린 결과다. 확신값은 규칙 파일의
#   confidence.keyword(high) 와 같은 0.85 로 둔다.
#
# -in: grade = 이 신호가 매긴 등급(없으면 None — '안 걸림')
# -in: hits  = 걸린 항목 목록(id·name·layer·count·grade·terms)
#
# -out: dict = 신호 한 칸
# -out: error = 없음
#------------------------------------------------------------------
def rule_sig(grade=None, hits=None):
    return {"grade": grade, "confidence": 0.85 if grade else 0.0,
            "seed_eligible": bool(grade), "hits": hits or []}


#------------------------------------------------------------------
# 법상 민감정보군(sensitive) 신호 한 칸
#=> 건강·유전·노조 같은 법이 따로 정한 정보군이다. 예전 이 파일은 없어진
#   'path'(폴더 규칙) 신호를 만들고 있었다 — 2026-09-08 에 걷어낸 신호라
#   그대로 두면 화면이 설명하지 못하는 데모가 나온다(2026-09-28 교체).
#
# -in: grade = 이 신호가 매긴 등급(없으면 None)
# -in: hits  = 걸린 항목 목록(id·name·category·count·grade·terms)
#
# -out: dict = 신호 한 칸
# -out: error = 없음
#------------------------------------------------------------------
def sensitive_sig(grade=None, hits=None):
    return {"grade": grade, "confidence": 0.85 if grade else 0.0,
            "seed_eligible": False, "hits": hits or []}


#------------------------------------------------------------------
# 보안분류 스탬프(stamp) 신호 한 칸
#=> 문서에 찍힌 '대외비' 같은 표식. 오탐이 적어 확신값이 가장 높다(0.95).
#
# -in: grade = 이 신호가 매긴 등급(없으면 None)
# -in: hits  = 걸린 항목 목록(id·name·grade·mode·count·term)
#
# -out: dict = 신호 한 칸
# -out: error = 없음
#------------------------------------------------------------------
def stamp_sig(grade=None, hits=None):
    return {"grade": grade, "confidence": 0.95 if grade else 0.0,
            "seed_eligible": bool(grade), "hits": hits or []}


#------------------------------------------------------------------
# 파일 이름(name) 신호 한 칸
#=> 파일 이름에 등급을 가르는 말이 있는지. 힌트라 확신값이 고정 0.6 이다.
#
# -in: grade = 이 신호가 매긴 등급(없으면 None)
# -in: hits  = 걸린 항목 목록(id·name·grade·term)
#
# -out: dict = 신호 한 칸
# -out: error = 없음
#------------------------------------------------------------------
def name_sig(grade=None, hits=None):
    return {"grade": grade, "confidence": 0.6 if grade else 0.0,
            "seed_eligible": False, "hits": hits or []}


records = [
    # 1) 임직원 명부 — 주민번호 대량 → C
    rec(r"D:\collected\인사\임직원_명부_2026.docx", "C", 0.85, "fusion", ["rule"],
        {"rule": rule_sig("C", [
            {"id": "rrn", "name": "주민등록번호", "layer": "L1", "count": 5,
             "grade": "C", "terms": []},
            {"id": "phone_mobile", "name": "휴대전화번호", "layer": "L1", "count": 5,
             "grade": "S", "terms": []}]),
         "name": name_sig()}, seed=True),

    # 2) 대외비 보고서 — 기밀 표식 → C
    rec(r"D:\collected\기획\대외비_사업계획.hwp", "C", 0.85, "fusion", ["rule"],
        {"rule": rule_sig("C", [
            {"id": "mark_confidential", "name": "기밀 표식", "layer": "L2", "count": 3,
             "grade": "C", "terms": [{"term": "대외비", "count": 3}]}]),
         "name": name_sig("C", [
             {"id": "mark_confidential", "name": "기밀 표식", "grade": "C", "term": "대외비"}])}),

    # 3) 건강검진 대장 — 법상 민감정보군 → C
    rec(r"D:\collected\인사\건강검진_결과대장.xlsx", "C", 0.85, "fusion", ["sensitive"],
        {"rule": rule_sig(), "name": name_sig(),
         "sensitive": sensitive_sig("C", [
             {"id": "health", "name": "건강정보", "category": "건강", "grade": "C",
              "count": 6, "terms": [{"term": "진단명", "count": 3},
                                    {"term": "투약내역", "count": 2},
                                    {"term": "검진결과", "count": 1}]}])}),

    # 4) 이사회 회의록 — 문서 앞부분의 보안 표식 → C
    #    스탬프는 확신값이 가장 높아(0.95) 단독으로 등급을 정하는 일이 잦다.
    rec(r"D:\collected\경영\이사회_회의록_3차.hwp", "C", 0.95, "fusion", ["stamp"],
        {"rule": rule_sig(), "name": name_sig(),
         "stamp": stamp_sig("C", [
             {"id": "stamp_confidential", "name": "대외비/기밀 스탬프", "grade": "C",
              "mode": "head", "count": 1, "term": "대외비"}])}),

    # 5) 계약서 — 법무 키워드 → S
    rec(r"D:\collected\법무\용역계약서_한빛테크.docx", "S", 0.7, "fusion", ["rule", "name"],
        {"rule": rule_sig("S", [
            {"id": "legal_contract", "name": "법무·계약", "layer": "L2", "count": 4,
             "grade": "S", "terms": [{"term": "계약서", "count": 4}]}]),
         "name": name_sig("S", [
             {"id": "legal_contract", "name": "법무·계약", "grade": "S", "term": "계약서"}])}),

    # 6) 보류 — 아무 신호도 없음(None)
    rec(r"D:\collected\기타\기술노트.txt", None, 0.0, "unclassified", [],
        {"rule": rule_sig(), "name": name_sig()}),

    # 7) 임베딩 전파로 구제 — 마스킹본이 기밀 원본 이웃 → C
    rec(r"D:\collected\기타\명부_마스킹본.docx", "C", 0.78, "fusion", ["embed"],
        {"rule": rule_sig(), "name": name_sig(),
         "embed": {"grade": "C", "confidence": 0.78, "seed_eligible": False,
                   "method": "knn_vote", "top_sim": 0.622,
                   "neighbors": [
                       {"file": r"D:\collected\인사\임직원_명부_2026.docx", "grade": "C", "sim": 0.622},
                       {"file": r"D:\collected\기획\대외비_사업계획.hwp", "grade": "C", "sim": 0.51}]}}),

    # 8) 저신뢰 — 검토 큐용(신뢰도 낮음)
    rec(r"D:\collected\해군\소요제기서_초안.hwpx", "S", 0.45, "fusion", ["embed"],
        {"rule": rule_sig(), "name": name_sig(),
         "embed": {"grade": "S", "confidence": 0.45, "seed_eligible": False,
                   "method": "knn_vote", "top_sim": 0.58,
                   "neighbors": [{"file": r"D:\collected\인사\복리후생_안내.docx",
                                  "grade": "S", "sim": 0.58}]}}),
]

with open(OUT, "w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("생성:", OUT, f"({len(records)}건)")
