#------------------------------------------------------------------
# 압축파일 확장(expand_paths) 단위 테스트
#=> zip 1개를 내부 '파일별' 작업으로 펼치는지, 폴더/잡음은 거르는지, 한글 이름을
#   복원하는지, 중첩 zip 도 푸는지, docx 같은 '문서 zip'은 안 펼치는지 검증한다.
#------------------------------------------------------------------

import gzip
import io
import os
import tarfile
import zipfile

import pytest

from csoclassify.extract.archive import (expand_paths, is_archive,
                                         _zip_entry_name, _archive_type)


#------------------------------------------------------------------
# 테스트용 zip 생성 헬퍼
#=> 이름:내용 매핑을 받아 임시폴더에 zip 파일을 만든다.
#
# -in: tmp     = 임시폴더 경로
# -in: name    = 만들 zip 파일명
# -in: entries = {내부경로: bytes/str} 매핑
# -out: str = 생성된 zip 경로
# -out: error = 없음
#------------------------------------------------------------------
def _make_zip(tmp, name, entries):
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w") as z:
        for n, data in entries.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            z.writestr(n, data)
    return p


#------------------------------------------------------------------
# 일반 zip → 내부 파일별로 펼침
#=> 3개 파일을 담은 zip 이 3개 작업으로 펼쳐지고, label 은 "<zip>/<내부>" 이며
#   src 는 실제로 읽을 수 있는 임시파일이어야 한다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_plain_zip(tmp_path):
    z = _make_zip(str(tmp_path), "docs.zip",
                  {"a.txt": "hello", "sub/b.txt": "world", "sub/c.txt": "!"})
    work = tmp_path / "work"
    work.mkdir()
    jobs = expand_paths([z], str(work))
    assert len(jobs) == 3
    labels = sorted(l for _, l, _o in jobs)
    # label 은 zip 경로 뒤에 내부경로가 붙는다.
    assert any(l.endswith("docs.zip/a.txt") for l in labels)
    assert any(l.endswith("docs.zip/sub/b.txt") for l in labels)
    # src 는 실제 읽을 수 있는 임시파일, origin 은 그 zip 경로.
    for src, _label, origin in jobs:
        assert os.path.isfile(src)
        assert origin == z


