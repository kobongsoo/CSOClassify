#------------------------------------------------------------------
# 데몬 레지스트리/싱글턴 단위 테스트 (설계서 §5-3, race 대응 검증)
#=> 잠금 획득이 "먼저 만든 자 승리"로 동작하고, 살아있는 데몬이 있으면 두 번째는
#   실패하며, 죽은(stale) 기록은 정리 후 다시 획득되는지 검증한다.
#   LOCALAPPDATA 를 임시폴더로 바꿔 실제 사용자 상태를 오염시키지 않는다.
#------------------------------------------------------------------

import os

from csoclassify.daemon import registry


#------------------------------------------------------------------
# 상태폴더를 임시로 격리하는 헬퍼
#=> monkeypatch 로 LOCALAPPDATA 를 tmp 로 바꾸고, 캐시가 없으니 그대로 반영된다.
#
# -in: monkeypatch = pytest 픽스처
# -in: tmp_path    = pytest 임시폴더
# -out: 없음
# -out: error = 없음
#------------------------------------------------------------------
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


#------------------------------------------------------------------
# 최초 획득은 성공, 재획득은 실패(살아있는 pid)
#=> 같은 모델로 두 번째 acquire 는, 현재 프로세스(살아있음) 기록이 있으면 False.
#
# -in: monkeypatch, tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_acquire_then_blocked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    model = "unit-model"

    # 첫 획득은 성공해야 한다.
    assert registry.acquire_singleton(model) is True

    # 살아있는 데몬이 있는 상황을 흉내: 현재 pid 로 record 를 남긴다.
    registry.write_record(model, {"pid": os.getpid(), "host": "127.0.0.1",
                                  "port": 1, "token": "t", "version": "x", "model": model})
    # 두 번째 획득은 "살아있는 데몬 존재"로 막혀야 한다.
    assert registry.acquire_singleton(model) is False

    registry.clear(model)


#------------------------------------------------------------------
# stale(죽은 pid) 기록은 정리 후 재획득
#=> 존재하지 않는 pid 기록이 남아 있으면 stale 로 보고 정리한 뒤 새로 획득되어야 한다.
#
# -in: monkeypatch, tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_stale_reclaimed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    model = "unit-stale"

    # 먼저 잠금을 만들어 두고(파일 존재), 죽은 pid 기록을 심는다.
    registry.acquire_singleton(model)
    registry.write_record(model, {"pid": 999999999, "host": "127.0.0.1",
                                  "port": 1, "token": "t", "version": "x", "model": model})
    # 죽은 pid 이므로 stale 정리 후 재획득이 성공해야 한다.
    assert registry.acquire_singleton(model) is True

    registry.clear(model)


#------------------------------------------------------------------
# record 쓰기/읽기 왕복
#=> write_record → read_record 가 같은 dict 를 돌려줘야 한다.
#
# -in: monkeypatch, tmp_path = pytest 픽스처
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_record_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    model = "unit-rt"
    rec = {"pid": 1, "host": "127.0.0.1", "port": 12345, "token": "abc",
           "version": "0.1.0", "model": model}
    registry.write_record(model, rec)
    assert registry.read_record(model) == rec
    registry.clear(model)
    assert registry.read_record(model) is None
