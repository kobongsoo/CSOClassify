#------------------------------------------------------------------
# 목록에 있으나 없는 파일이 '결과에도' 남는가 (2026-09-10)
#=> 예전에는 --filelist 가 가리킨 문서가 디스크에 없으면 결과에서 통째로
#   사라졌다. stderr 에 F4 경고는 떴지만, 받는 쪽이 읽는 것은 --out 파일이라
#   7건을 요청했는데 1건만 돌아와도 "1건 전부 성공"으로 보였다.
#   거버넌스 도구에서 '무엇이 왜 빠졌는지 알 수 없는 것'이 가장 나쁜 실패다.
#
#   [무엇을 지키나]
#     ① 없는 문서도 결과에 한 줄 남는다(error.kind="file_missing")
#     ② 목록이 준 sfile_id 가 그 줄에 있다 — 부르는 쪽이 어느 문서인지 알아야 한다
#     ③ summary.total 이 요청 건수와 같고, file_missing 으로 몇 건인지 센다
#     ④ 없는 문서에 등급을 지어내지 않는다(grade=null)
#     ⑤ 목록이 'ID 사전'일 뿐인 실행(--dir 과 함께)에서는 이 줄을 만들지 않는다
#------------------------------------------------------------------

import io
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
RULES = os.path.join(ROOT, "resources", "policy", "cso_rule.yaml")


#------------------------------------------------------------------
# 분류를 한 번 돌리고 결과 줄들을 돌려준다
#=> exe 가 아니라 소스 모듈로 돌린다(빌드 없이도 계약을 지킬 수 있게).
#
# -in: args = csoclassify 에 넘길 인자들
# -in: out  = 결과를 쓸 경로
#
# -out: (records, summary, returncode)
# -out: error = 없음
#------------------------------------------------------------------
def run(args, out):
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
               PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "csoclassify"] + args +
                       ["--rules", RULES, "--rule-only", "--out", out],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8")
    rows = [json.loads(l) for l in io.open(out, encoding="utf-8") if l.strip()]
    recs = [x for x in rows if "file" in x]
    summ = next((x["summary"] for x in rows if "summary" in x), {})
    return recs, summ, r.returncode


#------------------------------------------------------------------
# 목록 파일을 만든다
#
# -in: tmp_path = pytest 임시 폴더
# -in: rows     = [(경로, sfile_id)]
#
# -out: str = 만든 목록 파일 경로
# -out: error = 없음
#------------------------------------------------------------------
def mklist(tmp_path, rows):
    p = tmp_path / "list.jsonl"
    with io.open(str(p), "w", encoding="utf-8") as f:
        for path, sfid in rows:
            f.write(json.dumps({"path": path, "sfile_id": sfid},
                               ensure_ascii=False) + "\n")
    return str(p)


#------------------------------------------------------------------
# ①~④ 없는 문서가 결과에 남고, 번호·사유가 함께 온다
#------------------------------------------------------------------
def test_없는_파일도_결과에_남는다(tmp_path):
    real = tmp_path / "있는문서.txt"
    real.write_text("평범한 사내 문서입니다." * 20, encoding="utf-8")
    lst = mklist(tmp_path, [
        (str(real), "SF-OK-1"),
        (str(tmp_path / "없는문서.docx"), "SF-GONE-1"),
        (str(tmp_path / "이것도없음.hwp"), "SF-GONE-2"),
    ])

    recs, summ, rc = run(["--filelist", lst], str(tmp_path / "out.jsonl"))

    # ① 요청한 3건이 모두 결과에 있다(예전에는 1건만 나왔다).
    assert len(recs) == 3, [r["file"] for r in recs]

    missing = [r for r in recs if (r.get("error") or {}).get("kind") == "file_missing"]
    assert len(missing) == 2

    # ② 목록이 준 번호가 그 줄에 있다 — 없으면 어느 문서가 빠졌는지 알 수 없다.
    assert sorted(r["doc_id"] for r in missing) == ["SF-GONE-1", "SF-GONE-2"]
    for r in missing:
        assert r["doc_id_source"] == "sfile_id"
        # ④ 없는 문서에 등급을 지어내지 않는다.
        assert r["grade"] is None
        assert r["why"]["security"]["method"] == "file_missing"
        assert r["error"]["stage"] == "input"
        assert r["why"]["security"]["seed_eligible"] is False

    # ③ 요청 건수와 빠진 건수가 요약에 그대로 보인다.
    assert summ["total"] == 3
    assert summ["file_missing"] == 2

    # 종료코드로도 알 수 있어야 한다 — 결과를 파싱하지 않는 호출자를 위해서다.
    assert rc == 1


#------------------------------------------------------------------
# 다 있으면 아무것도 늘지 않는다
#=> 0건일 때 요약 칸이 생기면, 기존 요약을 읽던 쪽이 괜히 흔들린다.
#------------------------------------------------------------------
def test_다_있으면_요약이_그대로다(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("평범한 사내 문서입니다." * 20, encoding="utf-8")
    lst = mklist(tmp_path, [(str(a), "SF-1")])

    recs, summ, rc = run(["--filelist", lst], str(tmp_path / "out.jsonl"))
    assert len(recs) == 1
    assert summ["total"] == 1
    assert "file_missing" not in summ, "0건일 때는 칸을 만들지 않는다"
    assert rc == 0


#------------------------------------------------------------------
# ⑤ 목록이 'ID 사전'일 뿐이면 없는 파일을 세지 않는다
#=> --dir 로 대상을 정한 실행에서 목록은 번호를 붙이는 사전 역할만 한다.
#   그 목록에 이번 폴더 밖 문서가 적혀 있다고 '빠졌다'고 하면 엉뚱한 경고다.
#------------------------------------------------------------------
def test_ID사전으로_쓸_때는_빠졌다고_하지_않는다(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    a = docs / "a.txt"
    a.write_text("평범한 사내 문서입니다." * 20, encoding="utf-8")
    # 목록에는 폴더 밖의 없는 문서까지 적혀 있다.
    lst = mklist(tmp_path, [
        (str(a), "SF-1"),
        (str(tmp_path / "폴더밖없는문서.docx"), "SF-2"),
    ])

    recs, summ, rc = run(["--dir", str(docs), "--filelist", lst],
                         str(tmp_path / "out.jsonl"))
    assert len(recs) == 1, "폴더 안 문서만 결과에 있어야 한다"
    assert "file_missing" not in summ
    assert rc == 0
