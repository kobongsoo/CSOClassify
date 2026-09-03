# -*- coding: utf-8 -*-
"""문서 식별자(doc_id) · 입력 목록(--filelist) 어댑터.

설계: plan/문서분류체계-연동-분류규칙-설계.html §7-5 · §7-5-2-1 · §7-5-3 (D0 · D0b)

이 모듈이 푸는 문제는 하나다 — "같은 문서인가"를 무엇으로 판단할 것인가.
지금까지는 경로 문자열이 유일한 이름표였는데, 경로는 문서의 주소이지 신분증이
아니다. 폴더를 옮기거나 이름만 바꿔도 값이 달라지고, 그러면 그 문서에 쌓아 둔
사람의 판단 이력이 통째로 끊긴다. 실제로 저장소에서 드라이브 문자 대소문자
차이(D: vs d:) 하나 때문에 사람이 고친 등급 4건이 조용히 반영되지 않고 있었다.
"""

import csv
import hashlib
import io
import json
import os

# doc_id 폭. 문서분류체계 DB 의 DC_ID 가 VARCHAR(40) 이라 짝이 될 doc_id 도 같은
# 폭으로 맞춘다. SHA-256 hex 는 64자라 앞 40자(160비트)만 쓴다 — 1억 문서 기준
# 충돌 확률이 약 10⁻³³ 로 사실상 0 이고, SHA-1(정확히 40자)보다 충돌 내성이 좋다.
DOC_ID_LEN = 40


#------------------------------------------------------------------
# 목록 파일이 잘못됐다 — 처리를 시작하면 안 되는 실패
#=> F1(전부 파싱 실패)·F3(한 파일에 두 ID)처럼 "이 목록으로는 어느 문서에 어떤
#   ID 를 줘야 할지 알 수 없는" 상황에 던진다. 부르는 쪽(cli)이 잡아서 오류
#   계약대로 종료한다. 반쯤 잘못된 ID 가 결과에 섞이는 것이 최악이라 조기에 멈춘다.
#
# -필드: message = 사람이 읽는 실패 사유(그대로 화면에 나간다)
#------------------------------------------------------------------
class FileListError(Exception):
    pass


#------------------------------------------------------------------
# 경로 → 정규화 키
#=> 같은 파일을 가리키는 서로 다른 표기를 한 모양으로 모은다. 이 값이 doc_id 가
#   없을 때 문서를 잇는 보조키이고, 목록의 path 와 실제 스캔 경로를 맞추는 자리다.
#    1) 구분자를 '/' 로 통일     (D:\a\b  →  D:/a/b)
#    2) '..' · '.' 을 해소       (D:/a/../b  →  D:/b)
#    3) 드라이브 문자만 대문자   (d:/a  →  D:/a)
#
#   [왜 대소문자를 보존하나] 윈도우는 경로 대소문자를 구분하지 않고 리눅스는
#   구분한다. 정규화 규칙을 OS 별로 다르게 하면 같은 결과 파일이 OS 를 옮길 때
#   다르게 해석된다. 그래서 정규화는 리눅스 기준(보존)으로 고정하고, 대소문자만
#   다른 경우는 매칭 3단계에서 한 번 더 시도하되 그 사실을 기록한다(§7-5-3).
#   드라이브 문자만 예외로 대문자로 맞춘다 — 윈도우에서 이것이 갈릴 이유가 없고,
#   실제로 손해가 난 4건이 정확히 이 차이였다.
#
#   [UNC 경로] //server/share/... 는 앞의 '//' 를 지키며 그대로 둔다.
#
# -in: path = 원본 경로 문자열(절대·상대 모두)
#
# -out: str = 정규화된 경로 키(빈 입력이면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def normalize_key(path):
    if not path:
        return ""
    s = str(path).strip().replace("\\", "/")
    # UNC(//server/share)는 앞 슬래시 두 개가 의미를 가지므로 따로 떼어 두고,
    # 나머지만 정리한 뒤 다시 붙인다(normpath 가 '//' 를 뭉갤 수 있다).
    unc = s.startswith("//")
    body = s[2:] if unc else s
    # '..'/'.' 해소. os.path.normpath 는 윈도우에서 '/' 를 '\' 로 되돌리므로
    # 다시 '/' 로 바꾼다(OS 와 무관하게 같은 키가 나와야 한다).
    body = os.path.normpath(body).replace("\\", "/")
    if unc:
        body = "//" + body.lstrip("/")
    # 드라이브 문자(맨 앞 "x:")만 대문자로. 그 뒤 내용은 손대지 않는다.
    if len(body) >= 2 and body[1] == ":" and body[0].isalpha():
        body = body[0].upper() + body[1:]
    return body


