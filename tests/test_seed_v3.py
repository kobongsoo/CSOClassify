#------------------------------------------------------------------
# 기준 문서 저장소 스키마 v3(평탄화) — 이행 계약 테스트
#=> 설계서 plan/기준문서-seed저장소-v3평탄화-설계-20260908.html 의 T1·T4~T7.
#   v2 → v3 는 "같은 값을 다른 모양으로 적는" 변경이므로, 여기서 검사할 것은
#   딱 하나다 — 옮기는 과정에서 기준 문서가 하나라도 사라지거나 값이
#   바뀌지 않았는가. 거버넌스 도구에서 "무엇이 왜 빠졌는지 알 수 없는 것"이
#   가장 나쁜 실패이므로, 빠지는 경우마다 셀 수 있는 흔적을 남기는지도 본다.
#------------------------------------------------------------------

import os
import sys
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ui"))

from csoclassify import seedstore as S                                       # noqa: E402


#------------------------------------------------------------------
# v2 한 줄 만들기(시험용)
#=> 실제 운영 파일과 같은 모양 — axes 가 진실이고 grade/labels 가 파생값,
#   원본 지문은 doc 안에 3칸, 임베딩 정보는 embed 안에 있다.
#
# -in: state = 두 축에 함께 줄 상태값(기본 active)
#
# -out: dict = v2 모양의 한 줄
# -out: error = 없음
#------------------------------------------------------------------
def v2_row(state="active"):
    return {
        "v": 2, "file": "d:/sample/a.doc", "grade": "C",
        "labels": {"security": "C", "doctype": ["DC_1", "DC_2"]},
        "axes": {
            "security": {"state": state, "value": "C", "source": "phase4",
                         "approved_by": "고봉수", "ts": "2026-08-28T13:50:19+09:00",
                         "note": ""},
            "doctype": {"state": state, "value": ["DC_1", "DC_2"], "source": "phase6",
                        "approved_by": "고봉수", "ts": "2026-08-28T13:50:20+09:00",
                        "note": "본보기"},
        },
        "doc": {"hash": "aa11", "size": 38400, "mtime": "2022-12-22T12:10:36+09:00"},
        "embed": {"dim": 3, "ts": "2026-08-28T13:50:19+09:00"},
        "approved_by": "고봉수", "ts": "2026-08-28T13:50:19+09:00",
        "vector": [0.1, 0.2, 0.3],
    }


#------------------------------------------------------------------
# T1 — v2 를 v3 로 올려도 판정에 쓰이는 값이 그대로다
#=> 엔진이 읽는 칸은 grade 와 doctype 둘뿐이다(설계서 3장). 이 둘과 원본
#   지문(hash)이 그대로면 분류 결과는 바뀔 수 없다.
#------------------------------------------------------------------
def test_v2를_v3로_올려도_판정값이_같다():
    n = S.ensure_v3(v2_row())
    assert n["v"] == 3
    assert n["grade"] == "C"
    assert n["doctype"] == ["DC_1", "DC_2"]
    assert n["hash"] == "aa11"
    assert n["vector"] == [0.1, 0.2, 0.3]
    assert n["dim"] == 3
    # 중첩 칸은 남지 않는다 — 남으면 "어느 쪽이 진실인가"가 다시 생긴다.
    for gone in ("axes", "labels", "doc", "embed"):
        assert gone not in n
    # size·mtime 은 읽는 코드가 없어 뺐다(설계서 5장).
    assert "size" not in n and "mtime" not in n


#------------------------------------------------------------------
# 축마다 다른 출처·메모는 줄 단위 한 칸으로 합친다
#=> 출처는 실제로 갈리므로(phase4 / phase6) 이어 붙여 뜻을 지키고,
#   메모는 비어 있지 않은 것 중 가장 긴 것을 남긴다.
#------------------------------------------------------------------
def test_축별_출처와_메모가_합쳐진다():
    n = S.ensure_v3(v2_row())
    assert n["source"] == "phase4+phase6"
    assert n["note"] == "본보기"
    assert n["approved_by"] == "고봉수"
    assert n["ts"] == "2026-08-28T13:50:20+09:00"   # 가장 늦은 시각