#------------------------------------------------------------------
# 폴더 항목/__MACOSX 잡음은 건너뜀
#=> 디렉터리 항목과 macOS 메타는 파일이 아니므로 작업 목록에 들어가면 안 된다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_skips_noise(tmp_path):
    p = os.path.join(str(tmp_path), "n.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("dir/", b"")               # 폴더 항목
        z.writestr("__MACOSX/._x", b"junk")   # macOS 메타
        z.writestr("real.txt", b"keep")
    work = tmp_path / "w"
    work.mkdir()
    jobs = expand_paths([p], str(work))
    assert len(jobs) == 1
    assert jobs[0][1].endswith("n.zip/real.txt")


#------------------------------------------------------------------
# 한글 내부 이름 복원(cp437→cp949)
#=> 옛 zip(UTF-8 플래그 없음)의 한글 이름을 복원한다. 실제 zip 에선 이름 바이트가
#   cp949 인데 zipfile 이 cp437 로 디코드해 깨진다. 그 상황을 ZipInfo 로 재현해
#   _zip_entry_name 이 원래 한글로 되돌리는지 직접 검증한다.
#   (writestr 에 비-ASCII str 을 주면 파이썬이 UTF-8 플래그를 켜버려 실상황과 달라지므로,
#    복원 함수를 단위로 직접 테스트한다.)
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_zip_name_recover():
    # 실제 cp949 zip 을 zipfile 이 읽었을 때의 상태: 이름=cp949바이트를 cp437로 디코드, 플래그 0.
    garbled = "계약서.txt".encode("cp949").decode("cp437")
    zi = zipfile.ZipInfo(garbled)
    zi.flag_bits = 0                     # UTF-8 아님(옛 zip)
    assert _zip_entry_name(zi) == "계약서.txt"
    # UTF-8 플래그가 켜진 항목은 그대로 둔다.
    zi2 = zipfile.ZipInfo("이름.txt")
    zi2.flag_bits = 0x800
    assert _zip_entry_name(zi2) == "이름.txt"


#------------------------------------------------------------------
# 중첩 zip 도 펼침
#=> zip 안의 zip 이 있으면 안쪽 파일까지 개별 작업으로 나온다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_nested_zip(tmp_path):
    # 안쪽 zip 을 먼저 만든 뒤 그 바이트를 바깥 zip 에 넣는다.
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zi:
        zi.writestr("deep.txt", b"deep")
    p = os.path.join(str(tmp_path), "outer.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("top.txt", b"top")
        z.writestr("nested.zip", inner.getvalue())
    work = tmp_path / "w"
    work.mkdir()
    jobs = expand_paths([p], str(work))
    labels = [l for _, l, _o in jobs]
    # top.txt 와, 중첩 zip 안 deep.txt 가 모두 나와야 한다.
    assert any(l.endswith("top.txt") for l in labels)
    assert any(l.endswith("nested.zip/deep.txt") for l in labels)
    # 중첩이라도 origin 은 최상위 outer.zip 으로 통일된다.
    for _src, _label, origin in jobs:
        assert origin == p


#------------------------------------------------------------------
# 문서형 zip(docx)·일반 파일은 펼치지 않음
#=> docx 는 내부가 zip 이지만 '문서 1개'이므로 그대로 1건. 일반 txt 도 그대로 통과.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_docx_and_plain_not_expanded(tmp_path):
    # 최소 docx: [Content_Types].xml + word/document.xml 이 있으면 detect 가 docx 로 본다.
    docx = os.path.join(str(tmp_path), "d.docx")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document><w:body/></w:document>")
    plain = tmp_path / "p.txt"
    plain.write_text("just text", encoding="utf-8")
    work = tmp_path / "w"
    work.mkdir()
    assert is_archive(docx) is False          # docx 는 아카이브로 안 본다
    jobs = expand_paths([docx, str(plain)], str(work))
    # 둘 다 펼쳐지지 않고 원본 그대로 1건씩(= src==label, origin=None).
    assert len(jobs) == 2
    for src, label, origin in jobs:
        assert src == label
        assert origin is None


#------------------------------------------------------------------
# tar.gz 확장 + origin(최상위 압축) 표기
#=> tar.gz 를 풀어 내부 파일별 작업이 나오고, 각 작업의 origin 이 그 tar.gz 경로여야 한다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_targz(tmp_path):
    p = os.path.join(str(tmp_path), "d.tar.gz")
    with tarfile.open(p, "w:gz") as t:
        for name, body in (("a.txt", b"aaa"), ("sub/b.txt", b"bbb")):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    assert _archive_type(p) == "tar"
    work = tmp_path / "w"
    work.mkdir()
    jobs = expand_paths([p], str(work))
    assert len(jobs) == 2
    for src, label, origin in jobs:
        assert os.path.isfile(src)
        assert origin == p                 # 최상위 압축 경로가 origin
        assert label.startswith(p + "/")


#------------------------------------------------------------------
# 단일 gz 파일 확장(내부 이름은 확장자 제거)
#=> report.txt.gz → 내부 파일 이름이 report.txt 가 되어야 한다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_single_gz(tmp_path):
    p = os.path.join(str(tmp_path), "report.txt.gz")
    with gzip.open(p, "wb") as f:
        f.write("계약 내용".encode("utf-8"))
    assert _archive_type(p) == "gz"
    work = tmp_path / "w"
    work.mkdir()
    jobs = expand_paths([p], str(work))
    assert len(jobs) == 1
    assert os.path.basename(jobs[0][1]) == "report.txt"


#------------------------------------------------------------------
# 7z 확장(py7zr 있을 때만)
#=> 7z 를 풀어 내부 파일별 작업이 나오는지. py7zr 미설치 환경은 건너뛴다.
#
# -in: tmp_path = pytest 임시폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_expand_7z(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    p = os.path.join(str(tmp_path), "d.7z")
    with py7zr.SevenZipFile(p, "w") as z:
        z.writestr(b"secret", "a.txt")
        z.writestr(b"plain", "sub/b.txt")
    assert _archive_type(p) == "7z"
    work = tmp_path / "w"
    work.mkdir()
    jobs = expand_paths([p], str(work))
    assert len(jobs) == 2
    for src, label, origin in jobs:
        assert os.path.isfile(src)
        assert origin == p