#------------------------------------------------------------------
# 대소문자 무시 키
#=> 매칭 3단계(§7-5-3 ③)에서 "대소문자만 다른가"를 볼 때 쓴다. 조용히 같게
#   취급하지 않으려고 정규화 키와 따로 둔다 — 이 키로 이어졌다는 사실은
#   rematched_by 로 기록된다.
#
# -in: path = 원본 경로 또는 정규화 키
#
# -out: str = 소문자로 접은 정규화 키
# -out: error = 없음
#------------------------------------------------------------------
def casefold_key(path):
    return normalize_key(path).casefold()


#------------------------------------------------------------------
# 내용 해시로 doc_id 만들기 (폴백 ②)
#=> sfile_id 를 못 얻은 문서용. 파일 내용의 SHA-256 앞 40자를 쓴다.
#   문서를 옮기거나 이름을 바꿔도 유지되지만, 내용을 고치면 값이 바뀐다(R13).
#   그래서 이 값은 매핑 테이블에 넣지 않는다 — doc_id_source 로 구분한다.
#
# -in: path = 해시를 구할 실제 파일 경로(압축 내부 파일이면 그 임시 실경로)
#
# -out: str = 40자 소문자 16진수, 읽기 실패면 None
# -out: error = 없음 (OSError 는 None 으로 돌린다 — 부르는 쪽이 ③으로 내려간다)
#------------------------------------------------------------------
def content_doc_id(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            # 1MB 씩 읽어 대용량 파일에서도 메모리가 튀지 않게 한다.
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()[:DOC_ID_LEN]


#------------------------------------------------------------------
# 경로 해시로 doc_id 만들기 (폴백 ③)
#=> 내용조차 못 읽는 파일(암호 걸린 zip·손상 파일)의 최후 수단. 정규화 키의
#   SHA-256 앞 40자다. 문서를 고쳐도 유지되지만 옮기면 값이 바뀐다.
#
# -in: key = 정규화 경로 키(normalize_key 결과)
#
# -out: str = 40자 소문자 16진수
# -out: error = 없음
#------------------------------------------------------------------
def path_doc_id(key):
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:DOC_ID_LEN]


#------------------------------------------------------------------
# 목록 한 줄 — 경로와 그 문서의 sfile_id
#=> jsonl 한 줄 또는 csv 한 행이 이 모양으로 들어온다.
#
# -필드: key      = 정규화 경로 키(색인 키)
# -필드: path     = 목록에 적혀 있던 원본 경로(오류 메시지용)
# -필드: sfile_id = MpowerV11 문서 고유 ID
# -필드: hash     = 목록이 함께 준 파일 해시(선택). 없으면 None
#------------------------------------------------------------------
class Entry:
    __slots__ = ("key", "path", "sfile_id", "hash")

    def __init__(self, key, path, sfile_id, hash_=None):
        self.key = key
        self.path = path
        self.sfile_id = sfile_id
        self.hash = hash_


