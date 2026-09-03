# -*- coding: utf-8 -*-
"""문서 식별자 매칭 — 결과와 '사람이 고친 기록'을 잇는다.

설계: plan/문서분류체계-연동-분류규칙-설계.html §7-5-3 (매칭 순서)

지금까지 화면은 결과 레코드와 오버라이드를 **경로 문자열 정확 일치**로 이었다.
경로는 문서의 주소이지 신분증이 아니라서, 드라이브 문자 대소문자 하나만 달라도
(`D:\\...` vs `d:\\...`) 사람이 고친 등급이 조용히 사라진다. 실제로 저장소에서
17건 중 4건이 그렇게 반영되지 않고 있었다. 아무 오류도 나지 않아 알아채기 어려운,
거버넌스 도구에서 가장 나쁜 종류의 실패다.

이 모듈은 그 잇기를 4단계로 바꾼다.

    ① doc_id 가 같다               → 같은 문서
    ② 없으면 key(정규화 경로)가 같다 → 같은 문서, rematched_by="path"
    ③ 그래도 없고 대소문자만 다르다  → 같은 문서, rematched_by="case"
    ④ 전부 실패                     → 새 문서

[왜 ②·③을 기록하나] 조용히 같게 취급하지 않기 위해서다. 나중에 "왜 이 수정이
이 문서에 붙었나"를 설명할 수 있어야 한다.

[코어와의 관계] normalize_key 는 csoclassify.filelist.normalize_key 와 **같은 값**을
내야 한다. UI 는 빌드된 exe 를 상대로 도는 별도 프로세스라 그 패키지를 import 할 수
없어 여기 한 벌을 더 둔다. 두 구현이 갈리는 것을 막으려고
tests/test_docid.py 의 test_core_and_ui_normalization_agree 가
같은 입력에 같은 값이 나오는지 지킨다.
"""

import os

#------------------------------------------------------------------
# 경로 → 정규화 키
#=> 같은 파일을 가리키는 서로 다른 표기를 한 모양으로 모은다.
#    1) 구분자를 '/' 로 통일     (D:\a\b  →  D:/a/b)
#    2) '..' · '.' 을 해소       (D:/a/../b  →  D:/b)
#    3) 드라이브 문자만 대문자   (d:/a  →  D:/a)
#   대소문자는 보존한다 — 리눅스는 경로 대소문자를 구분하므로, OS 별로 규칙을
#   다르게 하면 같은 결과 파일이 OS 를 옮길 때 다르게 해석된다. 대소문자만 다른
#   경우는 아래 ③에서 한 번 더 시도하고 그 사실을 남긴다.
#
# -in: path = 원본 경로 문자열
#
# -out: str = 정규화된 경로 키(빈 입력이면 빈 문자열)
# -out: error = 없음
#------------------------------------------------------------------
def normalize_key(path):
    if not path:
        return ""
    s = str(path).strip().replace("\\", "/")
    # UNC(//server/share)의 앞 슬래시 두 개는 의미가 있어 따로 떼어 둔다.
    unc = s.startswith("//")
    body = s[2:] if unc else s
    body = os.path.normpath(body).replace("\\", "/")
    if unc:
        body = "//" + body.lstrip("/")
    if len(body) >= 2 and body[1] == ":" and body[0].isalpha():
        body = body[0].upper() + body[1:]
    return body


