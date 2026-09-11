#------------------------------------------------------------------
# 기준 문서 CLI 등록(--seed-add) — 설계서 15장 T2~T6 · T14~T19
#=> 실제 문서 추출·임베딩은 여기서 다루지 않는다(cli.py 테스트 관례).
#   대신 이 옵션의 '계약'을 지킨다: 인자를 문서 읽기 전에 다 검사하는가,
#   doc_id 를 지어내지 않는가, 지문이 항상 들어가는가, 부분 실패가 숨지 않는가.
#
#   [왜 인자 검사를 따로 시험하나] 문서를 절반 읽고 나서 인자가 틀린 것을 알면
#   이미 쓴 것과 안 쓴 것이 섞여 되돌리기 어렵다. "읽기 전에 다 본다"가 계약이다.
#------------------------------------------------------------------

import json
from types import SimpleNamespace

from csoclassify import seedcli
from csoclassify import seedstore


#------------------------------------------------------------------
# 인자 묶음 만들기(시험용)
#=> argparse 결과를 흉내 낸다. 실제 파서를 태우지 않는 이유는, 여기서 보려는
#   것이 '파싱'이 아니라 '검사 규칙'이기 때문이다.
#
# -in: **kw = 덮어쓸 인자들
#
# -out: SimpleNamespace = build_plan 이 읽는 인자 묶음
# -out: error = 없음
#------------------------------------------------------------------
def mk(**kw):
    base = dict(seed_add=None, seed_add_from=None, seed_grade=None,
                seed_doctype=None, seed_reviewer="고봉수", seed_note="",
                seed_audit=None, seeds=None, file=None, dir=None)
    base.update(kw)
    return SimpleNamespace(**base)


#------------------------------------------------------------------
# 분류체계 대역 — get() 만 흉내 낸다
#
# -in: ids = 있는 dc_id 집합
# -out: 객체(get)
# -out: error = 없음
#------------------------------------------------------------------
def mk_tax(*ids):
    have = set(ids)
    return SimpleNamespace(get=lambda d: (object() if d in have else None))


# ── 인자 검사 (T4 · T5 · T15) ───────────────────────────────────

#------------------------------------------------------------------
# T4 — 검토자 없이 부르면 거절한다
#=> 출처 없는 기준 문서를 만들지 않는다는 것이 이 옵션의 첫 원칙(P2)이다.
#   기준 문서는 다른 문서의 등급을 정하는 잣대라, 누가 정했는지 모르면 안 된다.
#------------------------------------------------------------------
def test_검토자_없으면_거절한다():
    plan, err = seedcli.build_plan(mk(seed_add=["a.txt"], seed_grade="C",
                                      seed_reviewer=""))
    assert plan is None
    assert err[0] == "bad_args" and "seed-reviewer" in err[1]


#------------------------------------------------------------------
# T5 — 축을 하나도 안 주면 거절한다
#=> 등급도 분류도 없는 줄은 저장해도 기준으로 쓰이지 않는다. 성공한 척하고
#   아무 일도 안 하는 것이 가장 나쁘다.
#------------------------------------------------------------------
def test_축을_하나도_안_주면_거절한다():
    plan, err = seedcli.build_plan(mk(seed_add=["a.txt"]))
    assert plan is None and err[0] == "bad_args"
    assert "보안등급도 업무분류도 없습니다" in err[1]


#------------------------------------------------------------------
# 등급이 C·S·O 가 아니면 거절한다
#=> 소문자 c 나 오타를 그대로 저장하면 엔진이 그 줄을 조용히 건너뛴다
#   (등급 검사가 "C"/"S"/"O" 만 통과시키므로) — 씨앗이 말없이 사라진다.
#------------------------------------------------------------------
def test_이상한_등급은_거절한다():
    plan, err = seedcli.build_plan(mk(seed_add=["a.txt"], seed_grade="c"))
    assert plan is None and err[0] == "bad_args"