#------------------------------------------------------------------
# 읽어 들인 입력 목록 — 경로로 sfile_id 를 찾는 사전
#=> 목록은 '대상 지정'과 'ID 사전' 두 가지로 쓰인다(§7-5-2-1). 어느 쪽이든
#   조회는 정규화 키로 하고, 못 찾으면 대소문자를 접어 한 번 더 본다.
#
# -필드: entries  = {정규화 키: Entry}
# -필드: warnings = 검증에서 나온 경고 문장들(F4·F6·F7 — 처리는 계속한다)
# -필드: stats    = {"lines","loaded","bad_lines"(F1),"no_id"(F2),
#                    "missing_file"(F4),"dup_sfile_id"(F7)}
#------------------------------------------------------------------
class FileList:
    def __init__(self, entries, warnings, stats):
        self.entries = entries
        self.warnings = warnings
        self.stats = stats
        # 대소문자만 다른 표기를 구제하기 위한 보조 색인. 같은 접힘 키에 둘 이상이
        # 걸리면 어느 쪽인지 알 수 없으므로 그 키는 색인에서 뺀다(조용한 오연결 방지).
        folded = {}
        for k in entries:
            folded.setdefault(k.casefold(), []).append(k)
        self._folded = {f: ks[0] for f, ks in folded.items() if len(ks) == 1}

    #--------------------------------------------------------------
    # 경로로 목록 항목 찾기
    #=> ① 정규화 키가 같으면 그것, ② 대소문자만 다르면 그것(구제).
    #
    # -in: path = 실제 스캔한 파일 경로
    #
    # -out: (Entry, matched) = 찾은 항목과 이어진 방법("key"|"case"), 없으면 (None, None)
    # -out: error = 없음
    #--------------------------------------------------------------
    def lookup(self, path):
        key = normalize_key(path)
        e = self.entries.get(key)
        if e is not None:
            return e, "key"
        k2 = self._folded.get(key.casefold())
        if k2 is not None:
            return self.entries[k2], "case"
        return None, None

    #--------------------------------------------------------------
    # 목록이 정한 처리 대상 경로들
    #=> --filelist 를 단독으로 준 경우(=목록이 대상을 정하는 경우)에 쓴다.
    #   실제로 존재하는 파일만 돌려준다 — 없는 파일은 F4 로 이미 경고했다.
    #
    # -in: 없음
    #
    # -out: list[str] = 처리할 파일 경로(목록에 적힌 원본 표기, 정렬)
    # -out: error = 없음
    #--------------------------------------------------------------
    def target_paths(self):
        return sorted(e.path for e in self.entries.values() if os.path.isfile(e.path))

    def __len__(self):
        return len(self.entries)


#------------------------------------------------------------------
# 목록 한 줄(dict) → (경로, sfile_id, hash)
#=> jsonl 과 csv 가 같은 모양으로 들어오게 맞춘다. path·sfile_id 밖의 필드는
#   무시한다 — MpowerV11 이 편한 대로 더 담아도 되게 한 약속이다(§7-5-2-1).
#
# -in: row  = 한 줄을 해석한 dict
# -in: base = 목록 파일이 있는 폴더(상대경로를 푸는 기준)
#
# -out: (path, sfile_id, hash) = 절대경로로 푼 경로 · ID · 해시(없으면 None)
# -out: error = path 가 비어 있으면 (None, None, None)
#------------------------------------------------------------------
def _row_to_entry(row, base):
    path = (row.get("path") or "").strip()
    if not path:
        return None, None, None
    sfile_id = (row.get("sfile_id") or "").strip()
    file_hash = (row.get("hash") or "").strip() or None
    # 상대경로는 '현재 작업폴더'가 아니라 '목록 파일이 있는 폴더' 기준으로 푼다.
    # 목록과 문서를 함께 다른 곳으로 옮겨도 깨지지 않게 하려는 것이다.
    if not os.path.isabs(path) and not path.startswith("//") and not path.startswith("\\\\"):
        path = os.path.join(base, path)
    return path, sfile_id, file_hash


