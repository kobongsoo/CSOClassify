#------------------------------------------------------------------
# 청킹 로직 단위 테스트
#=> sliding_windows/chunk_ids 가 겹침·경계·예외를 올바로 처리하는지 검증한다.
#   외부 의존성이 없어 모델/네트워크 없이 바로 돌릴 수 있다.
#------------------------------------------------------------------

import pytest

from csoclassify.chunk import sliding_windows, chunk_ids


#------------------------------------------------------------------
# 짧은 입력은 한 청크
#=> 전체 길이가 max_tokens 이하이면 분할 없이 (0, len) 하나만 나와야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_short_single_window():
    assert sliding_windows(5, 10, 2) == [(0, 5)]
    assert sliding_windows(10, 10, 2) == [(0, 10)]


#------------------------------------------------------------------
# 긴 입력은 겹침 있게 분할
#=> step = max-overlap 로 전진하고 마지막 창이 끝에 정확히 닿아야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_long_windows_overlap():
    spans = sliding_windows(25, 10, 2)
    assert spans == [(0, 10), (8, 18), (16, 25)]
    # 각 창의 시작 간격은 step(=8)과 같아야 한다(마지막 제외).
    assert spans[1][0] - spans[0][0] == 8


#------------------------------------------------------------------
# 빈 입력은 빈 결과
#=> 길이 0 이면 청크가 없어야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_empty():
    assert sliding_windows(0, 10, 2) == []
    assert chunk_ids([], 10, 2) == []


#------------------------------------------------------------------
# 잘못된 설정은 예외
#=> max_tokens<=0 또는 overlap>=max_tokens 는 ValueError 여야 한다(무한루프 방지).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_invalid_params():
    with pytest.raises(ValueError):
        sliding_windows(10, 0, 0)
    with pytest.raises(ValueError):
        sliding_windows(10, 5, 5)


#------------------------------------------------------------------
# chunk_ids 는 실제 원소를 담는다
#=> 경계로 자른 각 청크가 원본 id 의 해당 구간과 같아야 한다.
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_chunk_ids_content():
    ids = list(range(25))
    chunks = chunk_ids(ids, 10, 2)
    assert chunks[0] == list(range(0, 10))
    assert chunks[-1] == list(range(16, 25))