#------------------------------------------------------------------
# T15 — 두 모드를 함께 주면 거절한다
#=> "목록에도 있고 인자에도 있는데 값이 다르면 무엇이 이기나"를 사람이 매번
#   되짚어야 하는 상태를 만들지 않는다.
#------------------------------------------------------------------
def test_두_모드를_함께_주면_거절한다():
    plan, err = seedcli.build_plan(mk(seed_add=["a.txt"], seed_add_from="l.jsonl",
                                      seed_grade="C"))
    assert plan is None and err[0] == "mode_conflict"


#------------------------------------------------------------------
# --file/--dir 과 함께 쓰면 거절한다
#=> 분류 모드와 등록 모드가 섞이면 무엇이 결과인지 알 수 없다.
#------------------------------------------------------------------
def test_분류_모드와_함께_쓰면_거절한다():
    plan, err = seedcli.build_plan(mk(seed_add=["a.txt"], seed_grade="C",
                                      dir="D:/문서"))
    assert plan is None and err[0] == "mode_conflict"


# ── 목록 입력 (T14 · T18 · T19) ─────────────────────────────────

#------------------------------------------------------------------
# T14 — 목록은 문서마다 다른 값을 얹는다
#=> 이 옵션의 존재 이유다. 나란한 목록(--seed-grade C,S,S)을 쓰지 않는 것은
#   개수가 어긋나도 성공한 것처럼 보이기 때문이다(설계 7-1).
#------------------------------------------------------------------
def test_목록은_문서마다_다른_값을_얹는다(tmp_path):
    p = tmp_path / "list.jsonl"
    p.write_text(
        json.dumps({"file": "a.txt", "grade": "C",
                    "doctype": ["DC_1", "DC_2"], "note": "가"}) + "\n" +
        json.dumps({"file": "b.txt", "grade": "S"}) + "\n" +
        json.dumps({"file": "c.txt", "doctype": "DC_3"}) + "\n",
        encoding="utf-8")

    plan, err = seedcli.build_plan(mk(seed_add_from=str(p), seed_note="기본메모"))
    assert err is None
    assert [x["file"] for x in plan] == ["a.txt", "b.txt", "c.txt"]
    assert plan[0]["grade"] == "C" and plan[0]["doctype"] == ["DC_1", "DC_2"]
    assert plan[0]["note"] == "가"
    # 메모를 안 준 줄은 --seed-note 를 물려받는다.
    assert plan[1]["note"] == "기본메모"
    # 문자열 하나를 줘도 배열로 만든다 — 엔진은 배열만 읽는다.
    assert plan[2]["doctype"] == ["DC_3"] and plan[2]["grade"] is None
    # 검토자는 줄마다 안 적어도 --seed-reviewer 를 물려받는다.
    assert all(x["reviewer"] == "고봉수" for x in plan)


#------------------------------------------------------------------
# 목록에 모르는 칸이 있으면 거절한다
#=> 조용히 무시하면 grade 를 grades 로 잘못 적은 것 하나로 축이 통째로 빠진 채
#   등록되고, 부르는 쪽은 "성공 1건"만 본다.
#------------------------------------------------------------------
def test_목록의_모르는_칸은_거절한다(tmp_path):
    p = tmp_path / "list.jsonl"
    p.write_text(json.dumps({"file": "a.txt", "grades": "C"}) + "\n",
                 encoding="utf-8")
    plan, err = seedcli.build_plan(mk(seed_add_from=str(p)))
    assert plan is None and err[0] == "bad_args"
    assert "grades" in err[1]


#------------------------------------------------------------------
# 목록의 깨진 줄은 건너뛰지 않고 실패시킨다
#=> 등록은 '몇 건을 넣었나'가 계약인데, 조용히 건너뛰면 부르는 쪽이 몇 건이
#   빠졌는지 알 수 없다.
#------------------------------------------------------------------
def test_목록의_깨진_줄은_실패시킨다(tmp_path):
    p = tmp_path / "list.jsonl"
    p.write_text('{"file":"a.txt","grade":"C"}\n{깨짐\n', encoding="utf-8")
    plan, err = seedcli.build_plan(mk(seed_add_from=str(p)))
    assert plan is None and err[0] == "bad_args" and "2번째" in err[1]


