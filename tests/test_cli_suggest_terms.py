# -*- coding: utf-8 -*-
#------------------------------------------------------------------
# CLI 배선 — 규칙 단어 제안 모드(--suggest-terms) 시험
#=> 후보를 뽑는 계산 자체는 test_termsuggest.py 가 본다. 여기서는 '배선'만 본다 —
#   인자가 제대로 이어지는지, 재료가 없을 때 사람이 읽을 안내로 끝나는지,
#   그리고 무엇보다 <b>규칙 파일을 건드리지 않는지</b>.
#
#   [이 모드는 문서도 모델도 읽지 않는다] 그래서 무거운 파이프라인 없이
#   임시 폴더의 작은 재료만으로 끝까지 돌릴 수 있다.
#------------------------------------------------------------------

import hashlib
import json

from csoclassify import cli
from csoclassify import config
from csoclassify import errcodes


#------------------------------------------------------------------
# 재료 한 벌 만들기 (시험용)
#=> seed·검토 이력·저장된 본문·본문 색인을 임시 폴더에 만든다.
#   --textsave 가 실제로 남기는 모양과 같게 둔다(파일 이름 = SHA-256, _index.jsonl).
#
# -in: tmp_path = pytest 임시 폴더
# -in: docs     = [(폴더, 파일이름, dc_id, 본문)] 목록
# -in: as_seed  = True 면 class_seed.jsonl 로, False 면 cso_override.jsonl 로 넣는다
#
# -out: dict = {"seeds","overrides","text"} 경로들
# -out: error = 없음
#------------------------------------------------------------------
def make_material(tmp_path, docs, as_seed=False):
    text_dir = tmp_path / "text"
    text_dir.mkdir(exist_ok=True)
    seeds, overs, index = [], [], []
    for folder, name, node, text in docs:
        path = f"D:/분류함/{folder}/{name}"
        sha = hashlib.sha256(path.encode()).hexdigest()
        (text_dir / f"{sha}.txt").write_text(text, encoding="utf-8")
        index.append({"hash": sha, "txt": f"{sha}.txt", "file": path,
                      "doc_id": sha[:40], "chars": len(text), "truncated": False,
                      "ts": "2026-09-16 10:00:00"})
        if as_seed:
            seeds.append({"v": 3, "file": path, "hash": sha, "doctype": [node],
                          "approved_by": "kim", "ts": "2026-09-16 10:00:00"})
        else:
            # 검토 이력에는 hash 칸이 없다 — 본문은 색인으로 찾아야 한다.
            overs.append({"file": path, "doc_id": sha[:40], "axis": "doctype",
                          "confirmed": [node], "rejected": [], "reviewer": "kim"})
    dump = lambda rows: "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    (tmp_path / "class_seed.jsonl").write_text(dump(seeds), encoding="utf-8")
    (tmp_path / "cso_override.jsonl").write_text(dump(overs), encoding="utf-8")
    (text_dir / "_index.jsonl").write_text(dump(index), encoding="utf-8")
    return {"seeds": str(tmp_path / "class_seed.jsonl"),
            "overrides": str(tmp_path / "cso_override.jsonl"),
            "text": str(text_dir)}


#------------------------------------------------------------------
# 제안 모드를 돌린다 (시험용)
#=> 규칙·분류체계는 일부러 '없는 경로'를 준다. 시험이 배포된 정책 파일을
#   읽으면 그 파일이 바뀔 때마다 결과가 달라진다.
#
# -in: mat   = make_material 결과
# -in: node  = 훑을 분류(또는 "all")
# -in: extra = 덧붙일 인자 목록
# -in: tmp_path = 임시 폴더(없는 규칙 경로를 만들 자리)
#
# -out: int = 종료코드
# -out: error = 없음
#------------------------------------------------------------------
def run(mat, node, tmp_path, *extra):
    return cli.main(["--suggest-terms", node,
                     "--seeds", mat["seeds"],
                     "--overrides", mat["overrides"],
                     "--text-dir", mat["text"],
                     "--doc-rules", str(tmp_path / "없는규칙.yaml"),
                     "--taxonomy", str(tmp_path / "없는체계.yaml"),
                     *extra])


