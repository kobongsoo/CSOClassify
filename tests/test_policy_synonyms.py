#------------------------------------------------------------------
# 배포되는 유의어 사전(doc_synonyms.yaml) 자체의 무결성 검사
#=> 이 파일만은 일부러 "진짜 사전" 을 읽는다. 다른 어휘 테스트는 사전 내용이
#   바뀌어도 깨지면 안 되지만, 여기서 보는 것은 내용이 아니라 사전이 지켜야 할
#   구조 규칙이다. 그 규칙은 사람이 사전에 한 줄 더할 때 가장 쉽게 깨진다.
#
#   [지켜야 할 것 두 가지]
#     ① 꼬리말·머리말은 처음 걸린 하나만 쓰고 멈춘다(docruleedit.synonyms_of).
#        그래서 짧은 말이 위에 있으면 긴 말은 영영 쓰이지 않는다 — 조용히
#        죽은 항목이 되므로 사람 눈으로는 못 잡는다. 코드로 잡는다.
#     ② 치환으로 만든 말에 같은 토막이 두 번 이어 붙으면 안 된다
#        ("자재관리대장" → "자재관리관리대장"). 그런 말은 어떤 문서에도 없다.
#------------------------------------------------------------------

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "ui")
if UI not in sys.path:
    sys.path.insert(0, UI)

import docruleedit as DRE   # noqa: E402

POLICY = os.path.join(UI, "policy", "doc_rule.yaml")


#------------------------------------------------------------------
# 배포 사전 읽기
#=> 테스트마다 파일을 다시 읽지 않도록 한 곳에 모아 둔다. 사전이 없으면
#   테스트를 건너뛴다 — 사전은 없어도 되는 파일이라 실패로 볼 수 없다.
#
# -in: 없음
#
# -out: dict = load_synonyms() 결과
# -out: error = 사전 파일이 없으면 pytest.skip 으로 건너뛴다
#------------------------------------------------------------------
def real_syn():
    import pytest
    syn = DRE.load_synonyms(POLICY)
    if not syn.get("tails"):
        pytest.skip("배포 사전(doc_synonyms.yaml)이 없어 건너뜀")
    return syn


#------------------------------------------------------------------
# 합쳐진 사전은 긴 꼬리말이 먼저 와야 한다
#=> load_synonyms() 는 여러 겹을 합친 뒤 길이순으로 다시 세운다. 이 재정렬이
#   깨지면 "결과보고서" 같은 긴 항목이 "보고서" 뒤로 밀려 영영 쓰이지 않는데,
#   증상이 "그 분류만 유의어가 조금 적다" 라서 사람 눈으로는 못 잡는다.
#------------------------------------------------------------------
def test_합친_사전은_긴_꼬리말이_먼저다():
    keys = list(real_syn()["tails"])
    dead = [(짧은, 긴) for i, 짧은 in enumerate(keys)
            for 긴 in keys[i + 1:] if 긴.endswith(짧은)]
    assert not dead, f"짧은 꼬리말이 위에 있어 아래 항목이 죽는다: {dead}"


#------------------------------------------------------------------
# 합쳐진 사전은 긴 머리말이 먼저 와야 한다
#=> 꼬리말과 같은 이유다(startswith 로 훑고 처음 걸린 것에서 멈춘다).
#------------------------------------------------------------------
def test_합친_사전은_긴_머리말이_먼저다():
    keys = list(real_syn()["heads"])
    dead = [(짧은, 긴) for i, 짧은 in enumerate(keys)
            for 긴 in keys[i + 1:] if 긴.startswith(짧은)]
    assert not dead, f"짧은 머리말이 위에 있어 아래 항목이 죽는다: {dead}"


#------------------------------------------------------------------
# 배포 사전으로 만든 말에 겹친 토막이 없어야 한다
#=> 사전에 한 줄 더했을 때 엉뚱한 말이 튀어나오는지 실제 이름들로 확인한다.
#   여기 적힌 이름은 사전에 없는(=그물로만 걸리는) 이름들이다.
#------------------------------------------------------------------
def test_배포사전이_겹친_말을_만들지_않는다():
    syn = real_syn()
    이름들 = ["자재관리대장", "안전관리지침", "품질검사성적서", "학자금지원제도",
              "추진전략", "시행세칙", "관련서류", "사내용지침", "분기별실적"]
    나쁜말 = []
    for 이름 in 이름들:
        for w in DRE.synonyms_of(이름, syn) + DRE.synonyms_of(이름, syn, True):
            if DRE._is_doubled(w):
                나쁜말.append((이름, w))
    assert not 나쁜말, f"같은 토막이 두 번 붙은 말이 만들어졌다: {나쁜말}"


