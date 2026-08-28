"""화면 설정(파일 위치 등)을 settings.yaml 에 담아 두고 다시 불러오는 모듈.

예전에는 '고급 — 파일 위치' 에서 고친 경로가 session_state 에만 있어서, 브라우저를
새로 고치거나 streamlit 을 다시 띄우면 전부 기본값으로 돌아갔다. 관리자가 매번
같은 경로를 다시 입력해야 했다. 이 모듈이 그 값을 파일에 적어 두고 다음 실행 때
그대로 되살린다.
"""

import os
import yaml

# 설정 파일 이름. ui 폴더(=app.py 옆)에 둔다 — 결과·기준문서 파일들과 같은 자리라
# "이 화면이 쓰는 것들"이 한곳에 모인다.
SETTINGS_FILENAME = "settings.yaml"

# 설정 파일에 담을 키. default_paths() 가 만드는 키와 같아야 한다. 화이트리스트로
# 두는 이유: 파일에 엉뚱한 키가 섞여 들어와도 화면 상태를 오염시키지 않게 하려고.
KEYS = (
    "folder",        # ① 분류할 폴더
    "result",        # 분류 결과 jsonl
    "override",      # 내가 고친 기록
    "seed",          # 기준 문서(seed)
    "seed_audit",    # 기준 문서 변경 기록
    "rules",         # 보안등급 기준 파일(cso_rules.yaml)
    "taxonomy",      # 회사 분류 체계(doc_taxonomy.yaml)
    "export_input",  # 회사 분류 체계 원본(MpowerV11 에서 내보낸 JSON)
    "doc_rules",     # 업무분류 기준 파일(doc_rule.yaml)
    "cmd",           # 분류 실행 명령
    "pythonpath",    # 모듈 실행 시 PYTHONPATH(cmd 와 짝)
)


#------------------------------------------------------------------
# 설정 파일 경로
#=> settings.yaml 을 어디서 읽고 쓸지 정한다.
#    1) 환경변수 CSOCLASSIFY_UI_SETTINGS 가 있으면 그 경로를 그대로 쓴다
#       (여러 사람이 같은 UI 를 각자 설정으로 띄울 때 쓰라고 열어 둔다)
#    2) 없으면 이 파일(ui 폴더) 옆의 settings.yaml
#   반드시 절대경로로 만든다 — streamlit 을 어느 폴더에서 띄웠느냐에 따라 설정이
#   엉뚱한 곳에 저장되면 "고쳤는데 안 불러와진다"가 된다.
#
# -in: 없음
#
# -out: path = settings.yaml 절대경로
# -out: error = 없음
#------------------------------------------------------------------
def settings_path():
    env = os.environ.get("CSOCLASSIFY_UI_SETTINGS")
    if env:
        return os.path.abspath(env)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILENAME)


#------------------------------------------------------------------
# 저장된 설정 읽기(원본 그대로)
#=> settings.yaml 을 읽어 dict 로 준다. 파일이 없거나 깨졌어도 예외를 올리지
#   않는다 — 설정 파일 하나 때문에 화면이 아예 안 뜨면 안 되기 때문이다.
#   깨진 파일은 '빈 설정'으로 보고 기본값으로 굴러가게 둔다.
#
# -in: 없음
#
# -out: dict = 저장된 값(파일 없음·깨짐·형식 이상이면 빈 dict)
# -out: error = 없음(모든 실패를 빈 dict 로 환원)
#------------------------------------------------------------------
def load_raw():
    p = settings_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return {}
    # 최상위가 dict 가 아니면(빈 파일이면 None) 쓸 수 없는 형식이다.
    if not isinstance(data, dict):
        return {}
    return data.get("paths") if isinstance(data.get("paths"), dict) else data


