#------------------------------------------------------------------
# 초기화(공장 초기화) — 앱이 만들어 낸 데이터만 지우는 순수 로직
#=> 시연·재시작을 위해 "처음 설치한 상태"로 되돌린다. 화면(Streamlit)과 분리해
#   두어, 무엇을 지우는지 눈으로 확인하고 단위테스트로 못박을 수 있게 한다.
#
#   [지우는 것] ui/ 와 ui/policy/ 바로 아래의 .json · .jsonl 파일
#     - 분류 결과(cso_result.jsonl) · 수정 기록(cso_override.jsonl)
#     - 기준 문서(class_seed.jsonl) · 그 변경 이력(class_seed_audit.jsonl)
#     - 실행 부가정보(*.meta.json) 등 앱이 스스로 만든 것 전부
#
#   [남기는 것]
#     - doc_classification_export.json  ← 회사 분류 체계 '원본'(사람이 받아 온 자산)
#     - cso_rule.yaml · doc_rule_template.yaml · synonyms/  ← 애초에 대상이 아님(.yaml/폴더)
#     - 소스 코드(.py) · 실행.bat · settings.yaml · policy_org/ 등 그 밖의 모든 것
#
#   [안전 장치] 지우는 대상을 '확장자와 폴더'로 좁혀 정한다. "목록에 없는 것을 전부
#   지운다"로 만들면 같은 폴더에 있는 app.py 같은 소스까지 지워진다 — 되돌릴 수 없는
#   사고라, 애초에 그런 코드를 쓰지 않는다.
#------------------------------------------------------------------

import os


# 지울 대상 확장자. 앱이 스스로 만들어 내는 데이터는 전부 이 둘 중 하나다.
DATA_SUFFIXES = (".json", ".jsonl")

# 확장자가 맞아도 남기는 파일 — 사람이 밖에서 받아 온 자산이라 앱이 다시 만들 수 없다.
KEEP_FILES = ("doc_classification_export.json",)

# 파일마다 "이게 뭐고, 지우면 되살릴 수 있나" — 초기화 창이 그대로 보여준다.
#   되살림 True  = 폴더를 다시 분류하면 똑같이 다시 만들어진다
#   되살림 False = 사람이 쌓아 온 판단이라 기계가 다시 만들 수 없다(영영 사라진다)
# 이 구분이 이 화면에서 가장 중요한 정보다 — 시연 전 초기화와 운영 중 초기화의
# 무게가 완전히 다르기 때문이다.
#   되살리는 방법은 파일마다 다르다. "다시 분류하면 된다"고 뭉뚱그리면 안 된다 —
#   분류 체계와 업무분류 규칙은 분류를 아무리 돌려도 돌아오지 않고, 각자 다른
#   화면에서 다시 만들어야 한다.
FILE_NOTES = {
    "cso_result.jsonl":
        ("분류 결과 — 문서마다 등급·업무분류·판단 근거", True, "폴더를 다시 분류"),
    "cso_result.jsonl.meta.json":
        ("마지막 실행 정보 — 걸린 시간·문서 수", True, "폴더를 다시 분류"),
    "grades.jsonl":
        ("분류 결과(옛 이름 파일)", True, "폴더를 다시 분류"),
    "cso_override.jsonl":
        ("관리자가 고친 등급 · 확정한 업무분류", False, ""),
    "class_seed.jsonl":
        ("기준 문서 — “이런 문서는 이 등급” 본보기와 그 벡터", False, ""),
    "class_seed_audit.jsonl":
        ("기준 문서 변경 이력 — 누가 언제 등록·해제했나", False, ""),
    "doc_taxonomy.yaml":
        ("회사 분류 체계 — 회사에서 내려받은 것을 옮겨 놓은 것", True,
         "②의 [연결] 버튼"),
    "doc_rule.yaml":
        ("업무분류 규칙 — “무엇을 계약서로 볼 것인가”", True,
         "③ 판단 기준 ▸ 업무분류에서 다시 만들기"),
}

# 목록에 없는 파일의 설명. 무엇인지 모르면 '되살릴 수 있다'고 장담하지 않는다.
UNKNOWN_NOTE = ("앱이 만든 데이터", None, "")


# 초기화하면서 함께 지울 수 있는 '만들어진' 설정 파일.
#   doc_taxonomy.yaml : doc_classification_export.json 에서 다시 만들 수 있다
#   doc_rule.yaml     : doc_rule_template.yaml + 분류 체계로 다시 만들 수 있다
# 둘 다 되살릴 수 있어서 선택으로 둔다(기본은 지움 — '처음 상태'가 되어야 하므로).
GENERATED_YAML = ("doc_taxonomy.yaml", "doc_rule.yaml")

# 초기화하면서 '남길 수도 있는' 기준 문서 저장소.
#=> 다른 지움 대상과 성격이 다르다. 분류 결과는 다시 돌리면 되살아나지만
#   기준 문서는 사람이 하나씩 확정해 쌓은 것이라 지우면 끝이다. 그런데
#   "결과만 갈아엎고 기준 문서는 그대로 두고 다시 분류하고 싶다"는 요구가
#   실제로 있어서, 이 파일만 따로 선택으로 뺀다.
#   기본은 지움 — '처음 상태로 되돌리기'라는 이 기능의 뜻을 그대로 둔다.
SEED_FILE = "class_seed.jsonl"


