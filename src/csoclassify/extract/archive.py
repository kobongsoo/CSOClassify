#------------------------------------------------------------------
# 압축파일 확장기 — 아카이브 1개를 내부 파일 여러 개로 펼치기
#=> 사용자가 압축파일 1개를 검사하면, 예전엔 압축 전체 텍스트가 '합본 1건'으로 분류돼
#   어느 내부 파일이 민감한지 알 수 없었다. 여기서는 압축을 임시폴더에 풀어 내부
#   '파일 하나하나'를 분류 대상으로 만들어, 파일별로 C/S/O 를 낼 수 있게 한다.
#   또 각 내부 파일은 어떤 최상위 압축(origin)에서 나왔는지 함께 돌려줘, 상위(run_classify)
#   가 '압축파일 자체'의 집계 등급(내부 최고 위험)을 매길 수 있게 한다.
#
#   지원 포맷(내용 기반 감지 — 확장자 위장도 잡음):
#     · zip                              → 표준 zipfile (docx/xlsx/pptx/hwpx 는 '문서 1개'라 제외)
#     · tar / tar.gz / tar.bz2 / tar.xz  → 표준 tarfile (무의존)
#     · gz / bz2 / xz (단일 파일 압축)   → 표준 gzip/bz2/lzma (무의존)
#     · 7z                               → py7zr (순수 파이썬 계열, CentOS7 wheel 확인됨)
#     · rar                              → rarfile + 번들 unrar 바이너리(독점 포맷)
#   중첩 압축(zip 안 7z 등)도 max_depth 까지 재귀로 펼친다.
#   각 내부 파일은 (읽기용 임시경로 src, 표시·신호용 가상경로 label, 최상위압축 origin)
#   3-튜플로 돌려준다. label 은 "<압축경로>/<내부경로>" 라 파일명/경로 규칙 신호가 그대로 먹힌다.
#------------------------------------------------------------------

import bz2
import gzip
import lzma
import os
import sys
import tarfile
import zipfile

from .detect import detect_format

# 잡음 항목(분류 대상 아님): macOS 메타.
_NOISE_PREFIX = "__MACOSX/"
_NOISE_BASENAMES = {".DS_Store"}


#------------------------------------------------------------------
# zip 내부 항목 이름 복원(한글 깨짐 방지)
#=> 옛 zip 은 파일명을 cp437 로 저장하는데, 파이썬 zipfile 은 UTF-8 플래그가 없으면
#   cp437 로 디코드해 한글이 깨진다. 원래 한글(cp949/euc-kr)로 되돌린다.
#
# -in: info = zipfile.ZipInfo (내부 항목 메타)
#
# -out: str = 사람이 읽는 내부 경로(복원 실패 시 원본 그대로)
# -out: error = 없음(항상 문자열 반환)
#------------------------------------------------------------------
def _zip_entry_name(info):
    # UTF-8 플래그(0x800)가 있으면 이미 올바른 유니코드다.
    if info.flag_bits & 0x800:
        return info.filename
    try:
        # zipfile 이 cp437 로 잘못 디코드한 것을 바이트로 되돌린 뒤 cp949 로 재해석.
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


#------------------------------------------------------------------
# 잡음 내부 항목인지
#=> macOS 메타(__MACOSX/…, .DS_Store)는 분류 대상이 아니므로 거른다.
#
# -in: name = 내부 경로 문자열
# -out: bool = 잡음이면 True
# -out: error = 없음
#------------------------------------------------------------------
def _is_noise(name):
    return name.startswith(_NOISE_PREFIX) or os.path.basename(name) in _NOISE_BASENAMES