#------------------------------------------------------------------
# 사전에 이름이 없어도 그물(꼬리말·머리말)에 걸려야 한다
#=> 이 사전의 존재 이유다. 고객사가 새 분류를 만들어도 관리자가 사전을 고치지
#   않고 쓸 수 있어야 한다. 아래 이름은 모두 aliases 에 없는 이름이다.
#------------------------------------------------------------------
def test_사전에_없는_이름도_유의어가_붙는다():
    syn = real_syn()
    이름들 = ["설비점검일지", "월간실적보고서", "물품구매요청서", "협력사평가서",
              "신규사업기획서", "기술이전계약서", "내부통제기준서", "업무협조전"]
    빈것 = [이름 for 이름 in 이름들
            if not DRE.synonyms_of(이름, syn)]
    assert not 빈것, f"그물에 걸리지 않는 이름: {빈것}"


#------------------------------------------------------------------
# 배포되는 업종 사전을 전부 확인한다
#=> 업종 사전은 앞으로 하나씩 늘어난다(금융·법률·교육 …). 새로 만든 사전이
#   공통 사전 위에 얹혔을 때 순서가 무너지거나 겹친 말을 만들어 내면, 그
#   업종 고객사에서만 조용히 규칙이 나빠진다. 파일이 늘어나도 자동으로
#   함께 검사되도록 폴더를 훑는다.
#------------------------------------------------------------------
def test_배포되는_업종_사전들이_전부_멀쩡하다(tmp_path):
    import glob
    import shutil
    # 사전은 policy/synonyms/ 에 모여 있다. 예전 배포는 규칙 파일 옆에 평평하게
    # 깔려 있어서 두 자리를 다 훑는다 — 로더(_syn_path)와 같은 규칙이다.
    폴더 = os.path.join(UI, "policy")
    자리들 = [os.path.join(폴더, DRE.SYN_DIR), 폴더]
    core = next((p for p in (os.path.join(d, "doc_synonyms.core.yaml") for d in 자리들)
                 if os.path.isfile(p)), "")
    업종들 = [f for d in 자리들 for f in glob.glob(os.path.join(d, "doc_synonyms.*.yaml"))
              if os.path.basename(f) not in ("doc_synonyms.core.yaml",
                                             "doc_synonyms.local.yaml")]
    if not (os.path.isfile(core) and 업종들):
        import pytest
        pytest.skip("업종 사전이 아직 없어 건너뜀")

    for 업종파일 in 업종들:
        업종 = os.path.basename(업종파일).split(".")[1]
        칸 = tmp_path / 업종
        칸.mkdir()
        shutil.copy(core, str(칸))
        shutil.copy(업종파일, str(칸))
        rule = 칸 / "doc_rule.yaml"
        rule.write_text("industry: {}\ndoctype_rules: []\n".format(업종),
                        encoding="utf-8")
        syn = DRE.load_synonyms(str(rule))
        assert len(syn.get("layers") or []) == 2, f"{업종}: 사전이 안 얹혔다"

        # ① 긴 꼬리말이 앞에 오는가
        keys = list(syn["tails"])
        dead = [(짧은, 긴) for i, 짧은 in enumerate(keys)
                for 긴 in keys[i + 1:] if 긴.endswith(짧은)]
        assert not dead, f"{업종}: 짧은 꼬리말이 위에 있어 아래가 죽는다 {dead}"

        # ② 그 업종 사전의 이름들로 겹친 말이 만들어지지 않는가
        나쁜말 = []
        for 이름 in list(syn["aliases"]) + list(syn["tails"]):
            for w in DRE.synonyms_of(이름, syn) + DRE.synonyms_of(이름, syn, True):
                if DRE._is_doubled(w):
                    나쁜말.append((이름, w))
        assert not 나쁜말, f"{업종}: 같은 토막이 두 번 붙은 말 {나쁜말}"

        # ③ filename_only 에 적은 넓은 말이 표제부·본문으로 새지 않는가
        샌말 = []
        for 이름, 넓은말들 in (syn.get("filename_only") or {}).items():
            v = DRE.rule_vocab(이름, syn=syn)
            for 넓은 in 넓은말들 or []:
                if 넓은 in v["head_terms"] or 넓은 in v["terms"]:
                    샌말.append((이름, 넓은))
        assert not 샌말, f"{업종}: 넓은 말이 표제부·본문으로 샜다 {샌말}"
