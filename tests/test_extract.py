#------------------------------------------------------------------
# 사이냅 추출기 통합 테스트
#=> 실제 snf_exe.exe 로 텍스트가 뽑히는지, 실패 케이스가 ExtractError 로
#   드러나는지 검증한다. snf_exe 가 없거나 윈도우가 아니면 자동 skip 한다.
#------------------------------------------------------------------

import os

import pytest

from csoclassify import resources
from csoclassify.extract.synap_exe import SynapExeExtractor
from csoclassify.extract.base import ExtractError

# snf_exe 가 실제로 있어야 의미 있는 테스트 → 없으면 전체 skip.
_HAS_SNF = os.path.isfile(resources.synap_exe_path())
pytestmark = pytest.mark.skipif(not _HAS_SNF, reason="snf_exe.exe 없음")


#------------------------------------------------------------------
# 텍스트 파일 추출 성공
#=> UTF-8 텍스트 파일을 넣으면 원문 문자열이 그대로 나와야 한다.
#
# -in: tmp_path = pytest 임시폴더 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_extract_txt(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("한국어 추출 테스트 문장.", encoding="utf-8")
    ex = SynapExeExtractor()
    text = ex.extract(str(p))
    assert "한국어 추출 테스트" in text


#------------------------------------------------------------------
# 없는 파일은 ExtractError
#=> 존재하지 않는 경로는 프로세스도 안 띄우고 즉시 ExtractError 여야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_missing_file():
    ex = SynapExeExtractor()
    with pytest.raises(ExtractError):
        ex.extract("no_such_file_12345.hwp")


#------------------------------------------------------------------
# 텍스트 보존 옵션
#=> save_dir 을 주면 추출 텍스트 파일이 그 폴더에 남아야 한다.
#
# -in: tmp_path = pytest 임시폴더 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_save_text(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("보존 테스트", encoding="utf-8")
    save_dir = tmp_path / "kept"
    ex = SynapExeExtractor()
    ex.extract(str(src), save_dir=str(save_dir))
    # 원본명 기반 .txt 가 보존 폴더에 생겼는지 확인.
    kept = list(save_dir.glob("*.txt"))
    assert len(kept) == 1