#------------------------------------------------------------------
# 목록 파일 읽기 (jsonl · csv) — 핵심
#=> MpowerV11 이 뽑아 준 {경로, sfile_id} 목록을 읽어 색인을 만든다.
#    1) 확장자·첫 줄 모양으로 jsonl 인지 csv 인지 가린다
#    2) 한 줄씩 해석하며 F1(파싱 실패)·F2(ID 없음)를 세어 건너뛴다
#    3) 같은 정규화 키에 다른 ID 가 오면 F3 으로 즉시 멈춘다
#    4) 다 읽은 뒤 F4(파일 없음)·F7(한 ID 가 여러 경로) 을 경고로 모은다
#
#   [왜 F3 만 멈추나] 한 파일에 두 ID 는 모순이라 어느 쪽을 골라도 틀린다.
#   나머지는 "이만큼은 ID 를 못 얻었다"고 세어서 알려 주면 충분하다.
#
# -in: path = 목록 파일 경로(.jsonl/.json/.csv/.tsv, UTF-8·BOM 허용)
#
# -out: FileList = 색인·경고·통계를 담은 객체
# -out: error = 파일이 없거나 형식이 깨졌으면 FileListError
#------------------------------------------------------------------
def load(path):
    if not path or not os.path.isfile(path):
        raise FileListError(f"목록 파일을 찾을 수 없습니다: {path}")
    base = os.path.dirname(os.path.abspath(path))
    try:
        # BOM 이 있어도 첫 필드 이름이 '\ufeffpath' 가 되지 않도록 utf-8-sig 로 읽는다.
        with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
            raw = f.read()
    except OSError as e:
        raise FileListError(f"목록 파일을 읽을 수 없습니다: {path} ({e})")

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        raise FileListError(f"목록 파일이 비어 있습니다: {path}")

    rows, bad = _parse_rows(lines, path)

    entries, stats = {}, {
        "lines": len(lines), "loaded": 0, "bad_lines": bad,
        "no_id": 0, "missing_file": 0, "dup_sfile_id": 0,
    }
    warnings = []
    seen_id = {}

    for lineno, row in rows:
        fpath, sfile_id, file_hash = _row_to_entry(row, base)
        if not fpath:
            stats["bad_lines"] += 1
            continue
        # F2 — sfile_id 가 비었거나 없으면 그 줄은 없는 것으로 본다.
        if not sfile_id:
            stats["no_id"] += 1
            continue
        key = normalize_key(fpath)
        prev = entries.get(key)
        # F3 — 같은 파일에 다른 ID. 어느 쪽을 골라도 틀리므로 멈춘다.
        if prev is not None and prev.sfile_id != sfile_id:
            raise FileListError(
                f"목록 {os.path.basename(path)} {lineno}번째 줄 — 같은 파일에 "
                f"sfile_id 가 두 개입니다(F3).\n"
                f"  경로: {key}\n"
                f"  먼저: {prev.sfile_id}\n"
                f"  나중: {sfile_id}\n"
                f"  어느 쪽을 골라도 틀리므로 분류를 시작하지 않았습니다. "
                f"목록을 고쳐 다시 실행하세요.")
        if prev is None:
            entries[key] = Entry(key, fpath, sfile_id, file_hash)
            stats["loaded"] += 1
        seen_id.setdefault(sfile_id, []).append(key)

    # F1 — 한 줄도 못 읽었으면 데이터가 아니라 형식을 잘못 준 것이다.
    if not entries:
        raise FileListError(
            f"목록 {os.path.basename(path)} — 쓸 수 있는 줄이 하나도 없습니다"
            f"(전체 {len(lines)}줄 · 파싱 실패 {stats['bad_lines']} · "
            f"sfile_id 없음 {stats['no_id']}).\n"
            f"  jsonl 이면 {{\"path\": ..., \"sfile_id\": ...}} 한 줄씩, "
            f"csv 면 첫 줄에 path,sfile_id 헤더가 필요합니다.")

    # F4 — 목록에 있는데 파일이 없다. 목록이 낡았을 수 있다(경고만).
    missing = [e.path for e in entries.values() if not os.path.isfile(e.path)]
    if missing:
        stats["missing_file"] = len(missing)
        warnings.append(
            f"[F4] 목록에 있으나 파일이 없습니다: {len(missing)}건 "
            f"(예: {missing[0]}) — 목록이 낡았을 수 있습니다")

    # F7 — 같은 ID 가 여러 경로에. 사본이면 정상이라 경고만 한다.
    dups = {sid: ks for sid, ks in seen_id.items() if len(ks) > 1}
    if dups:
        stats["dup_sfile_id"] = len(dups)
        sid, ks = next(iter(dups.items()))
        warnings.append(
            f"[F7] 같은 sfile_id 가 여러 경로에 있습니다: {len(dups)}건 "
            f"(예: {sid} → {len(ks)}곳) — 같은 문서의 사본이면 정상입니다")

    if stats["bad_lines"]:
        warnings.append(f"[F1] 해석하지 못한 줄 {stats['bad_lines']}건을 건너뛰었습니다")
    if stats["no_id"]:
        warnings.append(f"[F2] sfile_id 가 비어 있는 줄 {stats['no_id']}건을 건너뛰었습니다")

    return FileList(entries, warnings, stats)


