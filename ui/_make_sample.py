#------------------------------------------------------------------
# UI 데모용 샘플 grades.jsonl 생성기
#=> 실제 문서 없이도 분류 검토 UI 를 시연할 수 있게, C/S/O/보류 및 각 신호
#   (rule/path/name/embed)를 골고루 담은 가짜 분류 레코드를 만든다.
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


def rule_sig(grade=None, hits=None):
    return {"grade": grade, "confidence": 0.85 if grade else 0.0,
            "seed_eligible": bool(grade), "hits": hits or []}


def path_sig(grade=None, source=None, acl=False):
    return {"grade": grade, "confidence": 0.9 if grade else 0.0,
            "seed_eligible": bool(grade), "acl_restricted": acl, "source": source}


def name_sig(grade=None, hits=None):
    return {"grade": grade, "confidence": 0.6 if grade else 0.0,
            "seed_eligible": False, "hits": hits or []}


records = [
    # 1) 임직원 명부 — 주민번호 대량 → C
    rec(r"D:\collected\인사\임직원_명부_2026.docx", "C", 0.85, "fusion", ["rule"],
        {"rule": rule_sig("C", [
            {"id": "rrn", "name": "주민등록번호", "layer": "L1", "count": 5,
             "validated": 4, "grade": "C", "terms": []},
            {"id": "phone_mobile", "name": "휴대전화번호", "layer": "L1", "count": 5,
             "validated": 0, "grade": "S", "terms": []}]),
         "path": path_sig("S", "hr_finance_dir"), "name": name_sig()}, seed=True),

    # 2) 대외비 보고서 — 기밀 표식 → C
    rec(r"D:\collected\기획\대외비_사업계획.hwp", "C", 0.85, "fusion", ["rule"],
        {"rule": rule_sig("C", [
            {"id": "mark_confidential", "name": "기밀 표식", "layer": "L2", "count": 3,
             "validated": 0, "grade": "C", "terms": [{"term": "대외비", "count": 3}]}]),
         "path": path_sig(), "name": name_sig("C", [
             {"id": "mark_confidential", "name": "기밀 표식", "grade": "C", "term": "대외비"}])}),

    # 3) 인사 폴더 문서 — 경로로 S
    rec(r"D:\collected\인사\복리후생_안내.docx", "S", 0.9, "fusion", ["path"],
        {"rule": rule_sig(), "path": path_sig("S", "hr_finance_dir"), "name": name_sig()}),

    # 4) 공개 공지 — 경로로 O
    rec(r"D:\collected\public\채용공고.pdf", "O", 0.9, "fusion", ["path"],
        {"rule": rule_sig(), "path": path_sig("O", "public_channel"), "name": name_sig()}),

    # 5) 계약서 — 법무 키워드 → S
    rec(r"D:\collected\법무\용역계약서_한빛테크.docx", "S", 0.7, "fusion", ["rule", "name"],
        {"rule": rule_sig("S", [
            {"id": "legal_contract", "name": "법무·계약", "layer": "L2", "count": 4,
             "validated": 0, "grade": "S", "terms": [{"term": "계약서", "count": 4}]}]),
         "path": path_sig(), "name": name_sig("S", [
             {"id": "legal_contract", "name": "법무·계약", "grade": "S", "term": "계약서"}])}),

    # 6) 보류 — 아무 신호도 없음(None)
    rec(r"D:\collected\기타\기술노트.txt", None, 0.0, "unclassified", [],
        {"rule": rule_sig(), "path": path_sig(), "name": name_sig()}),

    # 7) 임베딩 전파로 구제 — 마스킹본이 기밀 원본 이웃 → C
    rec(r"D:\collected\기타\명부_마스킹본.docx", "C", 0.78, "fusion", ["embed"],
        {"rule": rule_sig(), "path": path_sig(), "name": name_sig(),
         "embed": {"grade": "C", "confidence": 0.78, "seed_eligible": False,
                   "method": "knn_vote", "top_sim": 0.622,
                   "neighbors": [
                       {"file": r"D:\collected\인사\임직원_명부_2026.docx", "grade": "C", "sim": 0.622},
                       {"file": r"D:\collected\기획\대외비_사업계획.hwp", "grade": "C", "sim": 0.51}]}}),

    # 8) 저신뢰 — 검토 큐용(신뢰도 낮음)
    rec(r"D:\collected\해군\소요제기서_초안.hwpx", "S", 0.45, "fusion", ["embed"],
        {"rule": rule_sig(), "path": path_sig(), "name": name_sig(),
         "embed": {"grade": "S", "confidence": 0.45, "seed_eligible": False,
                   "method": "knn_vote", "top_sim": 0.58,
                   "neighbors": [{"file": r"D:\collected\인사\복리후생_안내.docx",
                                  "grade": "S", "sim": 0.58}]}}),
]

with open(OUT, "w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("생성:", OUT, f"({len(records)}건)")