#------------------------------------------------------------------
# T19 — 같은 doc_id 를 두 문서에 주면 거절한다
#=> 같은 신분증을 두 문서에 붙이면 받는 쪽에서 한 문서가 다른 문서를 덮는다.
#------------------------------------------------------------------
def test_같은_doc_id_를_두_문서에_주면_거절한다(tmp_path):
    p = tmp_path / "list.jsonl"
    p.write_text(
        json.dumps({"file": "a.txt", "doc_id": "SF-1", "grade": "C"}) + "\n" +
        json.dumps({"file": "b.txt", "doc_id": "SF-1", "grade": "S"}) + "\n",
        encoding="utf-8")
    plan, err = seedcli.build_plan(mk(seed_add_from=str(p)))
    assert plan is None and err[0] == "bad_args" and "SF-1" in err[1]


#------------------------------------------------------------------
# T6 — 분류체계에 없는 dc_id 는 거절한다
#=> 없는 분류로 씨앗을 만들면 전파가 조용히 헛돈다(붙을 라벨이 없다).
#------------------------------------------------------------------
def test_없는_분류코드는_거절한다():
    args = mk(seed_add=["a.txt"], seed_doctype="DC_1,DC_없음")
    plan, err = seedcli.build_plan(args, tax=mk_tax("DC_1"))
    assert plan is None and err[0] == "bad_args" and "DC_없음" in err[1]
    # 체계를 모르면 막지 않는다(--taxonomy 없이 도는 배포가 있다).
    plan, err = seedcli.build_plan(args, tax=None)
    assert err is None and plan[0]["doctype"] == ["DC_1", "DC_없음"]


# ── 줄 만들기 (T16 · T18) ───────────────────────────────────────

#------------------------------------------------------------------
# 분류 레코드 대역
#
# -in: file/hash/vector/doc_id/doc_id_source = 레코드에 담을 값
# -out: dict
# -out: error = 없음
#------------------------------------------------------------------
def rec(file, hash="h1", vector=(1.0, 0.0), doc_id=None, doc_id_source=None):
    r = {"file": file, "hash": hash, "vector": list(vector)}
    if doc_id_source:
        r["doc_id"] = doc_id
        r["doc_id_source"] = doc_id_source
    return r


#------------------------------------------------------------------
# T16 — 지문(hash)이 항상 들어간다
#=> v3 는 '원본이 바뀌었나'를 hash 하나로만 판정한다. 지문 없이 등록된 줄은
#   원본이 아무리 바뀌어도 경고 한 줄 없이 옛 벡터로 계속 전파한다.
#------------------------------------------------------------------
def test_지문과_판이름이_들어간다():
    plan = [{"file": "a.txt", "doc_id": None, "grade": "C", "doctype": [],
             "note": "", "reviewer": "고봉수"}]
    rows, added, failed = seedcli.build_rows(plan, [rec("a.txt")], [])
    assert not failed and len(added) == 1
    r = rows[0]
    assert r["hash"] == "h1"
    assert r["grade"] == "C"
    assert r["source"] == "cli"
    assert r["approved_by"] == "고봉수"
    assert r["engine"] == seedstore.ENGINE     # 어느 판이 만든 벡터인가
    assert r["dim"] == 2