#------------------------------------------------------------------
# v2 의 retired 축은 버린다 — v3 에서 해제는 곧 삭제다
#=> v2 는 값을 남겨 두는 척했지만 파생값이 지워져 엔진이 못 봤고, 두 축을
#   모두 해제하면 줄째 사라졌으며, 재등록도 막지 못했다. 이전 값은
#   class_seed_audit.jsonl 이 갖고 있다.
#------------------------------------------------------------------
def test_v2_retired_축은_버려진다():
    n = S.ensure_v3(v2_row(state="retired"))
    assert "grade" not in n and "doctype" not in n
    assert not S.keepable_axes(n)      # 남길 이유가 없는 줄이다


#------------------------------------------------------------------
# T4 — 보류(hold)된 축은 엔진에 안 보이지만 값은 남고 사유가 세어진다
#=> 이것이 v3 에서 가장 조심해야 할 자리다. 값 칸을 비우는 방식이라
#   "보류"와 "원래 없음"이 파일에서 같은 모양이 된다. 그래서 점검이
#   반드시 세어야 한다 — 세지 않으면 왜 안 먹는지 아무도 알 수 없다.
#------------------------------------------------------------------
def test_보류된_축은_안_쓰이되_사유와_함께_세어진다(tmp_path):
    seeds = S.add_seed([], "a.doc", "C", [1.0, 0.0], "고봉수", doc={"hash": "aa"})
    seeds = S.add_doctype_seed(seeds, "a.doc", ["DC_1"], [1.0, 0.0], "고봉수")
    seeds = S.suspect_axis(seeds, "a.doc", "security", "stale")
    e = seeds[0]

    assert "grade" not in e                                  # 엔진이 못 본다
    assert S.axis_state(e, "security") == S.STATE_SUSPECT
    assert S.axis_value(e, "security") == "C"                # 값은 남아 있다
    assert S.hold_reason(e, "security") == "stale"
    assert S.active_axes(e) == ("doctype",)                  # 다른 축은 산다
    assert S.keepable_axes(e) == ("security", "doctype")     # 줄은 남긴다

    rep = S.check_seeds(seeds)
    assert {"file": "a.doc", "axis": "security", "reason": "stale"} in rep["suspect"]
    assert S.axis_counts(seeds)["suspect"] == 1

    # 저장하고 다시 읽어도 보류 상태와 사유가 그대로다.
    p = str(tmp_path / "class_seed.jsonl")
    assert S.save_seeds(p, seeds) == 1
    back = S.load_seeds(p)
    assert S.hold_reason(back[0], "security") == "stale"
    assert "grade" not in back[0]


#------------------------------------------------------------------
# T5 — 사람이 확인하면 보류가 풀리고 값이 제자리로 돌아온다
#=> 시스템은 표시만 하고 되살리는 것은 사람이 한다는 원칙을 검사한다.
#------------------------------------------------------------------
def test_보류를_풀면_값이_제자리로_돌아온다():
    seeds = S.add_seed([], "a.doc", "C", [1.0, 0.0], "고봉수")
    seeds = S.suspect_axis(seeds, "a.doc", "security", "stale")
    seeds = S.restore_axis(seeds, "a.doc", "security")
    assert seeds[0]["grade"] == "C"
    assert not (seeds[0].get("hold") or {})
    assert S.axis_state(seeds[0], "security") == S.STATE_ACTIVE


#------------------------------------------------------------------
# 사람이 다시 확정해도 보류가 풀린다
#=> 원본을 다시 읽어 갱신하는 경로다. 여기서 hold 가 남으면 값과 보류가
#   함께 있는 앞뒤 안 맞는 줄이 된다.
#------------------------------------------------------------------
def test_다시_확정하면_보류가_풀린다():
    seeds = S.add_seed([], "a.doc", "C", [1.0, 0.0], "고봉수")
    seeds = S.suspect_axis(seeds, "a.doc", "security", "stale")
    seeds = S.add_seed(seeds, "a.doc", "S", [1.0, 0.0], "이영희")
    assert seeds[0]["grade"] == "S"
    assert "hold" not in seeds[0]


