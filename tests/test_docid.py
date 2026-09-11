# -*- coding: utf-8 -*-
"""문서 식별자(doc_id) · 입력 목록(--filelist) — 설계 §7-5 · §7-5-2-1 · §7-5-3.

이 시험들이 지키는 것은 하나다 — **사람이 고친 등급이 조용히 사라지지 않는 것**.
저장소 실측에서 드라이브 문자 대소문자 차이 하나로 17건 중 4건이 반영되지 않고
있었고, 아무 오류도 나지 않아 알아채기 어려웠다.
"""
import io
import json
import os
import pathlib
import sys

import pytest

from csoclassify import filelist as F

# UI 는 빌드된 exe 를 상대로 도는 별도 프로세스라 패키지를 import 하지 않는다.
# 그래서 경로 정규화가 한 벌 더 있고, 두 벌이 갈리지 않는지 여기서 지킨다.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"))
import docidkey  # noqa: E402


#------------------------------------------------------------------
# 경로 정규화 — 표기가 달라도 같은 키가 나온다
#=> 구분자·'..'·드라이브 문자 대소문자는 같은 파일을 다르게 보이게 만드는
#   대표적인 세 가지다. 이것들을 흡수하는 것이 매칭 ②의 전부다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 규칙이 바뀌면 AssertionError
#------------------------------------------------------------------
def test_normalize_key_absorbs_spelling():
    canon = "D:/collected/HWP/a.hwp"
    assert F.normalize_key(r"d:\collected\HWP\a.hwp") == canon      # 드라이브 소문자 + 역슬래시
    assert F.normalize_key("D:/collected/HWP/a.hwp") == canon
    assert F.normalize_key(r"D:\collected\x\..\HWP\a.hwp") == canon  # '..' 해소
    # 대소문자는 보존한다 — 리눅스는 경로 대소문자를 구분하므로 규칙을 OS 별로
    # 다르게 만들면 같은 결과 파일이 OS 를 옮길 때 다르게 읽힌다.
    assert F.normalize_key("D:/collected/hwp/a.hwp") != canon
    # UNC 의 앞 슬래시 두 개는 의미가 있으므로 지킨다.
    assert F.normalize_key(r"\\srv\share\a.hwp") == "//srv/share/a.hwp"


#------------------------------------------------------------------
# 코어와 UI 의 정규화가 같은 값을 낸다 (구현 두 벌의 드리프트 방지)
#=> 한쪽만 고치면 화면이 결과를 못 잇는데, 그때도 오류는 안 난다 — 바로 이
#   모듈이 막으려는 그 실패 모양이다. 그래서 시험으로 묶어 둔다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 두 구현이 갈리면 AssertionError
#------------------------------------------------------------------
def test_core_and_ui_normalization_agree():
    samples = [
        r"d:\collected\HWP\a.hwp", "D:/collected/HWP/a.hwp",
        r"D:\a\x\..\b.txt", r"\\srv\share\문서.hwp", "//srv/share/문서.hwp",
        "relative/경로.txt", "", "  D:/공백.txt  ", "E:/한글/파일 이름.pdf",
    ]
    for s in samples:
        assert F.normalize_key(s) == docidkey.normalize_key(s), f"불일치: {s!r}"


#------------------------------------------------------------------
# 실측 4건 유실 — 대소문자만 다른 경로를 이어 준다
#=> grades.jsonl 은 'D:\...', cso_override.jsonl 은 'd:\...' 로 쌓여 정확
#   일치가 0건이던 상황을 그대로 재현한다. 옛 기록에는 doc_id 가 없으므로
#   ②(정규화 경로)로 구제돼야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 매칭이 끊기면 AssertionError
#------------------------------------------------------------------
def test_override_matches_across_drive_letter_case():
    # 사람이 고친 기록 — 드라이브 문자가 소문자다(옛 기록이라 doc_id 도 없다).
    latest = docidkey.OverrideIndex({
        r"d:\collected\HWP\계약서.hwp": {"new_grade": "O", "reviewer": "고봉수"},
    })
    # 분류 결과 — 같은 파일인데 대문자로 적혀 있다.
    rec = {"file": r"D:\collected\HWP\계약서.hwp"}
    ov, how = latest.for_rec(rec)
    assert ov is not None, "대소문자 차이로 사람의 수정이 또 사라졌다"
    assert ov["new_grade"] == "O"
    assert how == "path"          # 정규화가 흡수한다 → ②