#------------------------------------------------------------------
# 압축 종류 감지(내용 기반)
#=> 확장자가 아니라 파일 앞부분의 매직 바이트/구조로 압축 종류를 판별한다.
#    · 7z/rar 는 고유 매직, zip 은 PK 매직(단 docx 등 '문서 zip'은 detect 로 제외)
#    · tar 계열(tar/tar.gz/tar.bz2/tar.xz)은 tarfile.is_tarfile 로 판별
#    · 그 외 단일 압축(gz/bz2/xz)은 각 매직으로 판별
#
# -in: path = 검사할 파일 경로
#
# -out: str|None = 'zip'|'tar'|'7z'|'rar'|'gz'|'bz2'|'xz' 또는 None(압축 아님)
# -out: error = 없음(읽기 실패 시 None)
#------------------------------------------------------------------
def _archive_type(path):
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return None
    # 7z: '7z BC AF 27 1C'
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    # rar: 'Rar!\x1a\x07\x00'(v4) / 'Rar!\x1a\x07\x01\x00'(v5)
    if head[:7] == b"Rar!\x1a\x07\x00" or head[:8] == b"Rar!\x1a\x07\x01\x00":
        return "rar"
    # zip 계열(PK): docx/xlsx/pptx/hwpx 는 '문서 1개'라 펼치지 않는다(detect 로 구분).
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip" if detect_format(path) == "zip" else None
    # tar 계열(압축 tar 포함): 이름과 무관하게 실제 구조로 판별.
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except OSError:
        pass
    # 단일 파일 압축(tar 가 아닐 때만 여기 도달).
    if head[:2] == b"\x1f\x8b":
        return "gz"
    if head[:3] == b"BZh":
        return "bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    return None


#------------------------------------------------------------------
# 일반 압축 여부(내용 기반)
#=> 위 감지가 압축 종류를 돌려주면 아카이브다. (문서 zip 은 False)
#
# -in: path = 검사할 파일 경로
# -out: bool = 확장 대상 압축이면 True
# -out: error = 없음
#------------------------------------------------------------------
def is_archive(path):
    return _archive_type(path) is not None


#------------------------------------------------------------------
# 임시파일에 바이트 저장(내부 헬퍼)
#=> 내부 파일 내용을 tmp_root 아래 '일련번호' 이름으로 평평하게 쓴다. detect 는 내용
#   기반이라 임시 파일명이 무엇이든 상관없다(경로 탈출·한글깨짐 걱정 없음).
#
# -in: tmp_root = 임시 루트폴더
# -in: seqbox   = [정수] 형태의 가변 카운터(호출 간 증가)
# -in: data     = 저장할 바이트
#
# -out: str = 만들어진 임시파일 경로
# -out: error = 파일 쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def _spill(tmp_root, seqbox, data):
    seqbox[0] += 1
    p = os.path.join(tmp_root, "a%d" % seqbox[0])
    with open(p, "wb") as f:
        f.write(data)
    return p


#------------------------------------------------------------------
# 번들 unrar 실행파일 경로 탐색 + rarfile 설정(내부 헬퍼)
#=> rar 는 독점 포맷이라 해제에 외부 unrar 바이너리가 필요하다. 배포에 함께 넣은
#   unrar(리눅스)/unrar.exe(윈도우)를 찾아 rarfile 에 지정한다. 못 찾으면 rarfile 이
#   시스템(unrar/bsdtar 등)을 자동 탐색하게 둔다.
#
# -in: 없음(실행 위치 기준으로 후보 폴더를 훑음)
# -out: 없음(rarfile.UNRAR_TOOL 설정 시도)
# -out: error = 없음(못 찾으면 기본값 유지)
#------------------------------------------------------------------
def _configure_unrar(rarfile_mod):
    exe = "unrar.exe" if os.name == "nt" else "unrar"
    # 후보: PyInstaller 번들(_MEIPASS) → 실행파일 옆 → 이 모듈 옆.
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, exe))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), exe))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), exe))
    for c in cands:
        if os.path.isfile(c):
            if os.name != "nt":
                # 리눅스는 실행권한이 없을 수 있으니 보정.
                try:
                    os.chmod(c, 0o755)
                except OSError:
                    pass
                # 번들 unrar 은 최신 libstdc++/libgcc(CXXABI)를 요구하는데, 구형 배포판
                # (CentOS7)의 시스템 libstdc++ 는 그걸 못 준다. 우리 배포에 이미 넣어둔
                # 최신 libstdc++.so.6(_MEIPASS/실행파일 옆)를 unrar 가 쓰도록 LD_LIBRARY_PATH
                # 에 그 폴더를 앞세운다(자식 프로세스가 os.environ 을 상속받는다).
                libdir = os.path.dirname(c)
                prev = os.environ.get("LD_LIBRARY_PATH", "")
                if libdir not in prev.split(os.pathsep):
                    os.environ["LD_LIBRARY_PATH"] = (
                        libdir + (os.pathsep + prev if prev else ""))
            rarfile_mod.UNRAR_TOOL = c
            return