#------------------------------------------------------------------
# 오버라이드 색인 — 파일 경로 말고도 doc_id·정규화 키로 찾을 수 있게 한다
#=> load_overrides() 가 만든 {파일경로: 기록} 을 그대로 담되(기존 코드가 dict 로
#   쓰고 있어 dict 를 상속한다), doc_id·정규화 키·대소문자 접힘 키 색인을 함께 만든다.
#
#   [같은 접힘 키에 둘 이상이면 뺀다] 리눅스에서 A.txt 와 a.txt 는 다른 파일이다.
#   그런 자리에서 ③으로 구제하면 남의 수정을 엉뚱한 문서에 붙이게 되므로,
#   모호한 키는 아예 색인에서 제외해 ④(새 문서)로 보낸다.
#
# -필드: by_doc_id = {doc_id: 저장된 파일키}
# -필드: by_key    = {정규화 경로: 저장된 파일키}
# -필드: by_fold   = {소문자 접힘 경로: 저장된 파일키} — 유일할 때만
#------------------------------------------------------------------
class OverrideIndex(dict):
    def __init__(self, by_file=None):
        super().__init__(by_file or {})
        self.by_doc_id, self.by_key = {}, {}
        fold = {}
        for f, e in self.items():
            did = (e or {}).get("doc_id")
            if did:
                self.by_doc_id.setdefault(did, f)
            k = normalize_key(f)
            self.by_key.setdefault(k, f)
            fold.setdefault(k.casefold(), []).append(f)
        self.by_fold = {k: v[0] for k, v in fold.items() if len(v) == 1}

    #--------------------------------------------------------------
    # 레코드 → 저장된 파일키 (매칭 순서 ①~④)
    #=> 이 레코드의 수정 기록이 어느 키 아래 저장돼 있는지 찾는다.
    #
    # -in: rec = 분류 레코드 dict(또는 경로 문자열)
    #
    # -out: (파일키, how) = 찾았으면 저장된 키와 이은 방법
    #        (None|"doc_id"|"path"|"case"), 못 찾으면 (None, None)
    # -out: error = 없음
    #--------------------------------------------------------------
    def resolve(self, rec):
        if isinstance(rec, str):
            rec = {"file": rec}
        rec = rec or {}
        f = rec.get("file")
        # ① doc_id — 문서를 옮기거나 이름을 바꿔도 이어지는 유일한 길.
        did = rec.get("doc_id")
        if did and did in self.by_doc_id:
            return self.by_doc_id[did], "doc_id"
        # 옛 기록은 경로가 키다. 정확히 같으면 더 볼 것 없다.
        if f in self:
            return f, None
        k = normalize_key(rec.get("key") or f)
        # ② 정규화 경로 — 구분자·'..'·드라이브 문자 표기 차이를 흡수한다.
        if k in self.by_key:
            return self.by_key[k], "path"
        # ③ 대소문자만 다른 경우. 저장소의 4건이 정확히 여기서 구제된다.
        f2 = self.by_fold.get(k.casefold())
        if f2 is not None:
            return f2, "case"
        return None, None            # ④ 새 문서

    #--------------------------------------------------------------
    # 레코드의 최신 오버라이드 가져오기
    #=> resolve() 로 키를 찾아 값을 돌려준다. 어떻게 이어졌는지도 함께 준다.
    #
    # -in: rec = 분류 레코드 dict
    #
    # -out: (기록, how) = 없으면 (None, None)
    # -out: error = 없음
    #--------------------------------------------------------------
    def for_rec(self, rec):
        key, how = self.resolve(rec)
        return (self.get(key) if key is not None else None), how


#------------------------------------------------------------------
# 이력 사전에서 이 레코드의 기록 꺼내기
#=> history 는 {파일경로: [기록,...]} 이라 색인(latest)이 찾아 준 키로 조회한다.
#   latest 와 history 는 같은 파일에서 함께 만들어지므로 키가 같다.
#
# -in: history = {파일경로: [기록,...]}
# -in: index   = OverrideIndex(= load_overrides 의 latest)
# -in: rec     = 분류 레코드
#
# -out: list = 그 문서의 결정 기록(없으면 빈 리스트)
# -out: error = 없음
#------------------------------------------------------------------
def history_for(history, index, rec):
    if not isinstance(index, OverrideIndex):
        # 색인이 아니면(옛 호출부) 종전대로 경로 정확 일치.
        return history.get((rec or {}).get("file"), [])
    key, _ = index.resolve(rec)
    return history.get(key, []) if key is not None else []


#------------------------------------------------------------------
# 레코드의 최신 오버라이드 찾기 (색인이 아니어도 동작)
#=> 화면 코드는 latest 자리에 빈 dict({})나 손으로 만든 dict 를 넘기는 자리가
#   있다. 그런 호출부까지 색인으로 바꾸게 하면 변경 범위만 커지고 얻는 것이
#   없으므로, 색인이면 매칭 순서를 쓰고 아니면 종전대로 경로 정확 일치로 찾는다.
#
# -in: latest = OverrideIndex 또는 평범한 {파일경로: 기록} dict
# -in: rec    = 분류 레코드
#
# -out: (기록, how) = 없으면 (None, None). how 는 이은 방법(색인일 때만 채워진다)
# -out: error = 없음
#------------------------------------------------------------------
def lookup(latest, rec):
    if isinstance(latest, OverrideIndex):
        return latest.for_rec(rec)
    return (latest or {}).get((rec or {}).get("file")), None