#------------------------------------------------------------------
# 본문 줄들 → (줄번호, dict) 목록
#=> jsonl 이냐 csv 냐를 가려서 해석한다. 판정은 확장자를 먼저 보되, 첫 줄이
#   '{' 로 시작하면 확장자와 무관하게 jsonl 로 본다 — 확장자를 잘못 붙여 온
#   목록 때문에 통째로 실패하는 것보다 낫다.
#
# -in: lines = 빈 줄을 걷어낸 본문 줄들
# -in: path  = 목록 파일 경로(확장자 판정용)
#
# -out: (rows, bad) = [(줄번호, dict), ...] 와 해석 실패 줄 수(F1)
# -out: error = csv 인데 헤더에 path/sfile_id 가 없으면 FileListError
#------------------------------------------------------------------
def _parse_rows(lines, path):
    ext = os.path.splitext(path)[1].lower()
    is_json = lines[0].lstrip().startswith("{") or ext in (".jsonl", ".json", ".ndjson")

    rows, bad = [], 0
    if is_json:
        for i, ln in enumerate(lines, 1):
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                bad += 1          # F1 — 그 줄만 버리고 계속 간다
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            rows.append((i, obj))
        return rows, bad

    # csv/tsv — 첫 줄이 헤더다. 탭이 콤마보다 많으면 탭 구분으로 본다.
    delim = "\t" if lines[0].count("\t") > lines[0].count(",") else ","
    reader = csv.DictReader(lines, delimiter=delim)
    field_names = [(n or "").strip() for n in (reader.fieldnames or [])]
    if "path" not in field_names or "sfile_id" not in field_names:
        raise FileListError(
            f"목록 {os.path.basename(path)} — csv 첫 줄에 path · sfile_id 열이 "
            f"필요합니다(지금: {', '.join(field_names) or '(헤더 없음)'})")
    for i, row in enumerate(reader, 2):   # 1번 줄은 헤더
        # 열 이름 앞뒤 공백을 흡수해 담는다(엑셀에서 뽑으면 흔하다).
        rows.append((i, {(k or "").strip(): v for k, v in row.items()}))
    return rows, bad


#------------------------------------------------------------------
# 문서 하나의 doc_id 정하기 — 우선순위 3단계 (§7-5-2)
#=> ① 목록의 sfile_id → ② 내용 해시 → ③ 경로 해시 순으로 시도한다.
#   어느 것으로 채웠는지를 함께 돌려주어, 나중에 매핑 테이블에 넣을 때
#   ①만 골라 넣을 수 있게 한다(②·③은 MpowerV11 문서와 이어지지 않는다 — R14).
#
# -in: path      = 표시용 경로(결과 레코드의 file 값)
# -in: read_path = 실제로 읽을 경로(압축 내부 파일이면 임시 실경로). None 이면 path
# -in: flist     = FileList 또는 None(목록을 안 준 실행)
#
# -out: (doc_id, source, key, matched) =
#         doc_id  = 40자 이내 식별자
#         source  = "sfile_id" | "content" | "path"
#         key     = 정규화 경로 키
#         matched = 목록과 이어진 방법("key"|"case") · 못 이었으면 None
# -out: error = 없음 (파일을 못 읽으면 ③으로 내려간다)
#------------------------------------------------------------------
def resolve_doc_id(path, read_path=None, flist=None):
    key = normalize_key(path)
    if flist is not None:
        entry, matched = flist.lookup(path)
        if entry is not None:
            return entry.sfile_id, "sfile_id", key, matched
    # ② 내용 해시 — 옮기거나 이름을 바꿔도 유지된다.
    cid = content_doc_id(read_path or path)
    if cid:
        return cid, "content", key, None
    # ③ 내용조차 못 읽는 파일(암호 zip·손상)의 최후 수단.
    return path_doc_id(key), "path", key, None
