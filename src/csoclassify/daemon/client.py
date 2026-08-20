#------------------------------------------------------------------
# 데몬 클라이언트 (설계서 §5-2 클라이언트 흐름)
#=> 상주 데몬에 접속해 embed 요청을 위임한다. 데몬이 없으면 자동 기동하고,
#   준비될 때까지 PING 폴링한다. 어떤 이유로든 데몬을 못 쓰면 DaemonUnavailable
#   을 던져, 상위(cli)가 in-process 로 폴백하게 한다(항상 동작 보장).
#------------------------------------------------------------------

import os
import subprocess
import sys
import time

from .. import __version__
from .. import config
from ..logsetup import get_logger
from . import ipc
from . import registry

log = get_logger(__name__)


#------------------------------------------------------------------
# 데몬 사용 불가 신호
#=> 접속 실패/기동 실패/버전불일치 등으로 데몬 경로를 포기해야 할 때 던진다.
#   상위는 이걸 받으면 in-process 로 처리한다.
#
# -필드: (메시지 문자열만 사용)
#------------------------------------------------------------------
class DaemonUnavailable(Exception):
    pass


class DaemonClient:
    #------------------------------------------------------------------
    # 생성자 — 대상 모델/타임아웃 보관
    #=> 어떤 모델 데몬에 붙을지와 접속/기동 대기 시간을 기억한다.
    #
    # -in: model         = 모델 별칭(레지스트리 키)
    # -in: idle_timeout  = 데몬 자동기동 시 넘겨줄 유휴시간(초)
    # -in: log_path      = 데몬이 남길 로그파일 경로(같은 파일 공유용, None 가능)
    # -in: verbose       = True 면 데몬도 DEBUG 로 기동
    #
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def __init__(self, model, idle_timeout=None, log_path=None, verbose=False):
        self.model = model
        # 0/None = 무한(자동종료 없음). 명시적 0 을 보존하려 is None 으로 판별(그 값을 --serve 로 전달).
        self.idle_timeout = config.DEFAULT_IDLE_TIMEOUT if idle_timeout is None else idle_timeout
        self.log_path = log_path
        self.verbose = verbose

    #------------------------------------------------------------------
    # PING 시도
    #=> 레지스트리 기록의 host:port 로 붙어 헬스정보를 받아 온다. 못 붙으면 None.
    #
    # -in: rec = 레지스트리 record dict(host/port 포함)
    #
    # -out: dict = ping 응답(model_ready/version 등) 또는 None(접속 실패)
    # -out: error = 없음(모든 통신 예외를 None 으로 흡수)
    #------------------------------------------------------------------
    def _ping(self, rec):
        try:
            sock = ipc.connect(rec["host"], rec["port"], config.CLIENT_CONNECT_TIMEOUT)
        except OSError:
            return None
        try:
            ipc.send_msg(sock, {"op": "ping"})
            return ipc.recv_msg(sock)
        except (OSError, ValueError):
            return None
        finally:
            try:
                sock.close()
            except OSError:
                pass

    #------------------------------------------------------------------
    # 살아있고 호환되는 데몬 기록 찾기
    #=> 레지스트리를 읽어 (a)pid 생존 (b)버전 일치 (c)PING 응답 & 모델준비 를 모두
    #   만족하는 record 를 반환한다. 버전이 다르면 구 데몬을 정지시키고 None 반환.
    #
    # -in: 없음
    #
    # -out: (rec, ping) = 사용 가능한 record 와 그 ping 응답, 없으면 (None, None)
    # -out: error = 없음
    #------------------------------------------------------------------
    def _find_live(self):
        rec = registry.read_record(self.model)
        if not rec:
            return None, None
        # 죽은 pid 기록은 stale → 정리하고 없음 처리.
        if not registry.is_pid_alive(rec.get("pid")):
            registry.clear(self.model)
            return None, None
        # 버전이 다르면 구버전 데몬을 정중히 종료시키고 새로 띄우게 한다.
        if rec.get("version") != __version__:
            self._try_stop(rec)
            registry.clear(self.model)
            return None, None
        ping = self._ping(rec)
        if ping and ping.get("ok") and ping.get("model_ready"):
            return rec, ping
        return None, None

    #------------------------------------------------------------------
    # 구 데몬 정지 시도(베스트에포트)
    #=> 버전 불일치 등으로 교체할 때 기존 데몬에 stop 을 보낸다. 실패해도 무시.
    #
    # -in: rec = 대상 데몬 record(host/port/token)
    #
    # -out: 없음
    # -out: error = 없음
    #------------------------------------------------------------------
    def _try_stop(self, rec):
        try:
            sock = ipc.connect(rec["host"], rec["port"], config.CLIENT_CONNECT_TIMEOUT)
        except OSError:
            return
        try:
            ipc.send_msg(sock, {"op": "stop", "token": rec.get("token")})
            ipc.recv_msg(sock)
        except (OSError, ValueError):
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    #------------------------------------------------------------------
    # 데몬 자동 기동
    #=> 현재 실행파일(또는 python -m csoclassify)을 '--serve' 인자로 백그라운드에 띄운다.
    #   콘솔창이 뜨지 않게/부모와 분리되게 실행한다. 실제 싱글턴 보장은 데몬이 잠금을
    #   잡으며 처리하므로, 중복 spawn 되어도 하나만 살아남는다.
    #
    # -in: 없음
    #
    # -out: 없음(프로세스만 띄움)
    # -out: error = spawn 자체 실패 시 OSError 전파(상위에서 폴백)
    #------------------------------------------------------------------
    def _spawn(self):
        # 얼려진(exe) 상태면 자기 자신이 실행파일, 아니면 python -m csoclassify.
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--serve"]
        else:
            args = [sys.executable, "-m", "csoclassify", "--serve"]
        args += ["--model", self.model, "--idle-timeout", str(self.idle_timeout)]
        # 데몬이 같은 로그파일에 처리 로그를 남기도록 경로/verbose 를 물려준다.
        if self.log_path:
            args += ["--log", self.log_path]
        if self.verbose:
            args += ["-v"]
        log.info("데몬 자동 기동 model=%s idle_timeout=%ds", self.model, self.idle_timeout)

        # 부모와 분리 + 콘솔창 숨김(윈도우). 표준 입출력은 버린다.
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        subprocess.Popen(args, **kwargs)

    #------------------------------------------------------------------
    # 데몬 준비 대기(폴링)
    #=> 자동 기동 후, 모델이 올라와 PING 이 model_ready 를 줄 때까지 짧게 반복 확인한다.
    #
    # -in: deadline = 이 시각(perf_counter 기준)까지만 대기
    #
    # -out: (rec, ping) = 준비된 데몬, 없으면 (None, None)
    # -out: error = 없음
    #------------------------------------------------------------------
    def _wait_ready(self, deadline):
        while time.perf_counter() < deadline:
            rec, ping = self._find_live()
            if rec:
                return rec, ping
            # 로딩 중일 수 있으니 잠깐 쉬었다가 다시 확인.
            time.sleep(0.15)
        return None, None

    #------------------------------------------------------------------
    # embed 요청(핵심)
    #=> 살아있는 데몬을 찾거나 새로 띄운 뒤, 파일 경로/옵션을 보내 결과를 받는다.
    #    1) 기존 데몬 탐색 → 있으면 바로 요청
    #    2) 없으면 spawn → 준비 대기 → 요청
    #    3) 끝내 못 쓰면 DaemonUnavailable
    #
    # -in: path = 처리할 문서 경로
    # -in: opts = 처리 옵션 dict
    #
    # -out: result = 파이프라인 결과 dict(file/model/vector/elapsed_ms 등)
    # -out: error = 데몬 사용 불가 시 DaemonUnavailable, 처리 실패 시 RuntimeError(kind 포함)
    #------------------------------------------------------------------
    def request_embed(self, path, opts):
        rec = self._ready_record()
        resp = self._rpc(rec, {"op": "embed", "token": rec.get("token"), "path": path, "opts": opts})
        if not resp.get("ok"):
            # 처리 자체의 실패(추출/임베딩)는 폴백이 아니라 진짜 오류로 올린다.
            raise RuntimeError(f"{resp.get('kind', 'error')}: {resp.get('error', '알 수 없는 오류')}")
        return resp["result"]

    #------------------------------------------------------------------
    # 데몬에 '텍스트 임베딩' 요청 (분류 경로 전용)
    #=> 이미 추출·정제된 텍스트를 데몬에 보내 벡터만 받는다(재추출 없음). 분류가
    #   반복 실행돼도 상주 데몬의 웜 모델을 재사용해 model_load=0 이 된다.
    #
    # -in: text = 임베딩할 정제 텍스트
    # -in: opts = {max_tokens, overlap, normalize} (없으면 서버 기본값)
    #
    # -out: vector = 문서 임베딩 벡터(list[float])
    # -out: error = DaemonUnavailable(접속/통신/기동 실패 → 호출부가 in-process 폴백)
    #               / RuntimeError(데몬이 임베딩 자체 실패를 보고)
    #------------------------------------------------------------------
    def request_embed_text(self, text, opts):
        rec = self._ready_record()
        resp = self._rpc(rec, {"op": "embed_text", "token": rec.get("token"),
                               "text": text, "opts": opts})
        if not resp.get("ok"):
            raise RuntimeError(f"{resp.get('kind', 'error')}: {resp.get('error', '알 수 없는 오류')}")
        return resp["vector"]

    #------------------------------------------------------------------
    # 준비된 데몬 record 확보 (없으면 기동·대기)
    #=> 살아있는 데몬을 찾고, 없으면 한 번 띄워 준비될 때까지 기다린 뒤 record 를 준다.
    #   request_embed / request_embed_text 가 공유한다.
    #
    # -in: 없음
    #
    # -out: rec = 데몬 레지스트리 record(host/port/token 등)
    # -out: error = DaemonUnavailable(기동 실패/제한시간 초과)
    #------------------------------------------------------------------
    def _ready_record(self):
        rec, _ping = self._find_live()
        if rec is None:
            try:
                self._spawn()
            except OSError as e:
                raise DaemonUnavailable(f"데몬 기동 실패: {e}")
            deadline = time.perf_counter() + config.DAEMON_START_WAIT
            rec, _ping = self._wait_ready(deadline)
            if rec is None:
                raise DaemonUnavailable("데몬이 제한시간 내 준비되지 않음")
        return rec

    #------------------------------------------------------------------
    # 데몬에 1회 요청/응답 (RPC)
    #=> 준비된 데몬에 접속해 메시지 하나를 보내고 응답을 받는다. 통신 계층 오류는
    #   DaemonUnavailable 로 올려 호출부가 in-process 로 폴백하게 한다.
    #
    # -in: rec = _ready_record() 가 준 데몬 record
    # -in: msg = 보낼 요청 dict(op/token/…)
    #
    # -out: dict = 데몬 응답 메시지
    # -out: error = DaemonUnavailable(접속/통신 실패)
    #------------------------------------------------------------------
    def _rpc(self, rec, msg):
        try:
            sock = ipc.connect(rec["host"], rec["port"], config.CLIENT_CONNECT_TIMEOUT)
        except OSError as e:
            raise DaemonUnavailable(f"데몬 접속 실패: {e}")
        try:
            ipc.send_msg(sock, msg)
            return ipc.recv_msg(sock)
        except (OSError, ValueError) as e:
            raise DaemonUnavailable(f"데몬 통신 실패: {e}")
        finally:
            try:
                sock.close()
            except OSError:
                pass

    #------------------------------------------------------------------
    # 데몬 상태 조회 (--status)
    #=> --status 용. 살아있는 데몬 record+ping 을 반환하거나 None.
    #
    # -in: 없음
    #
    # -out: (rec, ping) 또는 (None, None)
    # -out: error = 없음
    #------------------------------------------------------------------
    def status(self):
        return self._find_live()

    #------------------------------------------------------------------
    # 데몬 정지
    #=> --stop 용. 살아있는 데몬을 찾아 stop 을 보낸다.
    #
    # -in: 없음
    #
    # -out: bool = 정지 요청을 보냈으면 True, 없었으면 False
    # -out: error = 없음
    #------------------------------------------------------------------
    def stop(self):
        rec = registry.read_record(self.model)
        if rec and registry.is_pid_alive(rec.get("pid")):
            self._try_stop(rec)
            return True
        return False
