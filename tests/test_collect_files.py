#------------------------------------------------------------------
# 파일 수집(collect_files) 단위 테스트
#=> -dir 배치에서 기본은 해당 폴더만, --recursive 면 하위폴더까지 모으는지,
#   그리고 디렉터리는 제외하고 실제 파일만 반환하는지 검증한다.
#------------------------------------------------------------------

from types import SimpleNamespace

from csoclassify.cli import collect_files


#------------------------------------------------------------------
# 테스트용 args 네임스페이스 생성
#=> collect_files 가 참조하는 필드만 담은 가짜 args 를 만든다.
#
# -in: kw = 덮어쓸 필드(dir/glob/recursive/file)
# -out: SimpleNamespace = collect_files 에 넘길 args
# -out: error = 없음
#------------------------------------------------------------------
def _args(**kw):
    base = dict(file=None, dir=None, glob="*", recursive=False)
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
# 기본(비재귀)은 해당 폴더만
#=> --recursive 없으면 top.txt 만, 하위폴더의 nested.txt 는 제외돼야 한다.
#
# -in: tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_nonrecursive(tmp_path):
    d = _make_tree(tmp_path)
    files = collect_files(_args(dir=d, glob="*.txt", recursive=False))
    names = [f.replace("\\", "/").split("/")[-1] for f in files]
    assert names == ["top.txt"]


#------------------------------------------------------------------
# 재귀는 하위폴더까지
#=> --recursive 면 top.txt 와 sub/nested.txt 둘 다 나와야 한다.
#
# -in: tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_recursive(tmp_path):
    d = _make_tree(tmp_path)
    files = collect_files(_args(dir=d, glob="*.txt", recursive=True))
    names = sorted(f.replace("\\", "/").split("/")[-1] for f in files)
    assert names == ["nested.txt", "top.txt"]


#------------------------------------------------------------------
# -file 우선
#=> file 이 주어지면 그 하나만 반환한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_single_file():
    files = collect_files(_args(file="D:/x/a.hwp"))
    assert files == ["D:/x/a.hwp"]
