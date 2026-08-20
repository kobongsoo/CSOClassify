#------------------------------------------------------------------
# HWPX(zip+OWPML) 텍스트 추출기 (설계서 CSO_HybridParse §8)
#=> HWPX 는 ZIP 컨테이너 안에 OWPML(XML)로 본문을 담는 한글 신형 포맷이다.
#   사이냅(snf_exe) 없이도 파이썬 표준 zipfile 만으로 Contents/section*.xml 의
#   <hp:t> 텍스트 런을 뽑아 본문을 만든다. 외부 라이브러리 의존이 전혀 없어
#   어떤 배포 환경에서도 바로 동작한다.
#   (실측: snf 와 sim 1.00, 파일당 1~6ms — tests/compare_extractors_4way.ipynb)
#------------------------------------------------------------------

import re
import zipfile

from .base import TextExtractor, ExtractError

# 본문 텍스트 런: <hp:t>...</hp:t> (여러 줄 대응 DOTALL). 그룹1이 텍스트.
_HP_T = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)
# 런 내부에 남을 수 있는 인라인 태그(<hp:...> 등) 제거용.
_INNER_TAG = re.compile(r"<[^>]+>")
# 본문이 들어있는 파일만 대상: Contents/section0.xml, section1.xml, ...
_SECTION = re.compile(r"Contents/section\d+\.xml$", re.I)
# XML 기본 엔티티 복원표(&amp; 는 다른 엔티티 복원 후 마지막에 처리).
_ENTITIES = [("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")]


#------------------------------------------------------------------
# section 번호로 정렬하기 위한 키
#=> "Contents/section10.xml" 이 "section2.xml" 보다 뒤에 오도록, 파일명 속
#   숫자를 뽑아 정수로 비교한다(문자열 정렬이면 10 이 2 앞에 오는 오류 방지).
#
# -in: name = zip 내부 항목 이름(예: "Contents/section3.xml")
#
# -out: int = section 번호(숫자 못 찾으면 0)
# -out: error = 없음
#------------------------------------------------------------------
def _section_no(name):
    m = re.search(r"section(\d+)\.xml$", name, re.I)
    # 숫자를 못 찾는 예외적 이름은 0 으로 두어 맨 앞에 오게 한다.
    return int(m.group(1)) if m else 0


class HwpxZipExtractor(TextExtractor):
    #------------------------------------------------------------------
    # 문서 → 원시 텍스트 (핵심)
    #=> HWPX(zip) 를 열어 section XML 들의 <hp:t> 런을 순서대로 이어 붙인다.
    #    1) zip 으로 열고, 이름이 Contents/section<n>.xml 인 항목만 번호순 정렬
    #    2) 각 XML 에서 <hp:t> 런을 뽑아 내부 잔여 태그 제거 → 리스트에 축적
    #    3) XML 엔티티(&lt; 등)를 원문자로 복원
    #    4) 런을 개행으로 이어 반환(정제=clean_text 는 상위 파이프라인 담당)
    #   ※ save_dir 은 이 추출기가 직접 저장하지 않는다 — 하이브리드 추출기가
    #     '승자 엔진' 텍스트만 한 번 저장하도록 위임한다(중복 저장 방지, §base).
    #
    # -in: input_path = 추출할 .hwpx 경로
    # -in: save_dir   = (호환용) 여기서는 사용 안 함. 저장은 상위(HybridExtractor)가 담당
    #
    # -out: text = 추출된 본문 텍스트(런이 0개면 빈 문자열 → 상위가 폴백 판단)
    # -out: error = 파일 없음/ZIP 아님/손상 등은 ExtractError(→ HybridExtractor 가 snf 폴백)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        parts = []
        try:
            # zip 컨테이너로 연다. HWPX 가 아니거나 손상되면 여기서 예외 → 폴백 신호.
            with zipfile.ZipFile(input_path) as z:
                # 본문 section 만, 번호 순서대로 처리해 문서 흐름을 유지한다.
                names = sorted((n for n in z.namelist() if _SECTION.search(n)),
                               key=_section_no)
                for n in names:
                    # OWPML 은 UTF-8. 깨진 바이트는 대체문자로 흡수(추출 자체는 계속).
                    data = z.read(n).decode("utf-8", "replace")
                    for run in _HP_T.findall(data):
                        # 런 안에 남은 인라인 태그를 지워 순수 텍스트만 남긴다.
                        parts.append(_INNER_TAG.sub("", run))
        except FileNotFoundError:
            # 경로는 호출부가 붙이므로 여기선 사유만(다른 추출기와 메시지 관례 통일).
            raise ExtractError("입력 파일 없음")
        except (zipfile.BadZipFile, OSError) as e:
            # ZIP 이 아니거나 읽기 실패 → 상위가 snf 로 폴백하도록 실패로 알린다.
            raise ExtractError(f"HWPX(zip) 열기 실패: {type(e).__name__}")

        text = "\n".join(parts)
        # XML 엔티티를 원문자로 되돌린다(&amp; 는 마지막이라 이중복원 안 됨).
        for a, b in _ENTITIES:
            text = text.replace(a, b)
        return text