#------------------------------------------------------------------
# T18 — doc_id 를 지어내지 않는다
#=> 폴백 doc_id 는 '파일 해시 앞 40자'라 이미 hash 칸에 있고, 그 값을 실으면
#   외부 문서 ID 처럼 생긴 값이 흘러가 같은 문서가 두 건으로 들어간다.
#   그래서 목록이 준 값이나 sfile_id 만 쓰고, 없으면 칸 자체를 안 만든다.
#------------------------------------------------------------------
def test_doc_id_를_지어내지_않는다():
    plan = [{"file": "a.txt", "doc_id": None, "grade": "C", "doctype": [],
             "note": "", "reviewer": "x"}]

    # ① 아무 데서도 못 얻으면 칸이 없다.
    rows, _, _ = seedcli.build_rows(plan, [rec("a.txt")], [])
    assert "doc_id" not in rows[0]

    # ② 폴백(content)으로 채워진 값은 쓰지 않는다 — hash 와 같은 값이라서다.
    rows, _, _ = seedcli.build_rows(
        plan, [rec("a.txt", doc_id="h1"[:40], doc_id_source="content")], [])
    assert "doc_id" not in rows[0]

    # ③ sfile_id 로 채워진 값은 쓴다 — 외부 문서와 이어지는 진짜 신분증이다.
    rows, _, _ = seedcli.build_rows(
        plan, [rec("a.txt", doc_id="SF-9", doc_id_source="sfile_id")], [])
    assert rows[0]["doc_id"] == "SF-9"

    # ④ 목록이 준 값이 가장 세다 — 부르는 쪽(웹)이 아는 것이 더 정확하다.
    plan2 = [dict(plan[0], doc_id="SF-웹")]
    rows, _, _ = seedcli.build_rows(
        plan2, [rec("a.txt", doc_id="SF-9", doc_id_source="sfile_id")], [])
    assert rows[0]["doc_id"] == "SF-웹"


#------------------------------------------------------------------
# T3 — 기존 줄을 지우지 않고 병합한다
#=> class_seed.jsonl 은 두 축이 나눠 쓰는 파일이다. 등록 한 번에 남의 줄이
#   사라지면 그 문서들의 전파가 조용히 죽는다.
#------------------------------------------------------------------
def test_기존_줄을_지우지_않는다():
    before = seedstore.add_seed([], "old.txt", "S", [0.0, 1.0], "이영희")
    plan = [{"file": "new.txt", "doc_id": None, "grade": "C", "doctype": [],
             "note": "", "reviewer": "고봉수"}]
    rows, added, failed = seedcli.build_rows(plan, [rec("new.txt")], before)
    assert len(rows) == 2
    assert {r["file"] for r in rows} == {"old.txt", "new.txt"}
    old = next(r for r in rows if r["file"] == "old.txt")
    assert old["grade"] == "S" and old["approved_by"] == "이영희"


#------------------------------------------------------------------
# T7 — 벡터를 못 얻은 문서는 실패로 세고, 나머지는 등록한다
#=> 부분 실패가 숨으면 안 된다(P4). 벡터 없는 기준 문서는 비교 대상이 못 되므로
#   저장할 이유가 없다 — 화면과 같은 판단이다(설계 Q3).
#------------------------------------------------------------------
def test_벡터를_못_얻으면_그_건만_실패로_센다():
    plan = [{"file": "ok.txt", "doc_id": None, "grade": "C", "doctype": [],
             "note": "", "reviewer": "x"},
            {"file": "bad.txt", "doc_id": None, "grade": "C", "doctype": [],
             "note": "", "reviewer": "x"},
            {"file": "gone.txt", "doc_id": None, "grade": "C", "doctype": [],
             "note": "", "reviewer": "x"}]
    records = [rec("ok.txt"),
               {"file": "bad.txt", "hash": "h2", "vector": None,
                "error": {"kind": "extract_failed"}}]
    rows, added, failed = seedcli.build_rows(plan, records, [])
    assert [a["file"] for a in added] == ["ok.txt"]
    assert [f["file"] for f in failed] == ["bad.txt", "gone.txt"]
    assert failed[0]["why"] == "extract_failed"     # 왜 빠졌는지 말해 준다
    assert len(rows) == 1


