#------------------------------------------------------------------
# 정책 파일 사본 동기화 도구(scripts/sync_policy.py) 시험
#=> 임시 폴더에 원본·사본 폴더 구조를 만들어 검사·복사·보호 동작을 확인한다.
#   실제 저장소 파일은 건드리지 않는다.
#------------------------------------------------------------------

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))

import sync_policy as SP  # noqa: E402


#------------------------------------------------------------------
# 파일 쓰기 도우미
#=> 폴더를 만들며 바이트를 그대로 쓴다(줄끝을 시험이 정하게).
#
# -in: root = 임시 최상위 폴더
# -in: rel  = 상대경로
# -in: data = 쓸 바이트
#
# -out: 없음
# -out: error = 쓰기 실패 시 OSError
#------------------------------------------------------------------
def _w(root, rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


#------------------------------------------------------------------
# 파일 읽기 도우미
#
# -in: root = 임시 최상위 폴더
# -in: rel  = 상대경로
#
# -out: bytes = 파일 내용
# -out: error = 없으면 OSError
#------------------------------------------------------------------
def _r(root, rel):
    with open(os.path.join(root, rel), "rb") as f:
        return f.read()


#------------------------------------------------------------------
# cso_rule.yaml 사본만 있는 최소 구조 만들기
#=> 원본(resources/policy) + 사본 둘(Rust 판·화면). 배포 폴더는 만들지 않는다 —
#   없는 배포 폴더는 건너뛰어야 한다는 것도 함께 본다.
#
# -in: root = 임시 최상위 폴더
# -in: src  = 원본 내용
# -in: rust = Rust 판 사본 내용
# -in: ui   = 화면 사본 내용
#
# -out: 없음
# -out: error = 없음
#------------------------------------------------------------------
def _tree(root, src, rust, ui):
    _w(root, "resources/policy/cso_rule.yaml", src)
    _w(root, "Rust/resources/policy/cso_rule.yaml", rust)
    _w(root, "ui/policy/cso_rule.yaml", ui)


#------------------------------------------------------------------
# 줄끝만 다른 사본은 같은 파일이다
#=> 저장소 쪽은 git autocrlf 로 CRLF, 화면이 쓴 쪽은 LF 인 것이 정상이다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_line_endings_only_is_same(tmp_path):
    root = str(tmp_path)
    _tree(root, b"version: a\r\nx: 1\r\n", b"version: a\nx: 1\n", b"version: a\r\nx: 1\r\n")
    rows = SP.run(root, file="cso_rule.yaml")
    assert {r["state"] for r in rows} == {"same"}
    # 배포 폴더가 없으니 사본은 둘만 본다.
    assert {r["dst"] for r in rows} == {"Rust/resources/policy", "ui/policy"}
    assert SP.main(["--root", root, "--file", "cso_rule.yaml"]) == 0


#------------------------------------------------------------------
# 기록이 없는 첫 실행 — 다른 사본은 덮지 않는다
#=> 사본이 화면에서 고친 것일 수 있으므로, 지난 동기화 기록이 없으면 '사본에서 고쳤을 수
#   있음'으로 보고 --force 없이는 건드리지 않는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_first_run_protects_different_copy(tmp_path):
    root = str(tmp_path)
    _tree(root, b"version: b\n", b"version: a\n", b"version: b\n")
    assert SP.main(["--root", root, "--file", "cso_rule.yaml"]) == 1
    assert SP.main(["--root", root, "--file", "cso_rule.yaml", "--apply"]) == 2
    assert _r(root, "Rust/resources/policy/cso_rule.yaml") == b"version: a\n"


#------------------------------------------------------------------
# 지난 동기화 그대로인 사본은 원본이 바뀌면 복사된다 — 줄끝·백업 유지
#=> 한 번 --force 로 맞춘 뒤 원본만 고치면, 다음 --apply 는 확인 없이 복사한다.
#   사본의 CRLF 는 지키고, 덮기 전 사본은 _backup/policy-sync 아래에 남는다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_synced_copy_follows_source(tmp_path):
    root = str(tmp_path)
    _tree(root, b"version: b\n", b"version: a\r\n", b"version: b\n")
    SP.run(root, apply=True, force=True, file="cso_rule.yaml", stamp="t1")
    # 사본의 줄끝(CRLF)을 지켜서 썼다.
    assert _r(root, "Rust/resources/policy/cso_rule.yaml") == b"version: b\r\n"
    assert _r(root, "_backup/policy-sync/t1/Rust/resources/policy/cso_rule.yaml") == b"version: a\r\n"

    _w(root, "resources/policy/cso_rule.yaml", b"version: c\n")
    rows = SP.run(root, file="cso_rule.yaml")
    assert {r["state"] for r in rows} == {"differ"}
    assert SP.main(["--root", root, "--file", "cso_rule.yaml", "--apply"]) == 0
    assert _r(root, "Rust/resources/policy/cso_rule.yaml") == b"version: c\r\n"
    assert _r(root, "ui/policy/cso_rule.yaml") == b"version: c\n"


#------------------------------------------------------------------
# 동기화 뒤 사본을 손으로 고치면 다시 보호된다
#=> 화면에서 규칙을 저장한 경우다. 원본이 바뀌어도 그 사본은 덮지 않아야 한다.
#
# -in: tmp_path = pytest 임시 폴더
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_copy_edited_after_sync_is_protected(tmp_path):
    root = str(tmp_path)
    _tree(root, b"version: a\n", b"version: a\n", b"version: a\n")
    SP.run(root, apply=True, file="cso_rule.yaml")          # 같은 사본의 지문을 기록
    _w(root, "ui/policy/cso_rule.yaml", b"version: a-ui\n")  # 화면에서 고침
    _w(root, "resources/policy/cso_rule.yaml", b"version: b\n")
    rows = {r["dst"]: r["state"] for r in SP.run(root, file="cso_rule.yaml")}
    assert rows == {"Rust/resources/policy": "differ", "ui/policy": "edited"}
    assert SP.main(["--root", root, "--file", "cso_rule.yaml", "--apply"]) == 2
    assert _r(root, "Rust/resources/policy/cso_rule.yaml") == b"version: b\n"
    assert _r(root, "ui/policy/cso_rule.yaml") == b"version: a-ui\n"
    # 차이 보기는 아무것도 쓰지 않고 사본에만 있는 줄을 보여 준다.
    diff = SP.render_diff(root, SP.run(root, file="cso_rule.yaml"))
    assert "+version: a-ui" in diff
    assert _r(root, "ui/policy/cso_rule.yaml") == b"version: a-ui\n"


#------------------------------------------------------------------
# 회사 겹 유의어는 제품 소스 폴더로 복사하지 않는다
#=> 고객 제품명이 들어가는 파일이라 공개 저장소 쪽 폴더에 생기면 안 된다(dc3fbae).
#
# -in: 없음
# -out: 없음(assert)
# -out: error = 실패 시 AssertionError
#------------------------------------------------------------------
def test_local_synonyms_never_go_to_product_source():
    for _group, rel, _src, dsts in SP.MANIFEST:
        if rel.endswith("doc_synonyms.local.yaml"):
            assert "resources/policy" not in dsts
            assert "Rust/resources/policy" not in dsts