#------------------------------------------------------------------
# 매칭 순서 ①~④ — doc_id 가 경로를 이긴다
#=> 문서를 옮기거나 이름을 바꾸면 경로는 못 잇는다. doc_id 가 있으면 그때도
#   이어져야 하고, 그것이 이 설계의 존재 이유다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 우선순위가 바뀌면 AssertionError
#------------------------------------------------------------------
def test_match_order_doc_id_wins_and_new_doc_is_new():
    latest = docidkey.OverrideIndex({
        "D:/old/자리/계약서.hwp": {"doc_id": "SF001", "new_grade": "C"},
        "D:/other/보고서.hwp": {"doc_id": "SF002", "new_grade": "S"},
    })
    # ① 경로가 완전히 달라져도 doc_id 로 이어진다(폴더 정리·개명).
    moved = {"file": "D:/새자리/계약서_최종.hwp", "doc_id": "SF001"}
    ov, how = latest.for_rec(moved)
    assert (ov["new_grade"], how) == ("C", "doc_id")

    # ③ 대소문자만 다르면 구제하되 그 사실을 남긴다.
    ov, how = latest.for_rec({"file": "D:/OTHER/보고서.hwp"})
    assert (ov["new_grade"], how) == ("S", "case")

    # ④ 아무것도 안 맞으면 새 문서다 — 남의 수정을 붙이면 안 된다.
    ov, how = latest.for_rec({"file": "D:/전혀/다른.hwp", "doc_id": "SF999"})
    assert (ov, how) == (None, None)


#------------------------------------------------------------------
# 모호한 대소문자는 구제하지 않는다
#=> 리눅스에서 A.txt 와 a.txt 는 다른 파일이다. 접힘 키가 겹치는 자리에서
#   ③으로 구제하면 남의 수정을 엉뚱한 문서에 붙이게 된다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 모호한데도 이어지면 AssertionError
#------------------------------------------------------------------
def test_ambiguous_case_is_not_rescued():
    latest = docidkey.OverrideIndex({
        "/srv/docs/A.txt": {"new_grade": "C"},
        "/srv/docs/a.txt": {"new_grade": "O"},
    })
    ov, how = latest.for_rec({"file": "/srv/docs/A.TXT"})
    assert (ov, how) == (None, None)