#------------------------------------------------------------------
# 감사 기록 경로 — --seeds 와 같은 폴더가 기본이다
#=> 화면의 기본값과 다를 수 있어(화면은 ui/ 아래를 쓴다) 실행 요약에 찍는다.
#   --seed-audit 으로 명시하면 그것이 이긴다.
#------------------------------------------------------------------
def test_감사_기록_경로(tmp_path):
    seeds = str(tmp_path / "policy" / "class_seed.jsonl")
    assert seedcli.audit_path(mk(seeds=seeds)).endswith("class_seed_audit.jsonl")
    assert "policy" in seedcli.audit_path(mk(seeds=seeds))
    # 명시하면 그것을 쓴다.
    assert seedcli.audit_path(mk(seeds=seeds, seed_audit="D:/a.jsonl")) == "D:/a.jsonl"
    # stdout 모드는 파일을 안 만지므로 감사 기록도 없다.
    assert seedcli.audit_path(mk()) is None


#------------------------------------------------------------------
# 목록의 '#' 주석 줄은 건너뛴다 (--filelist 와 같은 규칙)
#=> 목록에 "무엇을 적어야 하는지"를 사람 말로 적어 둘 수 있어야 한다. --filelist 는
#   이미 그렇게 하는데 --seed-add-from 만 안 되면, 같은 모양의 목록인데 한쪽만
#   주석이 되어 쓰는 사람이 매번 어느 쪽인지 되짚어야 한다.
#   판정은 filelist._is_comment 를 그대로 쓴다 — 복사해 두면 한쪽만 고쳐져 갈린다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 주석에서 멈추면 AssertionError
#------------------------------------------------------------------
def test_목록의_주석줄은_건너뛴다(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('# 이 목록은 2026-09 등록분입니다' + chr(10) +
                 '#' + chr(10) +
                 '## 굵은 구분선' + chr(10) +
                 '{"file": "a.txt", "grade": "C"}' + chr(10),
                 encoding="utf-8")
    plan, err = seedcli.build_plan(mk(seed_add_from=str(p)))
    assert err is None, err
    assert len(plan) == 1 and plan[0]["file"] == "a.txt"


#------------------------------------------------------------------
# '#' 로 시작하는 경로는 주석이 아니다 (오탐 방지)
#=> 이 저장소 샘플에도 '#외부유출금지#' 같은 폴더가 있다. '#' 하나로 잘라 내면
#   그런 문서가 조용히 빠진다. '#' 뒤에 공백·'#' 이 오거나 줄이 끝날 때만 주석이다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 경로가 주석으로 먹히면 AssertionError
#------------------------------------------------------------------
def test_샵으로_시작하는_경로는_주석이_아니다(tmp_path):
    p = tmp_path / "l.jsonl"
    # JSON 객체 줄이라 '{' 로 시작한다 — 경로 자체에 '#' 이 들어 있는 경우.
    p.write_text('{"file": "#외부유출금지#/b.docx", "grade": "S"}' + chr(10),
                 encoding="utf-8")
    plan, err = seedcli.build_plan(mk(seed_add_from=str(p)))
    assert err is None, err
    assert plan[0]["file"] == "#외부유출금지#/b.docx"


#------------------------------------------------------------------
# 주석을 건너뛰어도 오류의 줄 번호는 실제 줄 번호다
#=> "N 번째 줄"은 사람이 파일을 열어 눈으로 세는 번호여야 한다. 건너뛴 줄을 빼고
#   세면 편집기에서 그 줄을 찾을 수 없다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(단언)
# -out: error = 번호가 어긋나면 AssertionError
#------------------------------------------------------------------
def test_주석을_건너뛰어도_줄번호는_실제줄(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('# 주석' + chr(10) + '#' + chr(10) + '## 두번' + chr(10) +
                 '{"file": "a.txt", "grade": "C"}' + chr(10) +
                 '깨진줄' + chr(10), encoding="utf-8")
    plan, err = seedcli.build_plan(mk(seed_add_from=str(p)))
    assert plan is None and err is not None
    assert "5번째 줄" in err[1], err[1]