#------------------------------------------------------------------
# 단일 파일 압축의 내부 이름 만들기(내부 헬퍼)
#=> gz/bz2/xz 는 파일 1개를 감싼 것이라 내부 이름이 없다. 표시용으로 압축 확장자를
#   떼어 이름을 만든다(예: "report.txt.gz" → "report.txt").
#
# -in: label = 압축의 표시 경로
# -in: atype = 'gz'|'bz2'|'xz'
# -out: str = 내부 파일 표시 이름
# -out: error = 없음
#------------------------------------------------------------------
def _single_inner_name(label, atype):
    base = os.path.basename(label)
    exts = {"gz": (".gz", ".gzip"), "bz2": (".bz2", ".bz"), "xz": (".xz", ".lzma")}
    for e in exts.get(atype, ()):
        if base.lower().endswith(e):
            return base[: -len(e)]
    # 확장자를 못 떼면 원본 이름 뒤에 표식을 붙여 원본과 구분한다.
    return base + ".out"


#------------------------------------------------------------------
# 아카이브 1개 → 내부 (임시경로, 내부이름) 목록 추출(내부 헬퍼)
#=> 종류별로 알맞은 라이브러리로 내부 파일들을 임시폴더에 풀고, (임시경로, 내부이름)
#   목록을 만든다. 폴더 항목·잡음은 거른다.
#
# -in: src      = 아카이브 실제 경로
# -in: atype    = _archive_type 결과
# -in: label    = 표시용 경로(단일 압축 내부이름 계산에 사용)
# -in: tmp_root = 임시 루트폴더
# -in: seqbox   = 가변 카운터
#
# -out: list[tuple(str,str)] = (임시경로, 내부이름) 목록
# -out: error = 라이브러리 없음/손상 등은 예외 전파(호출부가 폴백 처리)
#------------------------------------------------------------------
def _extract_members(src, atype, label, tmp_root, seqbox):
    out = []
    if atype == "zip":
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = _zip_entry_name(info)
                if _is_noise(name):
                    continue
                out.append((_spill(tmp_root, seqbox, z.read(info)), name))
        return out

    if atype == "tar":
        # r:* = 압축 종류(gz/bz2/xz/무압축) 자동 판별.
        with tarfile.open(src, "r:*") as t:
            for m in t.getmembers():
                if not m.isfile():
                    continue
                if _is_noise(m.name):
                    continue
                f = t.extractfile(m)
                if f is None:
                    continue
                out.append((_spill(tmp_root, seqbox, f.read()), m.name))
        return out

    if atype in ("gz", "bz2", "xz"):
        # 단일 파일 압축: 통째로 풀어 파일 하나로.
        opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}[atype]
        with opener(src, "rb") as f:
            data = f.read()
        out.append((_spill(tmp_root, seqbox, data), _single_inner_name(label, atype)))
        return out

    if atype == "7z":
        import py7zr   # 지연 import(미설치면 상위가 폴백)
        seqbox[0] += 1
        sub = os.path.join(tmp_root, "s7_%d" % seqbox[0])
        os.makedirs(sub, exist_ok=True)
        with py7zr.SevenZipFile(src, "r") as z:
            z.extractall(path=sub)
        # 풀린 실제 파일들을 걷는다(임시경로=실제경로, 내부이름=상대경로).
        for root, _dirs, fnames in os.walk(sub):
            for fn in fnames:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, sub).replace(os.sep, "/")
                if _is_noise(rel):
                    continue
                out.append((full, rel))
        return out

    if atype == "rar":
        import rarfile   # 지연 import(미설치면 상위가 폴백)
        _configure_unrar(rarfile)
        with rarfile.RarFile(src) as rf:
            for info in rf.infolist():
                if info.isdir():
                    continue
                name = info.filename
                if _is_noise(name):
                    continue
                out.append((_spill(tmp_root, seqbox, rf.read(info)), name))
        return out

    return out