#------------------------------------------------------------------
# T6 — 기준으로 쓸 것이 하나도 없는 줄은 파일에 쓰지 않는다
#=> 벡터만 있고 등급도 분류코드도 없는 줄은 전파에 쓸 수 없다. 남겨 두면
#   점검 화면의 숫자만 부풀린다.
#------------------------------------------------------------------
def test_두_축이_모두_없는_줄은_저장되지_않는다(tmp_path):
    p = str(tmp_path / "class_seed.jsonl")
    seeds = [{"v": 3, "file": "a.doc", "vector": [1.0], "approved_by": "x"},
             {"v": 3, "file": "b.doc", "grade": "C", "vector": [1.0]}]
    assert S.save_seeds(p, seeds) == 1
    back = S.load_seeds(p)
    assert [x["file"] for x in back] == ["b.doc"]


#------------------------------------------------------------------
# T7 — 모르는 미래 판은 건너뛰되 조용히 넘어가지 않는다
#=> 앞으로 v4 를 쓰는 판이 나와 같은 파일을 쓸 수 있다. 이 코드는 v4 의
#   뜻을 모르므로 추측해서 읽으면 안 된다. 다만 그냥 건너뛰기만 하면
#   기준 문서가 소리 없이 줄어든다 — 몇 줄을 왜 건너뛰었는지 남긴다.
#------------------------------------------------------------------
def test_모르는_미래판은_건너뛰고_흔적을_남긴다(tmp_path):
    p = str(tmp_path / "class_seed.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"v": 3, "file": "a.doc", "grade": "C",
                            "vector": [1.0]}) + "\n")
        f.write(json.dumps({"v": 9, "file": "b.doc", "grade": "S"}) + "\n")
        f.write("{깨진 줄\n")

    back = S.load_seeds(p)
    assert [x["file"] for x in back] == ["a.doc"]
    whys = sorted(x["why"] for x in S.SKIPPED)
    assert whys == ["broken", "v9"]
    assert any(x.get("file") == "b.doc" for x in S.SKIPPED)


#------------------------------------------------------------------
# v1(가장 옛 판) 줄도 읽는다
#=> --make-doctype-seeds 가 예전에 쓰던 모양이다. axes 도 v 도 없이
#   labels.doctype 만 있는 줄인데, 이것을 못 읽으면 옛 배포판이 만든
#   기준 문서가 통째로 사라진다.
#------------------------------------------------------------------
def test_v1_옛_줄도_읽는다():
    n = S.ensure_v3({"file": "c.doc", "labels": {"doctype": ["DC_9"]},
                     "vector": [0.3]})
    assert n["v"] == 3
    assert n["doctype"] == ["DC_9"]
    assert n["dim"] == 1
    n2 = S.ensure_v3({"file": "d.doc", "grade": "S", "vector": [0.4]})
    assert n2["grade"] == "S"