#------------------------------------------------------------------
# 초기화가 훑을 폴더 두 곳
#=> ui 폴더와 그 아래 policy 폴더. 하위 폴더로 더 내려가지 않는다 —
#   synonyms/ 처럼 남겨야 할 자산이 들어 있고, 깊이 들어갈수록 사고 확률만 커진다.
#
# -in: ui_dir = ui 폴더 절대경로
#
# -out: list = 훑을 폴더 경로들(존재하는 것만)
# -out: error = 없음
#------------------------------------------------------------------
def scan_dirs(ui_dir):
    cand = [ui_dir, os.path.join(ui_dir, "policy")]
    return [d for d in cand if os.path.isdir(d)]


#------------------------------------------------------------------
# 무엇을 지울지 먼저 계산한다(지우지는 않는다)
#=> 화면이 이 목록을 그대로 보여 주고, 사람이 확인한 뒤에만 run_reset 을 부른다.
#   "누르면 뭐가 사라지는지 모르는 버튼"을 만들지 않기 위한 구조다.
#
# -in: ui_dir           = ui 폴더 경로
# -in: drop_generated   = True 면 만들어진 doc_taxonomy.yaml · doc_rule.yaml 도 대상에 넣는다
# -in: drop_seed        = False 면 기준 문서(class_seed.jsonl)를 남긴다(기본 True=지움).
#                         변경 이력(class_seed_audit.jsonl)은 이 선택과 무관하게
#                         지운다 — 사람이 고른 것은 '기준 문서'이지 그 로그가 아니다
#
# -out: (targets, kept)
#        targets = [{"path","name","dir","size","why","recoverable","restore"}] (경로순)
#                  recoverable: True=다시 분류하면 복구 · False=복구 불가 · None=모름
#        kept    = [{"name","why"}] 확장자는 맞지만 남기는 파일들
# -out: error = 없음(읽을 수 없는 폴더는 건너뛴다)
#------------------------------------------------------------------
def plan_reset(ui_dir, drop_generated=True, drop_seed=True):
    targets, kept = [], []
    for d in scan_dirs(ui_dir):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue          # 폴더(synonyms 등)는 아예 건드리지 않는다
            low = name.lower()
            is_data = low.endswith(DATA_SUFFIXES)
            is_gen = drop_generated and name in GENERATED_YAML
            if not (is_data or is_gen):
                continue
            if name in KEEP_FILES:
                kept.append({"name": name, "why": "회사 분류 체계 원본 — 다시 만들 수 없습니다"})
                continue
            # 사람이 끄면 남긴다. 남긴 것도 '남는 것' 목록에 넣어 화면에 보이게
            # 한다 — 지울 목록에서 조용히 빠지기만 하면, 남았는지 지워졌는지
            # 사람이 알 수 없다.
            if name == SEED_FILE and not drop_seed:
                kept.append({"name": name, "why": "기준 문서 — 지우지 않기로 선택했습니다"})
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            why, recov, how = FILE_NOTES.get(name, UNKNOWN_NOTE)
            targets.append({"path": p, "name": name, "dir": d, "size": size,
                            "why": why, "recoverable": recov, "restore": how})
    targets.sort(key=lambda t: t["path"].lower())
    return targets, kept


#------------------------------------------------------------------
# 계산한 목록을 실제로 지운다
#=> plan_reset 이 고른 것만 지운다. 여기서 새로 파일을 찾지 않는 것이 중요하다 —
#   화면이 보여 준 목록과 실제로 지우는 것이 반드시 같아야 한다.
#
# -in: targets = plan_reset 의 첫 번째 반환값
#
# -out: (deleted, failed)
#        deleted = 지운 파일 경로 리스트
#        failed  = [(경로, 사유)] 지우지 못한 것들(다른 프로그램이 열고 있는 경우 등)
# -out: error = 없음(개별 실패는 failed 로 돌려준다)
#------------------------------------------------------------------
def run_reset(targets):
    deleted, failed = [], []
    for t in targets:
        try:
            os.remove(t["path"])
            deleted.append(t["path"])
        except OSError as e:
            failed.append((t["path"], str(e)))
    return deleted, failed


#------------------------------------------------------------------
# 지울 용량 합계를 사람이 읽는 글자로
#=> "12건 · 1.2MB" 처럼 규모를 한눈에 보여 주려고 쓴다.
#
# -in: n_bytes = 바이트 수
#
# -out: str = 예 "1.2MB" · "43KB" · "0B"
# -out: error = 없음
#------------------------------------------------------------------
def human_size(n_bytes):
    n = float(n_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" or n >= 10 else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


#------------------------------------------------------------------
# '다시 만들 수 있나' 한 마디
#=> 초기화 창의 표에 그대로 들어가는 글자. 사람이 표만 보고도 "이건 다시 분류하면
#   돌아오는구나 / 이건 영영 사라지는구나" 를 알 수 있어야 한다.
#
# -in: target = plan_reset 이 만든 한 줄(recoverable · restore 를 본다)
#
# -out: str = 예 "✅ 폴더를 다시 분류" · "⚠ 복구 안 됨" · "—"(모름)
# -out: error = 없음
#------------------------------------------------------------------
def recover_text(target):
    t = target or {}
    if t.get("recoverable") is True:
        return "✅ " + (t.get("restore") or "다시 만들 수 있음")
    if t.get("recoverable") is False:
        return "⚠ 복구 안 됨"
    return "—"