# 보고서 갈래 세 가지가 여러 폴더에 흩어진, 실제에 가까운 재료.
REPORT_DOCS = [
    ("11_정부/a", "1_품목보고서.pdf", "DC_001_003", "품목보고서\n시장 현황분석 결과."),
    ("11_정부/a", "2_품목보고서.pdf", "DC_001_003", "품목보고서\n현황분석 내용."),
    ("11_정부/b", "3_품목보고서.pdf", "DC_001_003", "품목보고서\n현황분석 표."),
    ("12_시장/a", "실태조사 요약.hwp", "DC_001_003", "실태조사 요약\n현황분석 중심."),
    ("12_시장/b", "국내 실태조사.docx", "DC_001_003", "실태조사 정리\n현황분석 요약."),
    # 실태조사도 근거 하한(문서 3건·폴더 2곳)을 넘겨야 후보가 된다.
    ("13_대외/a", "해외 실태조사.pptx", "DC_001_003", "실태조사 개요\n현황분석 정리."),
    ("08_규정/a", "인사규정.docx", "DC_006_002", "사내규정\n인사 관련 조항."),
    ("08_규정/b", "보안규정.docx", "DC_006_002", "사내규정\n보안 관련 조항."),
    ("08_규정/c", "회계규정.docx", "DC_006_002", "사내규정\n회계 관련 조항."),
]


#------------------------------------------------------------------
# 확정 이력만 있어도 본문이 붙고 후보가 나온다
#=> 검토 이력에는 hash 가 없어, 본문 색인으로 되찾지 못하면 전부 '본문 없음'이 된다.
#   실제 재료의 대부분이 검토 이력에서 오므로 이 길이 막히면 기능이 반쪽이 된다.
#------------------------------------------------------------------
def test_검토이력만_있어도_본문이_붙는다(tmp_path, capsys):
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    assert run(mat, "DC_001_003", tmp_path) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "품목보고서" in out
    assert "실태조사" in out
    # 본문을 못 찾았으면 '본문 없음 N건'이 찍힌다. 찍히면 안 된다.
    assert "본문 없음" not in out


#------------------------------------------------------------------
# 본문 폴더를 안 주면 제목·파일 이름만으로 돌고, 몇 건인지 알린다
#------------------------------------------------------------------
def test_본문폴더_없이도_돈다(tmp_path, capsys):
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    code = cli.main(["--suggest-terms", "DC_001_003",
                     "--seeds", mat["seeds"], "--overrides", mat["overrides"],
                     "--doc-rules", str(tmp_path / "없는규칙.yaml"),
                     "--taxonomy", str(tmp_path / "없는체계.yaml")])
    assert code == config.EXIT_OK
    out = capsys.readouterr().out
    assert "본문 없음 6건" in out
    assert "품목보고서" in out          # 파일 이름에 있으므로 여전히 나온다


#------------------------------------------------------------------
# all 이면 확정 문서가 있는 분류를 모두 훑는다
#------------------------------------------------------------------
def test_all_은_분류를_모두_훑는다(tmp_path, capsys):
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    assert run(mat, "all", tmp_path) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "DC_001_003" in out
    assert "DC_006_002" in out


#------------------------------------------------------------------
# 재료가 모자란 분류는 까닭을 말한다
#------------------------------------------------------------------
def test_재료가_모자라면_까닭을_말한다(tmp_path, capsys):
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    assert run(mat, "DC_006_002", tmp_path, "--suggest-min-docs", "5") == config.EXIT_OK
    assert "5건부터 제안합니다" in capsys.readouterr().out


#------------------------------------------------------------------
# 재료 파일이 하나도 없으면 사람이 읽을 안내로 끝난다
#=> 스택 트레이스만 남기면 무엇을 지정해야 하는지 알 수 없다.
#------------------------------------------------------------------
def test_재료가_없으면_안내하고_끝낸다(tmp_path, capsys):
    code = cli.main(["--suggest-terms", "all",
                     "--seeds", str(tmp_path / "없음.jsonl"),
                     "--overrides", str(tmp_path / "없음2.jsonl")])
    # 종료코드는 errcodes 표가 정한다 — 시험이 숫자를 따로 외우지 않게 한다.
    assert code == errcodes.exit_of("seeds_missing")
    err = capsys.readouterr().err
    assert "제안할 재료가 없습니다" in err


