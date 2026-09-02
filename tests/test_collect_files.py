#------------------------------------------------------------------
# 파일 수집(collect_files) 단위 테스트
#=> --dir 배치는 '항상 하위폴더까지 재귀'로 모으는지(-r 여부 무관),
#   그리고 디렉터리는 제외하고 실제 파일만 반환하는지 검증한다.
#------------------------------------------------------------------

from types import SimpleNamespace

from csoclassify.cli import collect_files, read_files_from


#------------------------------------------------------------------
# 테스트용 args 네임스페이스 생성
#=> collect_files 가 참조하는 필드만 담은 가짜 args 를 만든다.
#
# -in: kw = 덮어쓸 필드(dir/glob/recursive/file)
# -out: SimpleNamespace = collect_files 에 넘길 args
# -out: error = 없음
#------------------------------------------------------------------
def _args(**kw):
    base = dict(file=None, dir=None, files_from=None, glob="*", recursive=False)
    base.update(kw)
    return SimpleNamespace(**base)


#------------------------------------------------------------------
# 중첩 폴더 픽스처 구성
#=> top.txt + sub/nested.txt 구조를 tmp 에 만든다.
#
# -in: tmp = pytest tmp_path
# -out: str = 최상위 폴더 경로
# -out: error = 없음
#------------------------------------------------------------------
def _make_tree(tmp):
    (tmp / "top.txt").write_text("x", encoding="utf-8")
    sub = tmp / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("y", encoding="utf-8")
    return str(tmp)


#------------------------------------------------------------------
# --dir 은 -r 없이도 항상 하위폴더까지
#=> -r 을 안 줘도 top.txt 와 sub/nested.txt 둘 다 나와야 한다(항상 재귀).
#
# -in: tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_always_recursive(tmp_path):
    d = _make_tree(tmp_path)
    files = collect_files(_args(dir=d, glob="*.txt", recursive=False))
    names = sorted(f.replace("\\", "/").split("/")[-1] for f in files)
    assert names == ["nested.txt", "top.txt"]


#------------------------------------------------------------------
# -r 을 줘도 결과 동일(무시됨)
#=> recursive=True 여도 위와 같은 결과(항상 재귀라 차이 없음).
#
# -in: tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_recursive_flag_ignored(tmp_path):
    d = _make_tree(tmp_path)
    files = collect_files(_args(dir=d, glob="*.txt", recursive=True))
    names = sorted(f.replace("\\", "/").split("/")[-1] for f in files)
    assert names == ["nested.txt", "top.txt"]


#------------------------------------------------------------------
# --file 우선
#=> file 이 주어지면 그 하나만 반환한다(실제로 있는 파일일 때).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_single_file(tmp_path):
    p = tmp_path / "a.hwp"
    p.write_bytes(b"x")
    assert collect_files(_args(file=str(p))) == [str(p)]


#------------------------------------------------------------------
# --file 이 '읽을 수 있는 파일'이 아니면 0건
#=> [2026-09-01] 예전에는 존재 여부를 묻지 않고 그대로 추출에 넘겼다. 그래서
#   없는 경로나 폴더를 주면 '추출 실패' 레코드 한 건이 만들어져 결과처럼 나갔다 —
#   부르는 쪽에서 "문서를 읽어 봤는데 글자가 없더라"와 구분이 안 됐다. 둘은
#   대응이 다르다(전자는 사람이 그 문서를 처리, 후자는 부른 쪽이 경로를 고침).
#   0건으로 돌려주면 호출부가 no_input(1001)으로 알린다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_없는_파일과_폴더는_0건(tmp_path):
    assert collect_files(_args(file=str(tmp_path / "없다.hwp"))) == []
    sub = tmp_path / "폴더"
    sub.mkdir()
    assert collect_files(_args(file=str(sub))) == []


#------------------------------------------------------------------
# --files-from 목록 파싱 규칙
#=> 목록 파일에서 빈 줄·'#' 주석·감싼 따옴표를 걷어내고, 실제 파일만
#   입력 순서 그대로(중복은 첫 것만) 남기는지 확인한다. 순서가 뒤집히면
#   결과 jsonl 순서가 호출부 기대와 어긋나므로 순서까지 본다.
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(assert)
# -out: error = 규칙이 어긋나면 AssertionError
#------------------------------------------------------------------
def test_read_files_from_rules(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("b", encoding="utf-8")
    lines = [
        "# 주석",
        "",
        '"%s"' % b,             # 따옴표로 감싼 경로
        "  %s  " % a,           # 앞뒤 공백
        str(b),                 # 중복 — 첫 것만 남아야 한다
        str(tmp_path / "none.txt"),   # 없는 파일 — 건너뛴다
        str(tmp_path),                # 폴더 — 건너뛴다
    ]
    lst = tmp_path / "list.txt"
    lst.write_text("\n".join(lines), encoding="utf-8")
    assert read_files_from(str(lst)) == [str(b), str(a)]


#------------------------------------------------------------------
# --files-from 이 collect_files 의 대상이 되는지
#=> args.files_from 만 준 경우 그 목록이 그대로 처리 대상이 되어야 한다.
#   (--file/--dir 과의 동시 사용 금지는 cli.main 이 막으므로 여기선 안 본다.)
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(assert)
# -out: error = 목록이 반영되지 않으면 AssertionError
#------------------------------------------------------------------
def test_collect_files_uses_files_from(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("a", encoding="utf-8")
    lst = tmp_path / "list.txt"; lst.write_text(str(a), encoding="utf-8")
    assert collect_files(_args(files_from=str(lst))) == [str(a)]


#------------------------------------------------------------------
# BOM 붙은 목록 파일도 읽히는지
#=> 윈도우 메모장으로 저장하면 앞에 BOM 이 붙는다. 그걸 안 떼면 첫 줄의
#   경로가 통째로 어긋나 '그런 파일 없음'이 된다 — 겪기 쉬운 사고라 못 박는다.
#
# -in: tmp_path = pytest 임시 폴더
#
# -out: 없음(assert)
# -out: error = 첫 줄이 유실되면 AssertionError
#------------------------------------------------------------------
def test_read_files_from_bom(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("a", encoding="utf-8")
    lst = tmp_path / "list.txt"
    lst.write_bytes(("﻿" + str(a)).encode("utf-8"))
    assert read_files_from(str(lst)) == [str(a)]