#------------------------------------------------------------------
# doc_id 우선순위 — sfile_id → 내용 해시 → 경로 해시
#=> 목록에 있으면 시스템이 정한 ID 를 쓰고, 없으면 폴백하되 '무엇으로 채웠는지'를
#   남긴다. 이 표식이 없으면 폴백 값이 매핑 테이블로 흘러들어 같은 문서가 두 건이 된다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 우선순위가 바뀌면 AssertionError
#------------------------------------------------------------------
def test_resolve_doc_id_priority(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("내용", encoding="utf-8")

    # 목록이 없으면 doc_id 는 비운다 — 지어내지 않고, 지문으로 문서를 가린다.
    did, src, key, matched, h = F.resolve_doc_id(str(f))
    assert did is None, "sfile_id 가 아니면 doc_id 를 채우면 안 된다"
    assert src == "content" and matched is None
    assert key == F.normalize_key(str(f))
    assert h and len(h) == 64, "SHA-256 전체(64자)여야 한다"

    # 목록에 있으면 sfile_id. 지문은 그래도 함께 구한다 — 결합 키이자
    # '원본이 바뀌었나'의 근거라, 번호가 있다고 없어도 되는 값이 아니다.
    lst = tmp_path / "l.jsonl"
    lst.write_text(json.dumps({"path": str(f), "sfile_id": "SF-1"}) + chr(10),
                   encoding="utf-8")
    fl = F.load(str(lst))
    did, src, _key, _m, h2 = F.resolve_doc_id(str(f), None, fl)
    assert (did, src) == ("SF-1", "sfile_id")
    assert h2 == h, "같은 파일이면 지문도 같아야 한다"

    # 내용도 못 읽으면 둘 다 없다 — 남은 단서는 경로뿐이다.
    did, src, _key, _m, h3 = F.resolve_doc_id(str(tmp_path / "없는파일.txt"))
    assert did is None and src == "path" and h3 is None

#------------------------------------------------------------------
# 목록 로드 — jsonl/csv, 상대경로, 대소문자 표기 차이
#=> 목록의 path 와 실제 스캔 경로는 표기가 다를 수 있다. 그래도 이어져야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_load_jsonl_and_csv(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.txt").write_text("a", encoding="utf-8")
    (d / "b.txt").write_text("b", encoding="utf-8")

    # jsonl — 상대경로는 '목록 파일이 있는 폴더' 기준으로 푼다(현재 작업폴더가 아니다).
    lst = tmp_path / "l.jsonl"
    lst.write_text(
        json.dumps({"path": "docs/a.txt", "sfile_id": "SF-A"}, ensure_ascii=False) + "\n"
        + json.dumps({"path": "docs/b.txt", "sfile_id": "SF-B", "memo": "무시됨"},
                     ensure_ascii=False) + "\n", encoding="utf-8")
    fl = F.load(str(lst))
    assert len(fl) == 2
    e, how = fl.lookup(str(d / "a.txt"))
    assert e.sfile_id == "SF-A" and how == "key"

    # csv — 첫 줄 헤더. path·sfile_id 밖의 열은 무시한다.
    c = tmp_path / "l.csv"
    c.write_text("path,sfile_id,memo\n%s,SF-A,x\n" % (d / "a.txt").as_posix(),
                 encoding="utf-8")
    fl2 = F.load(str(c))
    e2, _ = fl2.lookup(str(d / "a.txt"))
    assert e2.sfile_id == "SF-A"


#------------------------------------------------------------------
# F3 — 같은 파일에 sfile_id 가 둘이면 시작하지 않는다
#=> 어느 쪽을 골라도 틀리므로, 문서를 한 건도 읽기 전에 멈추는 것이 맞다.
#   반쯤 잘못된 ID 가 붙은 결과가 나가는 것이 최악이다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 안 멈추면 AssertionError
#------------------------------------------------------------------
def test_f3_conflicting_sfile_id_is_fatal(tmp_path):
    lst = tmp_path / "l.jsonl"
    lst.write_text(
        json.dumps({"path": "D:/a.txt", "sfile_id": "AAA"}) + "\n"
        + json.dumps({"path": r"d:\a.txt", "sfile_id": "BBB"}) + "\n",  # 표기만 다른 같은 파일
        encoding="utf-8")
    with pytest.raises(F.FileListError) as e:
        F.load(str(lst))
    assert "F3" in str(e.value)


#------------------------------------------------------------------
# F1 · F2 — 못 읽은 줄은 세어서 건너뛰고, 하나도 못 읽으면 멈춘다
#=> 한 줄이 깨진 것은 데이터 문제라 건너뛰면 되지만, 전부 깨진 것은 형식을
#   잘못 준 것이라 계속 갈 이유가 없다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_f1_f2_counts_and_all_bad_is_fatal(tmp_path):
    good = tmp_path / "g.jsonl"
    good.write_text(
        "이건 JSON 이 아니다\n"                                        # F1
        + json.dumps({"path": "D:/a.txt", "sfile_id": ""}) + "\n"      # F2
        + json.dumps({"path": "D:/b.txt", "sfile_id": "SF-B"}) + "\n",
        encoding="utf-8")
    fl = F.load(str(good))
    assert fl.stats["bad_lines"] == 1 and fl.stats["no_id"] == 1
    assert fl.stats["loaded"] == 1

    bad = tmp_path / "b.jsonl"
    bad.write_text("아무것도\n아니다\n", encoding="utf-8")
    with pytest.raises(F.FileListError):
        F.load(str(bad))


#------------------------------------------------------------------
# F7 — 같은 sfile_id 가 여러 경로에 있으면 경고만 한다
#=> 같은 문서의 사본이면 정상이라 멈출 이유가 없다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_f7_duplicate_sfile_id_warns_only(tmp_path):
    lst = tmp_path / "l.jsonl"
    lst.write_text(
        json.dumps({"path": "D:/a.txt", "sfile_id": "SF-1"}) + "\n"
        + json.dumps({"path": "D:/사본/a.txt", "sfile_id": "SF-1"}) + "\n",
        encoding="utf-8")
    fl = F.load(str(lst))
    assert len(fl) == 2
    assert fl.stats["dup_sfile_id"] == 1
    assert any("F7" in w for w in fl.warnings)


#------------------------------------------------------------------
# 주석 판정 — '#' 뒤에 공백이 있어야 주석이다
#=> 목록에 설명을 적어 둘 수 있어야 하지만, `#외부유출금지_회사규정#` 같은
#   폴더가 실제로 있으므로 '#' 뒤가 글자면 경로로 읽어야 한다.
#   Rust 판 `filelist.rs::주석줄은_건너뛰고_샾으로_시작하는_경로는_읽는다` 와
#   같은 것을 본다 — 한쪽만 고치면 두 판이 같은 목록을 다르게 읽는다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_is_comment_keeps_paths_starting_with_hash():
    assert F._is_comment("# 설명")
    assert F._is_comment("## 제목")
    assert F._is_comment("   #\t들여쓴 주석")
    assert F._is_comment("#")
    # '#' 뒤가 글자면 경로다 — 주석이 아니다.
    assert not F._is_comment("#외부유출금지#/a.docx,SF-1")
    assert not F._is_comment('{"path": "#a/b.txt", "sfile_id": "SF-1"}')


#------------------------------------------------------------------
# 주석 줄은 건너뛰되 F1(파싱 실패)로 세지 않는다
#=> 주석을 '해석 못 한 줄'로 세면 멀쩡한 목록에서 F1 경고가 쏟아진다.
#   주석뿐인 파일은 '빈 파일'과 원인이 달라 메시지도 달라야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_comment_lines_are_skipped_not_counted_as_bad(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    lst = tmp_path / "l.jsonl"
    lst.write_text(
        "# 이 줄은 주석\n"
        "##\n"
        "\n"
        + json.dumps({"path": str(f), "sfile_id": "SF-A"}) + "\n",
        encoding="utf-8")
    fl = F.load(str(lst))
    assert len(fl) == 1
    assert fl.stats["lines"] == 1 and fl.stats["comments"] == 2
    assert fl.stats["bad_lines"] == 0, "주석을 파싱 실패로 세면 안 된다"
    assert not any("F1" in w for w in fl.warnings)

    # csv 도 같다 — 주석은 헤더 판정 전에 빠져야 한다.
    csv_lst = tmp_path / "l.csv"
    csv_lst.write_text(
        "# 필수 열은 path, sfile_id 두 개다\n"
        "path,sfile_id\n"
        f"{f},SF-A\n",
        encoding="utf-8")
    fl2 = F.load(str(csv_lst))
    assert len(fl2) == 1 and fl2.stats["comments"] == 1
    assert fl2.lookup(str(f))[0].sfile_id == "SF-A"

    # 주석뿐이면 '빈 파일'과 다른 이유를 알려 준다.
    only = tmp_path / "only.jsonl"
    only.write_text("# 아무 데이터도 없다\n# 정말로\n", encoding="utf-8")
    with pytest.raises(F.FileListError) as e:
        F.load(str(only))
    assert "주석" in str(e.value)


#------------------------------------------------------------------
# 배포에 같이 나가는 샘플 목록이 실제로 읽힌다
#=> 샘플은 사용자가 제일 먼저 여는 파일이라, 이게 안 읽히면 첫인상이
#   "고장난 도구"가 된다. 주석을 잔뜩 단 뒤로는 더 그렇다.
#   Rust 판 `filelist.rs::배포_샘플_목록이_읽힌다` 와 짝이다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_shipped_sample_filelist_parses():
    sample = (pathlib.Path(__file__).resolve().parents[1]
              / "Rust" / "dist-onedir" / "windows" / "sample" / "filelist.sample.txt")
    fl = F.load(str(sample))
    assert fl.stats["loaded"] == 7, "데이터 줄 7건이 적재돼야 한다"
    assert fl.stats["bad_lines"] == 0, "주석을 파싱 실패로 세면 안 된다"
    assert fl.stats["no_id"] == 0
    assert fl.stats["comments"] > 0, "주석이 있어야 한다"
    # 폴더 이름이 '#' 로 시작하는 줄도 살아 있어야 한다.
    assert any("/#외부유출금지" in k for k in fl.entries), \
        "'#' 로 시작하는 폴더 경로가 주석으로 먹히면 안 된다"


#------------------------------------------------------------------
# 오류 메시지의 줄번호는 '편집기에서 보이는 줄'이다
#=> 주석·빈 줄을 걷어낸 뒤의 순번을 쓰면, 주석이 많은 목록에서 F3 이 났을 때
#   엉뚱한 줄을 가리켜 사람이 찾아가지 못한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_f3_reports_physical_line_number(tmp_path):
    lst = tmp_path / "l.jsonl"
    # 1~3줄이 주석/빈 줄 → 충돌이 나는 줄은 물리 5번째다.
    lst.write_text(
        "# 머리말\n"
        "\n"
        "# 또 주석\n"
        + json.dumps({"path": "D:/a.txt", "sfile_id": "AAA"}) + "\n"
        + json.dumps({"path": r"d:\a.txt", "sfile_id": "BBB"}) + "\n",
        encoding="utf-8")
    with pytest.raises(F.FileListError) as e:
        F.load(str(lst))
    assert "5번째 줄" in str(e.value), str(e.value)
