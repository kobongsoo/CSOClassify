#------------------------------------------------------------------
# 추출 텍스트 정제
#=> 사이냅이 뽑은 원시 텍스트에는 페이지 마커(..PAGE:N), 제어문자, 과도한
#   빈 줄/공백이 섞여 있다. 임베딩 품질과 토큰 낭비를 줄이려고 이를 다듬는다.
#------------------------------------------------------------------

import re
import unicodedata

# 페이지 마커 라인: 사이냅 기본 출력의 '..PAGE:12' 같은 줄.
_PAGE_MARKER = re.compile(r"^\s*\.\.PAGE:\d+\s*$", re.MULTILINE)
# 제어문자(탭/개행 제외): 임베딩에 무의미하고 토크나이저를 흔들 수 있어 제거.
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 3줄 이상 연속 빈 줄 → 2줄로 축약(문단 경계는 보존).
_MULTI_BLANK = re.compile(r"\n{3,}")
# 줄 안의 연속 공백/탭 → 공백 1개.
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


#------------------------------------------------------------------
# 원시 텍스트 정제 (핵심)
#=> 여러 정규식으로 잡음을 제거하고 공백/개행을 정규화한다.
#    1) 유니코드 정규화(NFC) 로 한글 자모 결합 형태 통일
#    2) (옵션) 페이지 마커 라인 제거
#    3) 제어문자 제거 → 각 줄 우측 공백/연속공백 정리 → 과다 빈 줄 축약
#
# -in: text = 사이냅이 추출한 원시 텍스트
# -in: remove_page_markers = True 면 '..PAGE:N' 라인을 제거(기본 True)
#
# -out: cleaned = 정제된 텍스트(양끝 공백 제거)
# -out: error = 없음 (입력이 None 이면 빈 문자열)
#------------------------------------------------------------------
def clean_text(text, remove_page_markers=True):
    # 방어적 처리: None 이 들어와도 죽지 않고 빈 문자열을 돌려준다.
    if not text:
        return ""

    # (1) 자모가 분리된 한글 등을 완성형(NFC)으로 통일 → 토큰 일관성 확보.
    text = unicodedata.normalize("NFC", text)

    # (2) 페이지 마커는 본문이 아니므로 옵션에 따라 라인 통째로 제거.
    if remove_page_markers:
        text = _PAGE_MARKER.sub("", text)

    # (3-1) 눈에 안 보이는 제어문자 제거(탭/개행은 남겨 구조 보존).
    text = _CTRL.sub("", text)

    # (3-2) 윈도우 개행(\r\n)·맥 개행(\r)을 \n 으로 통일.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # (3-3) 줄 안 연속 공백 축약.
    text = _MULTI_SPACE.sub(" ", text)

    # (3-4) 각 줄 끝의 잉여 공백 제거.
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    # (3-5) 빈 줄이 3줄 이상 이어지면 2줄로 축약.
    text = _MULTI_BLANK.sub("\n\n", text)

    return text.strip()


#------------------------------------------------------------------
# 글자수 상한 적용 (G3 — 크기 상한의 본체)
#=> 정제된 본문이 상한보다 길면 "앞부분만" 남기고 잘라 준다. 문서를 버리는 게
#   아니라 앞부분만 보고 판단하게 하는 것이다. 부른 쪽은 반환된 truncated 를 보고
#   결과에 '일부만 봤다'는 표식을 달아야 한다.
#    1) 상한이 없거나(None/0 이하) 본문이 상한 이하면 그대로 돌려준다(무해)
#    2) 넘으면 앞에서부터 limit 글자만 남긴다
#
#   [왜 앞부분인가] 등급을 정하는 신호 — 표지의 문서 종류, 머리말의 '대외비',
#   결재 스탬프, 업무분류 어휘 — 는 문서 앞쪽에 몰려 있다. 뒤를 버리는 쪽이
#   판정 손실이 가장 작다.
#   [왜 자르나] 이 길이가 뒤따르는 PII 정규식 스캔·임베딩 청크 수·데몬 IPC
#   페이로드 크기를 한꺼번에 결정한다. 여기서 유계로 만들면 셋이 같이 유계가 된다.
#
# -in: text  = 정제된 본문(clean_text 결과)
# -in: limit = 남길 최대 글자수(None 또는 0 이하면 제한 없음)
#
# -out: (text, n_orig, truncated) = 잘린(또는 그대로인) 본문, 자르기 전 글자수, 잘렸는지
# -out: error = 없음 (입력이 None 이면 ("", 0, False))
#------------------------------------------------------------------
def truncate_text(text, limit):
    # 방어적 처리: None 이 들어와도 죽지 않게 빈 문자열과 같은 취급.
    if not text:
        return "", 0, False

    n_orig = len(text)
    # 상한을 끄는 방법을 명시적으로 둔다(--no-size-limit 가 None 을 넘긴다).
    if not limit or limit <= 0 or n_orig <= limit:
        return text, n_orig, False

    return text[:limit], n_orig, True