#------------------------------------------------------------------
# 원본이 바뀐 것은 해시로만 판정한다
#=> v3 에서 size·mtime 을 뺐어도 stale 판정이 그대로 된다는 확인이다.
#   설계서 5장의 근거가 실제로 성립하는지 여기서 잡는다.
#------------------------------------------------------------------
def test_원본_변경은_해시로_판정한다(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("hello", encoding="utf-8")
    f = str(real)
    seeds = S.add_seed([], f, "C", [1.0, 0.0], "고봉수", doc={"hash": "aa"})

    rep = S.check_seeds(seeds, records=[{"file": f, "hash": "zz"}])
    assert rep["stale"] == [{"file": f, "before": "aa", "after": "zz"}]

    out, ev = S.sync_with_records(seeds, [{"file": f, "hash": "zz"}])
    assert [e["action"] for e in ev] == ["suspect"]
    assert S.hold_reason(out[0], "security") == "stale"

    # 원본이 되돌아오면 표시가 풀린다.
    out2, ev2 = S.sync_with_records(out, [{"file": f, "hash": "aa"}])
    assert [e["action"] for e in ev2] == ["restore"]
    assert out2[0]["grade"] == "C"


#------------------------------------------------------------------
# 엔진이 읽는 모양 그대로인지 — Python 전파 인덱스로 확인
#=> 설계서 11장. seedstore 가 쓴 파일을 분류 엔진이 실제로 읽어 내는지가
#   이 이행의 마지막 관문이다. 한쪽만 고치면 기준 문서가 조용히 사라진다.
#------------------------------------------------------------------
def test_엔진이_v3_파일을_읽는다(tmp_path):
    sys.path.insert(0, os.path.join(_HERE, "..", "src"))
    from csoclassify.classify.propagate import SeedIndex, DoctypeSeedIndex

    p = str(tmp_path / "class_seed.jsonl")
    seeds = S.add_seed([], "a.doc", "C", [1.0, 0.0, 0.0], "고봉수")
    seeds = S.add_doctype_seed(seeds, "a.doc", ["DC_1"], [1.0, 0.0, 0.0], "고봉수")
    S.save_seeds(p, seeds)

    assert SeedIndex.from_seed_file(p).size == 1
    assert DoctypeSeedIndex.from_seed_file(p).size == 1

    # 옛 파일(v1)도 그대로 읽힌다 — 배포판이 여러 벌 도는 동안 둘 다 돈다.
    p2 = str(tmp_path / "old.jsonl")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(json.dumps({"file": "b.doc", "grade": "S",
                            "labels": {"doctype": ["DC_2"]},
                            "vector": [0.0, 1.0, 0.0]}) + "\n")
    assert SeedIndex.from_seed_file(p2).size == 1
    assert DoctypeSeedIndex.from_seed_file(p2).size == 1


#------------------------------------------------------------------
# 시각 표기는 두 판이 같은 모양이어야 한다 <중요>
#=> 2026-09-10 에 ISO-8601("2026-09-08T17:10:56+09:00")에서 사람이 읽는
#   모양("2026-09-08 17:10:56")으로 바꿨다. 한 파일에 두 도구가 쓰는데 한쪽만
#   바꾸면 표기가 섞여 사람이 정렬해 볼 수 없다 — 그런데 이건 오류를 내지
#   않고 조용히 어긋나므로, 모양 자체를 시험으로 못박는다.
#
#   [왜 정렬까지 보나] 이 값을 파싱하는 코드는 없지만 max()/sort 로 '가장 늦은
#   시각'을 고르는 곳이 있다(ensure_v3). 새 모양이 문자열 정렬로도 시간 순서를
#   지키는지 확인해 둔다.
#------------------------------------------------------------------
def test_시각_표기가_정해진_모양이다():
    import re
    ts = S.now_iso()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", ts), ts
    # 'T' 나 시간대 오프셋이 다시 들어오면 잡는다.
    assert "T" not in ts and "+" not in ts

    # 문자열로 정렬해도 시간 순서가 유지된다(ensure_v3 의 max 가 이것에 기댄다).
    assert "2026-09-08 09:00:00" < "2026-09-08 17:10:56"
    assert "2026-09-08 23:59:59" < "2026-09-09 00:00:00"


#------------------------------------------------------------------
# 옛 표기가 섞인 파일도 읽힌다
#=> 2026-09-10 이전에 만든 파일에는 "…T…+09:00" 모양이 그대로 남아 있다.
#   읽다가 깨지면 그때 등록한 기준 문서가 통째로 사라지므로, 섞여 있어도
#   값을 그대로 실어 나르는지 확인한다(형식을 바꿔 쓰지도, 버리지도 않는다).
#------------------------------------------------------------------
def test_옛_시각_표기가_섞여도_읽힌다():
    old = "2026-08-28T13:50:19+09:00"
    n = S.ensure_v3({"v": 3, "file": "a.doc", "grade": "C", "ts": old,
                     "vector": [0.1]})
    assert n["ts"] == old        # 옛 값을 건드리지 않는다
