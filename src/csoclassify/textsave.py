"""추출 본문(.txt) 보존 — 설계: plan/추출텍스트-저장-설계-20260907.html"""

import io
import json
import os

# 색인 파일 이름 — 저장 폴더 안에서 hash 이름과 원본 경로를 잇는 유일한 다리다.
# '.txt' 가 아니어야 한다: 받는 쪽이 폴더를 *.txt 로 훑을 때 색인이 딸려 들어가면 안 된다.
INDEX_NAME = "_index.jsonl"

# 폴더만 발견한 사람을 위한 안내문. 이것도 .txt 가 아니다(같은 이유).
README_NAME = "_README.md"

README_TEXT = """# 이 폴더에 무엇이 들어 있나

MpowerClassify 가 `--textsave` 로 남긴 **문서 본문 발췌**입니다.
파일 이름은 원본 파일의 SHA-256 이고, 내용은 분류할 때 실제로 읽은 텍스트입니다.

## 취급 주의

**원문이 그대로 들어 있습니다 — 개인정보·기밀이 포함될 수 있습니다.**
보안등급 C(기밀) 문서의 본문도 걸러내지 않고 그대로 남깁니다.
공유 폴더·백업·소스 저장소에 올리지 마세요.

## 어느 원본인지 찾으려면

`_index.jsonl` 을 보세요. 한 줄에 한 문서이며 `hash`·`txt`·`file` 로 이어집니다.
"""


#------------------------------------------------------------------
# 저장 폴더 준비 (실행 시작 전 1회)
#=> 폴더를 만들고 '정말 쓸 수 있는지'까지 실제로 써 보며 확인한다.
#   문서 2,000건을 다 돌린 뒤에 "한 건도 저장 못 했다"를 알게 되면 안 되므로,
#   시작 전에 확인해서 안 되면 그 자리에서 끝낸다(설계 9장).
#    1) 폴더 생성(이미 있으면 그대로)
#    2) 임시 파일을 하나 써 보고 지운다 — 권한·읽기전용·디스크 문제를 여기서 잡는다
#    3) 안내문(_README.md)을 없을 때만 만든다
#
# -in: save_dir = 저장 폴더 경로(상대경로면 절대경로로 바꿔 돌려준다)
#
# -out: path = 절대경로로 정규화된 저장 폴더
# -out: error = 만들 수 없거나 쓸 수 없으면 OSError 전파(부르는 쪽이 bad_args 로 끝낸다)
#------------------------------------------------------------------
def prepare(save_dir):
    # 데몬은 작업 폴더가 달라서, 상대경로를 그대로 넘기면 엉뚱한 데 쌓인다(설계 11장).
    path = os.path.abspath(save_dir)
    os.makedirs(path, exist_ok=True)
    # '만들어졌다'와 '쓸 수 있다'는 다르다 — 읽기전용 폴더도 makedirs 는 통과한다.
    probe = os.path.join(path, ".__write_test")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("ok")
    os.remove(probe)
    # 폴더만 발견한 제3자를 위한 경고. 이미 있으면 건드리지 않는다(사람이 고쳤을 수 있다).
    readme = os.path.join(path, README_NAME)
    if not os.path.exists(readme):
        with io.open(readme, "w", encoding="utf-8", newline="") as f:
            f.write(README_TEXT)
    return path


#------------------------------------------------------------------
# 본문 한 건 저장
#=> 파일 이름을 원본의 SHA-256 으로 삼는다. 그래서 내용이 같은 문서가 여러 경로에
#   있어도 .txt 는 한 개만 생기고(중복 제거), 결과 레코드의 hash 칸으로 바로 찾을 수 있다.
#    1) 같은 이름이 이미 있으면 내용까지 확인한다 — 같으면 다시 안 쓰고(중복 제거),
#       다르면 남의 본문을 내 것인 양 가리키지 않도록 오류로 알린다
#    2) 없으면 UTF-8 로 쓴다(개행은 \n 그대로 — 실행 환경이 달라도 파일이 같아야 한다)
#
# -in: save_dir = prepare() 가 돌려준 폴더
# -in: sha      = 원본 파일의 SHA-256 (레코드의 hash 와 같은 값)
# -in: text     = 저장할 본문(정제·절단까지 끝난, 도구가 실제로 판정에 쓴 텍스트)
#
# -out: (name, deduped) = 저장된 파일 이름, 이미 있어서 안 썼으면 True
# -out: error = 쓰기 실패 시 OSError 전파(부르는 쪽이 문서 한 건만 건너뛴다)
#------------------------------------------------------------------
def save(save_dir, sha, text):
    name = f"{sha}.txt"
    dest = os.path.join(save_dir, name)
    if os.path.exists(dest):
        # 같은 해시 = 같은 '원본 파일'. 그런데 저장하는 것은 원본이 아니라
        # *추출한 본문*이고, 파이썬 판과 Rust 판은 파서가 달라 같은 문서에서도
        # 본문이 다르게 나온다(실측: 같은 pptx 가 9,312 / 9,330 바이트).
        # 그러니 "이미 있으니 됐다"로 넘기면, 다른 엔진이 만든 남의 본문을
        # 내 것인 양 가리키게 된다 — 조용한 거짓말이다. 내용을 확인한다.
        with io.open(dest, "r", encoding="utf-8", newline="") as f:
            old = f.read()
        if old == text:
            return name, True
        raise OSError("같은 해시의 다른 본문이 이미 있습니다 — 다른 엔진(Rust 판)이나 "
                      "다른 버전으로 만든 폴더로 보입니다. 폴더를 나누세요")
    # newline="" 로 파이썬이 \n 을 \r\n 으로 바꾸지 못하게 막는다 — 그래야
    # 윈도우·리눅스, 파이썬·Rust 가 모두 같은 바이트를 낸다.
    with io.open(dest, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return name, False


#------------------------------------------------------------------
# 색인 한 줄 덧붙이기
#=> hash 이름만으로는 사람이 어느 문서인지 못 알아본다. 그 다리를 놓는 파일이다.
#   덮어쓰지 않고 이어붙인다 — 어제 돌린 색인을 오늘 실행이 말없이 지우는 것이
#   더 위험하기 때문이다(설계 7장).
#
# -in: save_dir  = prepare() 가 돌려준 폴더
# -in: sha       = 원본 SHA-256
# -in: name      = 저장된 .txt 이름
# -in: file      = 원본 표시 경로(압축 내부면 '압축경로/내부경로')
# -in: doc_id    = 문서 식별자(없으면 None)
# -in: chars     = 저장한 글자 수
# -in: truncated = G3 로 뒤가 잘린 본문인가
# -in: ts        = 저장 시각 문자열(레코드의 ts 와 같은 모양)
#
# -out: 없음
# -out: error = 쓰기 실패 시 OSError 전파
#------------------------------------------------------------------
def index_append(save_dir, sha, name, file, doc_id, chars, truncated, ts):
    row = {"hash": sha, "txt": name, "file": file, "doc_id": doc_id,
           "chars": chars, "truncated": bool(truncated), "ts": ts}
    with io.open(os.path.join(save_dir, INDEX_NAME), "a",
                 encoding="utf-8", newline="") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
