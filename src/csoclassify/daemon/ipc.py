#------------------------------------------------------------------
# 데몬 IPC — loopback TCP + 길이프리픽스 JSON (설계서 §5-4)
#=> 클라이언트와 데몬이 주고받는 메시지를 "4바이트 길이 + UTF-8 JSON" 형식으로
#   보내고 받는다. 반드시 127.0.0.1 에만 바인딩하고, 요청에 토큰을 요구해
#   같은 PC 의 다른 사용자가 함부로 붙지 못하게 한다.
#------------------------------------------------------------------

import socket
import struct

# 한 메시지 최대 크기(16MB). 악의적/버그성 초대형 길이 값으로부터 보호.
MAX_MSG = 16 * 1024 * 1024


#------------------------------------------------------------------
# 지정 바이트 수만큼 정확히 수신
#=> TCP 는 부분 수신이 흔하므로, n 바이트를 다 받을 때까지 반복해서 읽는다.
#
# -in: sock = 연결된 소켓
# -in: n    = 받아야 할 총 바이트 수
#
# -out: bytes = 정확히 n 바이트
# -out: error = 상대가 먼저 끊으면 ConnectionError
#------------------------------------------------------------------
def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        # 빈 수신은 상대가 연결을 닫았다는 뜻.
        if not chunk:
            raise ConnectionError("연결이 예기치 않게 닫힘")
        buf.extend(chunk)
    return bytes(buf)


#------------------------------------------------------------------
# 메시지 송신
#=> dict 를 JSON 으로 직렬화해 [길이(4바이트 big-endian)][본문] 으로 보낸다.
#
# -in: sock = 연결된 소켓
# -in: obj  = 보낼 dict(직렬화 가능해야 함)
#
# -out: 없음
# -out: error = 본문이 너무 크면 ValueError, 전송 실패 시 OSError 전파
#------------------------------------------------------------------
def send_msg(sock, obj):
    import json
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_MSG:
        raise ValueError("메시지가 너무 큼")
    # 길이를 먼저 보내 수신측이 정확히 몇 바이트 읽을지 알게 한다.
    sock.sendall(struct.pack(">I", len(body)) + body)


#------------------------------------------------------------------
# 메시지 수신
#=> [길이][본문] 을 읽어 dict 로 복원한다. 길이 상한을 넘으면 거부한다.
#
# -in: sock = 연결된 소켓
#
# -out: dict = 복원된 메시지
# -out: error = 과대 길이면 ValueError, 끊김이면 ConnectionError
#------------------------------------------------------------------
def recv_msg(sock):
    import json
    header = _recv_exact(sock, 4)
    (length,) = struct.unpack(">I", header)
    if length > MAX_MSG:
        raise ValueError("수신 메시지 길이가 상한 초과")
    body = _recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


#------------------------------------------------------------------
# 서버 리슨 소켓 생성
#=> 127.0.0.1 의 임의(빈) 포트에 바인딩하고 리슨한다. 실제 포트는 반환값으로 알려준다.
#
# -in: host = 바인딩 주소(기본 127.0.0.1; 외부주소 금지)
#
# -out: (sock, port) = 리슨 소켓, 실제 바인딩된 포트번호
# -out: error = 바인딩 실패 시 OSError 전파
#------------------------------------------------------------------
def make_server(host="127.0.0.1"):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 종료 직후 재기동에서 포트 재사용을 원활히.
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, 0))   # 0 = OS 가 빈 포트 자동 할당
    srv.listen(16)
    port = srv.getsockname()[1]
    return srv, port


#------------------------------------------------------------------
# 클라이언트 연결
#=> 데몬(host:port)에 timeout 안에 접속한 소켓을 돌려준다.
#
# -in: host    = 데몬 주소
# -in: port    = 데몬 포트
# -in: timeout = 접속 제한시간(초)
#
# -out: sock = 연결된 소켓(호출측이 close 책임)
# -out: error = 접속 실패/타임아웃 시 OSError 전파
#------------------------------------------------------------------
def connect(host, port, timeout):
    sock = socket.create_connection((host, port), timeout=timeout)
    # 접속 후에는 요청/응답 각각에 넉넉한 별도 타임아웃을 둘 수 있게 해제.
    sock.settimeout(None)
    return sock