#------------------------------------------------------------------
# 규칙 파일을 절대 고치지 않는다
#=> 이 모드의 약속이다. 보여 주기만 하고, 넣는 것은 사람이 화면에서 한다.
#------------------------------------------------------------------
def test_규칙파일을_고치지_않는다(tmp_path, capsys):
    rule_path = tmp_path / "doc_rule.yaml"
    before = ("version: test-1\ndoctype_rules:\n"
              "- id: dt_x\n  node: DC_001_003\n  title_terms: [보고서]\n")
    rule_path.write_text(before, encoding="utf-8")
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    code = cli.main(["--suggest-terms", "DC_001_003",
                     "--seeds", mat["seeds"], "--overrides", mat["overrides"],
                     "--text-dir", mat["text"], "--doc-rules", str(rule_path),
                     "--taxonomy", str(tmp_path / "없는체계.yaml")])
    assert code == config.EXIT_OK
    assert rule_path.read_text(encoding="utf-8") == before
    assert "규칙 파일은 고치지 않았습니다" in capsys.readouterr().out


#------------------------------------------------------------------
# 이미 규칙에 있는 말은 후보에서 빠진다 (규칙 파일이 실제로 읽히는가)
#------------------------------------------------------------------
def test_이미_있는_말은_빠진다(tmp_path, capsys):
    rule_path = tmp_path / "doc_rule.yaml"
    rule_path.write_text("version: test-1\ndoctype_rules:\n"
                         "- id: dt_x\n  node: DC_001_003\n  title_terms: [품목보고서]\n",
                         encoding="utf-8")
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    cli.main(["--suggest-terms", "DC_001_003",
              "--seeds", mat["seeds"], "--overrides", mat["overrides"],
              "--text-dir", mat["text"], "--doc-rules", str(rule_path),
              "--taxonomy", str(tmp_path / "없는체계.yaml")])
    out = capsys.readouterr().out
    assert "품목보고서" not in out
    assert "실태조사" in out           # 규칙에 없는 말은 그대로 나온다


#------------------------------------------------------------------
# --suggest-json 으로 기계가 읽을 결과도 남긴다
#------------------------------------------------------------------
def test_결과를_json_으로_남긴다(tmp_path):
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=False)
    out_path = tmp_path / "제안.json"
    assert run(mat, "DC_001_003", tmp_path,
               "--suggest-json", str(out_path)) == config.EXIT_OK
    got = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(got) == 1
    assert got[0]["node"] == "DC_001_003"
    assert any(c["term"] == "품목보고서" for c in got[0]["candidates"])
    # 후보마다 '어느 칸에 넣을지'가 함께 있어야 화면이 그대로 쓸 수 있다.
    assert all("fields" in c and "checked" in c for c in got[0]["candidates"])


#------------------------------------------------------------------
# 사람이 승인하지 않은 seed 는 기본으로 안 쓰고, 쓰면 경고한다
#=> 규칙→seed→규칙 자기강화를 막는 장치가 배선까지 이어져 있는지 본다.
#------------------------------------------------------------------
def test_승인안된_seed_는_기본으로_안쓴다(tmp_path, capsys):
    mat = make_material(tmp_path, REPORT_DOCS, as_seed=True)
    # 승인 표시를 지운다 — 자동 선별분과 같은 모양이 된다.
    rows = [json.loads(l) for l in
            open(mat["seeds"], encoding="utf-8") if l.strip()]
    for r in rows:
        r.pop("approved_by", None)
    open(mat["seeds"], "w", encoding="utf-8").write(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    # 검토 이력은 비워, 재료가 seed 뿐이게 만든다.
    open(mat["overrides"], "w", encoding="utf-8").write("")

    assert run(mat, "all", tmp_path) == config.EXIT_OK
    assert "확정된 문서가 한 건도 없습니다" in capsys.readouterr().out

    assert run(mat, "all", tmp_path, "--include-auto-seeds") == config.EXIT_OK
    got = capsys.readouterr()
    assert "품목보고서" in got.out
    assert "승인하지 않은 seed" in got.err     # 왜 위험한지 알린다