#------------------------------------------------------------------
# 경로 목록을 '분류 작업' 목록으로 확장
#=> 입력 경로들을 훑어, 압축은 임시폴더에 풀어 내부 파일들로 치환하고, 그 외 일반
#   파일은 그대로 둔다. 반환값의 각 원소는 (src, label, origin) 3-튜플이다.
#    1) 큐에 (src, label, depth, origin) 로 넣고 하나씩 처리
#    2) src 가 압축이고 depth<max_depth 면 내부 파일을 임시폴더로 풀어 재귀로 큐에 추가
#       (내부 파일의 origin 은 '최상위 압축' 경로로 물려줘, 압축 자체 등급 집계에 쓴다)
#    3) 압축이 아니면 (src, label, origin) 작업으로 확정
#   ※ 라이브러리 없음/손상/빈 압축 등은 '펼치지 않고' 원본 1건으로 둔다(상위가 폴백 처리).
#
# -in: paths    = 입력 파일 경로 리스트(collect_files 결과)
# -in: tmp_root = 내부 파일을 풀 임시폴더(호출부가 만들고 끝나면 지운다)
# -in: max_depth= 중첩 압축을 펼칠 최대 깊이(기본 3; 압축폭탄 무한재귀 방지)
#
# -out: list[tuple(str,str,str|None)] = (읽기용 실제경로, 표시·신호용 경로, 최상위압축경로 또는 None)
# -out: error = 없음(개별 압축 실패는 그 압축만 원본 1건으로 폴백)
#------------------------------------------------------------------
def expand_paths(paths, tmp_root, *, max_depth=3):
    jobs = []
    # (src=읽을 실제경로, label=표시/신호용 경로, depth=중첩깊이, origin=최상위압축경로|None)
    queue = [(p, p, 0, None) for p in paths]
    seqbox = [0]   # 임시파일 이름 충돌 방지용 가변 카운터

    while queue:
        src, label, depth, origin = queue.pop(0)

        # 너무 깊으면(중첩 폭탄 방지) 더는 안 펼치고 한 건으로 둔다.
        atype = None if depth >= max_depth else _archive_type(src)
        if atype is None:
            jobs.append((src, label, origin))
            continue

        try:
            members = _extract_members(src, atype, label, tmp_root, seqbox)
        except Exception:   # noqa: BLE001  라이브러리 없음/손상/도구 없음 등 → 폴백
            jobs.append((src, label, origin))
            continue

        # 빈 압축(또는 전부 폴더/잡음)이면 원본 1건으로 남긴다.
        if not members:
            jobs.append((src, label, origin))
            continue

        # 내부 파일의 origin 은 '최상위 압축' 경로. (이미 있으면 물려주고, 없으면 이 압축이 최상위)
        child_origin = origin if origin is not None else label
        for tmp_path, inner_name in members:
            queue.append((tmp_path, label + "/" + inner_name, depth + 1, child_origin))

    return jobs
