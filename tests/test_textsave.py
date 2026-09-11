"""--textsave (추출 본문 보존) — 설계: plan/추출텍스트-저장-설계-20260907.html

여기서 지키는 것은 크게 둘이다.
  · 저장은 **곁다리**다 — 되든 안 되든 분류 결과가 같아야 한다(P1·P2).
  · 무엇이 왜 빠졌는지 **알 수 있어야** 한다 — 조용한 실패를 만들지 않는다.
"""

import io
import json
import os

import pytest

from csoclassify import textsave


#------------------------------------------------------------------
# T1 — 파일 이름이 원본의 SHA-256 이다
#=> 이름이 레코드의 hash 칸과 같아야 "결과 한 줄에서 그 문서의 본문으로" 갈 수 있다.
#   이게 깨지면 저장은 되는데 되짚을 수가 없어(옛 __2.txt 문제) 쓸모가 없다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_파일명은_원본해시다(tmp_path):
    d = textsave.prepare(str(tmp_path / "out"))
    name, deduped = textsave.save(d, "a" * 64, "본문입니다")
    assert name == "a" * 64 + ".txt"
    assert deduped is False
    assert io.open(os.path.join(d, name), encoding="utf-8").read() == "본문입니다"


#------------------------------------------------------------------
# T2 — 내용이 같은 문서는 한 파일, 색인은 경로마다 한 줄
#=> 실제 폴더에는 같은 문서의 복사본이 많다(99_중복파일 폴더가 따로 있을 정도).
#   .txt 를 매번 새로 쓰면 디스크가 몇 배로 들고, 그렇다고 색인까지 한 줄로 줄이면
#   "그 본문이 어느 경로들에서 나왔는지"를 잃는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_같은_내용은_한_파일_색인은_두_줄(tmp_path):
    d = textsave.prepare(str(tmp_path / "out"))
    sha = "b" * 64
    n1, dedup1 = textsave.save(d, sha, "같은 본문")
    n2, dedup2 = textsave.save(d, sha, "같은 본문")
    assert (dedup1, dedup2) == (False, True)
    txts = [f for f in os.listdir(d) if f.endswith(".txt")]
    assert txts == [n1], txts

    for p in ("D:/a/문서.hwp", "D:/b/문서.hwp"):
        textsave.index_append(d, sha, n2, p, None, 5, False, "2026-09-07T00:00:00+09:00")
    rows = [json.loads(l) for l in
            io.open(os.path.join(d, textsave.INDEX_NAME), encoding="utf-8")]
    assert len(rows) == 2, rows
    assert [r["file"] for r in rows] == ["D:/a/문서.hwp", "D:/b/문서.hwp"]


#------------------------------------------------------------------
# T2b — 해시는 같은데 본문이 다르면 조용히 넘어가지 않는다
#=> 파일 이름은 '원본 파일'의 해시인데 저장하는 것은 '추출한 본문'이다.
#   파이썬 판과 Rust 판은 파서가 달라 같은 문서에서도 본문이 다르게 나온다
#   (실측: 같은 pptx 가 9,312 / 9,330 바이트). 그때 "이미 있으니 됐다"로 넘기면
#   남의 본문을 내 것인 양 가리키게 된다 — 가장 찾기 어려운 종류의 거짓말이다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_같은_해시_다른_본문은_오류로_알린다(tmp_path):
    d = textsave.prepare(str(tmp_path / "out"))
    sha = "c" * 64
    textsave.save(d, sha, "파이썬 판이 뽑은 본문")
    with pytest.raises(OSError) as e:
        textsave.save(d, sha, "Rust 판이 뽑은 본문")
    assert "다른 본문" in str(e.value)
    # 남의 파일을 덮어쓰지 않았는지까지 본다.
    assert io.open(os.path.join(d, sha + ".txt"), encoding="utf-8").read() \
        == "파이썬 판이 뽑은 본문"


#------------------------------------------------------------------
# T5 — 본문이 없으면 파일을 만들지 않는다
#=> 0바이트 파일을 남기면 "저장됐다"로 오해된다. 추출 실패(스캔본 등)와
#   "본문이 정말 비어 있음"은 결과에서 text_saved 키의 유무로 갈려야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_본문이_없으면_저장하지_않는다(tmp_path):
    from csoclassify.cli import _save_text_file
    d = textsave.prepare(str(tmp_path / "out"))

    rec = {"file": "a.pdf", "hash": "d" * 64}
    assert _save_text_file(d, rec, "", "a.pdf", False, "2026-09-07T00:00:00+09:00") == "skip"
    assert "text_saved" not in rec
    assert [f for f in os.listdir(d) if f.endswith(".txt")] == []

    # 해시가 없어도(=--textsave 없이 부른 경우) 이름을 지을 근거가 없으므로 건너뛴다.
    rec2 = {"file": "a.pdf"}
    assert _save_text_file(d, rec2, "본문", "a.pdf", False, "t") == "skip"