#------------------------------------------------------------------
# 기본값 위에 저장된 설정을 덮어 최종 경로 묶음 만들기
#=> 화면이 실제로 쓸 값을 만든다. "기본값을 깔고, 저장된 것만 덮는다"는 순서가
#   중요하다 — 나중에 키가 하나 늘어도 옛 settings.yaml 을 쓰던 사람이 그 키만
#   기본값으로 자연스럽게 받는다(파일을 지우거나 고칠 필요가 없다).
#   값이 빈 문자열이면 '저장 안 함'으로 보고 기본값을 살린다. 단 folder 는
#   비워 두는 것도 뜻이 있으므로(아직 안 고름) 빈 값을 그대로 존중한다.
#
# -in: defaults = default_paths() 결과(기본 경로 묶음)
#
# -out: dict = defaults 와 같은 키를 가진 최종 설정
# -out: error = 없음
#------------------------------------------------------------------
def load_paths(defaults):
    saved = load_raw()
    out = dict(defaults)
    for k in KEYS:
        if k not in saved:
            continue
        v = saved[k]
        if not isinstance(v, str):
            continue
        v = v.strip()
        # folder 는 "아직 안 골랐음"을 빈 문자열로 표현하므로 빈 값도 그대로 받는다.
        if v or k == "folder":
            out[k] = v
    return out


#------------------------------------------------------------------
# 지금 설정을 settings.yaml 에 쓰기
#=> KEYS 에 있는 값만 골라 저장한다.
#    1) 임시 파일에 먼저 쓰고 os.replace 로 바꿔치기 — 쓰다가 죽어도 기존 설정이
#       반쪽짜리로 망가지지 않는다
#    2) allow_unicode 로 한글 경로가 \uXXXX 로 뭉개지지 않게 한다
#
# -in: paths = 저장할 경로 묶음(dict). KEYS 밖의 키는 무시한다
#
# -out: path = 저장한 파일 경로(실패하면 None)
# -out: error = 쓰기 실패 시 None 반환(예외를 올리지 않는다 — 설정 저장이 안 됐다고
#        분류 작업까지 막을 이유는 없다. 호출부가 None 을 보고 화면에 알린다)
#------------------------------------------------------------------
def save_paths(paths):
    p = settings_path()
    body = {k: (paths.get(k) or "") for k in KEYS}
    doc = {
        "_comment": "문서자동분류 화면 설정 — '고급 · 파일 위치'에서 고치면 자동 저장됩니다.",
        "paths": body,
    }
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, p)
    except OSError:
        # 임시 파일이 남아 있으면 지운다(다음 저장 때 방해되지 않게).
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    return p


#------------------------------------------------------------------
# 저장된 값과 달라졌는지 보고, 달라졌을 때만 쓰기
#=> streamlit 은 화면을 건드릴 때마다 스크립트를 통째로 다시 돌린다. 매번 파일을
#   쓰면 디스크를 쓸데없이 두드리므로, 직전에 저장한 내용과 다를 때만 쓴다.
#   결과를 세 갈래로 분명히 돌려준다 — 호출부가 "안 바뀐 것"과 "쓰다 실패한 것"을
#   헷갈리지 않아야 엉뚱한 경고를 띄우지 않는다.
#
# -in: paths = 지금 화면이 들고 있는 경로 묶음
# -in: last  = 직전에 저장했다고 알고 있는 값(dict 또는 None)
#
# -out: (status, snapshot) = status 는 "unchanged"(바뀐 것 없음) ·
#        "saved"(이번에 저장함) · "failed"(바뀌었는데 쓰기 실패) 중 하나.
#        snapshot 은 '지금 저장돼 있는 상태'로, 호출부가 다음 비교에 쓸 dict
# -out: error = 없음(쓰기 실패는 status="failed" 로 표현)
#------------------------------------------------------------------
def save_if_changed(paths, last):
    now = {k: (paths.get(k) or "") for k in KEYS}
    if last is not None and now == last:
        return "unchanged", last
    if save_paths(paths) is None:
        # 실패했으면 스냅샷을 갱신하지 않는다 — 다음 기회에 다시 시도하게 둔다.
        return "failed", last
    return "saved", now
