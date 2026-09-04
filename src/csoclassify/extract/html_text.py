#------------------------------------------------------------------
# HTML 텍스트 추출기 (stdlib 전용 — 추가 의존성 0)
#=> HTML 은 태그가 본문에 섞여 있어 그대로 읽으면 마크업 노이즈가 분류에 낀다.
#   파이썬 표준 라이브러리 html.parser 로 태그를 벗기고 본문 텍스트만 남긴다.
#    · <script>/<style> 안 내용은 버린다(코드·CSS 는 본문 아님)
#    · 엔티티(&amp; 등)는 convert_charrefs 로 자동 복원
#    · 블록 태그(p/div/br/tr/li/h1~6/table…) 경계에 개행을 넣어 가독성 유지
#   외부 라이브러리 없이 동작하므로 배포 용량 증가가 사실상 없다.
#------------------------------------------------------------------

from html.parser import HTMLParser

from .base import TextExtractor, ExtractError

# 시도할 인코딩 순서(한국어 환경 고려).
_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "latin-1")
# 내용을 버릴 태그(코드/스타일).
_DROP = {"script", "style", "head", "noscript"}
# 경계에 개행을 넣을 블록 태그.
# [td·th 를 넣는 이유] 표는 tr 로만 끊으면 같은 행의 셀 값이 구분자 없이 이어붙는다.
#   <td>900101-1234568</td><td>010-1234-5678</td>  →  900101-1234568010-1234-5678
#   그러면 검출기의 경계 검사(앞뒤가 숫자면 거부)에 걸려 두 값이 **함께 사라진다**.
#   [실측] PII 가 표로 빼곡한 문서 하나에서 사이냅 대비 검출이 37% 적었다 — 추출
#   글자수는 5.5% 차이뿐이라 '덜 뽑아서'가 아니라 '이어붙여서'였다(2026-09-04).
_BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2", "h3", "h4",
          "h5", "h6", "ul", "ol", "section", "article", "header", "footer", "hr"}


#------------------------------------------------------------------
# 태그 제거 파서(내부)
#=> HTMLParser 를 상속해, script/style 구간은 건너뛰고 나머지 데이터만 모은다.
#   블록 태그 경계에는 개행을 넣어 문단이 붙지 않게 한다.
#
# -필드: parts = 수집한 텍스트 조각 리스트
# -필드: _skip = 버릴 태그(script/style) 중첩 깊이
#------------------------------------------------------------------
class _Collector(HTMLParser):
    #------------------------------------------------------------------
    # 생성자 — 엔티티 자동복원 켜고 상태 초기화
    # -in: 없음
    # -out: 없음
    #------------------------------------------------------------------
    def __init__(self):
        # convert_charrefs=True → &amp; 등 엔티티가 handle_data 에서 이미 복원돼 온다.
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    #------------------------------------------------------------------
    # 시작 태그 처리 — 버릴 태그면 깊이 증가, 블록이면 개행
    # -in: tag=태그명, attrs=(무시)
    # -out: 없음
    #------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in _DROP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    #------------------------------------------------------------------
    # 끝 태그 처리 — 버릴 태그면 깊이 감소, 블록이면 개행
    # -in: tag=태그명
    # -out: 없음
    #------------------------------------------------------------------
    def handle_endtag(self, tag):
        if tag in _DROP and self._skip > 0:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    #------------------------------------------------------------------
    # 텍스트 데이터 — 버리는 구간이 아니면 수집
    # -in: data=태그 사이 텍스트
    # -out: 없음
    #------------------------------------------------------------------
    def handle_data(self, data):
        if self._skip == 0:
            self.parts.append(data)


class HtmlTextExtractor(TextExtractor):
    #------------------------------------------------------------------
    # HTML → 텍스트 (핵심)
    #=> HTML 을 인코딩 맞춰 읽고, 태그를 벗겨 본문만 돌려준다.
    #    1) 바이트로 읽어 utf-8-sig→utf-8→cp949→latin-1 순으로 디코드
    #    2) _Collector 로 파싱(script/style 제거, 블록 개행)
    #    3) 조각을 합쳐 반환(정제는 상위 clean_text 담당)
    #   ※ save_dir 저장은 상위(HybridExtractor)가 처리한다.
    #
    # -in: input_path = .html/.htm 경로
    # -in: save_dir   = (호환용) 사용 안 함
    #
    # -out: text = 태그 제거된 본문 텍스트
    # -out: error = 읽기 실패 시 ExtractError(파싱 실패는 가능한 만큼 반환)
    #------------------------------------------------------------------
    def extract(self, input_path, save_dir=None):
        try:
            with open(input_path, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise ExtractError(f"HTML 읽기 실패: {type(e).__name__}")
        # 인코딩을 앞에서부터 엄격 시도, 다 실패하면 latin-1 로 손실없이.
        text = None
        for enc in _ENCODINGS[:-1]:
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("latin-1", errors="replace")

        parser = _Collector()
        try:
            parser.feed(text)
            parser.close()
        except Exception:   # noqa: BLE001  (깨진 HTML 이라도 모은 데까진 살린다)
            pass
        return "".join(parser.parts)