#------------------------------------------------------------------
# T3 — 옵션을 안 주면 아무것도 안 남긴다
#=> 기본이 꺼짐이어야 한다(P4). 본문에는 개인정보가 그대로 들어 있으므로,
#   명시적으로 켠 사람만 남겨야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_옵션이_없으면_저장하지_않는다():
    from csoclassify.cli import _save_text_file
    rec = {"file": "a.pdf", "hash": "e" * 64}
    assert _save_text_file(None, rec, "본문", "a.pdf", False, "t") == "skip"
    assert "text_saved" not in rec


#------------------------------------------------------------------
# T4 — 저장이 실패해도 분류 결과를 건드리지 않는다
#=> 저장은 곁다리다(P1·P2). 디스크가 차거나 잠겨도 grade 는 그대로 나와야 하고,
#   대신 무엇이 왜 빠졌는지는 반드시 남아야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_저장_실패는_레코드에_남고_등급은_그대로다(tmp_path, monkeypatch):
    from csoclassify import cli
    d = textsave.prepare(str(tmp_path / "out"))

    def boom(*a, **k):
        raise OSError("디스크 가득 참")

    monkeypatch.setattr(textsave, "save", boom)
    rec = {"file": "a.pdf", "hash": "f" * 64, "grade": "C"}
    state = cli._save_text_file(d, rec, "본문", "a.pdf", False, "t")
    assert state == "error"
    # 판정은 손대지 않는다.
    assert rec["grade"] == "C"
    # 그러나 조용하지도 않다.
    assert "디스크 가득 참" in rec["text_save_error"]
    assert "text_saved" not in rec


#------------------------------------------------------------------
# T6 — 절단된 본문은 색인에 그렇게 적힌다
#=> 저장하는 것은 '도구가 실제로 보고 판정한 텍스트'라 G3 로 잘린 문서는 뒤가 없다.
#   그 사실을 색인만 봐도 알아야, 나중에 본문을 읽는 사람이 "왜 여기서 끊기지"를
#   문서가 이상한 것으로 오해하지 않는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_절단본은_색인에_표시된다(tmp_path):
    from csoclassify.cli import _save_text_file
    d = textsave.prepare(str(tmp_path / "out"))
    rec = {"file": "big.pdf", "hash": "1" * 64}
    assert _save_text_file(d, rec, "앞부분만", "big.pdf", True, "2026-09-07T00:00:00+09:00") == "saved"
    row = json.loads(io.open(os.path.join(d, textsave.INDEX_NAME), encoding="utf-8").read())
    assert row["truncated"] is True
    assert row["chars"] == len("앞부분만")
    assert row["txt"] == rec["text_saved"]


#------------------------------------------------------------------
# T6b — 색인의 doc_id 는 sfile_id 로 얻은 것만 싣는다
#=> 폴백 doc_id 는 'hash 앞 40자'라 바로 옆 hash 칸과 같은 값이고, MpowerV11
#   문서 ID 처럼 생긴 값을 남기면 받는 쪽이 적재해 버린다(--simple 과 같은 규약).
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_색인_docid는_sfile_id만_싣는다(tmp_path):
    from csoclassify.cli import _save_text_file
    d = textsave.prepare(str(tmp_path / "out"))

    rec = {"file": "a.hwp", "hash": "2" * 64,
           "doc_id": "2" * 40, "doc_id_source": "content"}
    _save_text_file(d, rec, "본문", "a.hwp", False, "t")

    rec2 = {"file": "b.hwp", "hash": "3" * 64,
            "doc_id": "SF_1", "doc_id_source": "sfile_id"}
    _save_text_file(d, rec2, "본문2", "b.hwp", False, "t")

    rows = [json.loads(l) for l in
            io.open(os.path.join(d, textsave.INDEX_NAME), encoding="utf-8")]
    assert rows[0]["doc_id"] is None, rows[0]
    assert rows[1]["doc_id"] == "SF_1", rows[1]


#------------------------------------------------------------------
# 폴더 준비 — 안내문이 자동으로 생기고, 사람이 고친 것은 건드리지 않는다
#=> 폴더만 발견한 제3자에게 "여기 원문 발췌가 있다"가 닿아야 한다(설계 S4).
#   다만 이미 있는 안내문을 매번 덮으면, 사람이 적어 둔 주의사항이 지워진다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_안내문은_없을_때만_만든다(tmp_path):
    d = textsave.prepare(str(tmp_path / "out"))
    readme = os.path.join(d, textsave.README_NAME)
    assert os.path.exists(readme)
    assert "개인정보" in io.open(readme, encoding="utf-8").read()

    with io.open(readme, "w", encoding="utf-8", newline="") as f:
        f.write("우리 팀 규칙: 이 폴더는 매주 지운다")
    textsave.prepare(str(tmp_path / "out"))
    assert io.open(readme, encoding="utf-8").read().startswith("우리 팀 규칙")


#------------------------------------------------------------------
# 색인·안내문 이름은 .txt 가 아니다
#=> 받는 쪽이 폴더를 *.txt 로 훑을 때 색인이 문서인 척 섞여 들어가면 안 된다.
#   이름을 바꾸려는 사람에게 "왜 .txt 면 안 되는지"를 시험으로 남긴다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_보조파일은_txt가_아니다():
    assert not textsave.INDEX_NAME.endswith(".txt")
    assert not textsave.README_NAME.endswith(".txt")
