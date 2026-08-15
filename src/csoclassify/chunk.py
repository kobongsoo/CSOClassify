#------------------------------------------------------------------
# 토큰 기준 청킹 (순수 로직)
#=> 긴 문서는 모델 최대 토큰을 넘으므로 여러 조각(청크)으로 나눈다. 여기서는
#   토크나이저에 의존하지 않는 "정수 인덱스 계산"만 담당해 단위 테스트가 쉽다.
#   실제 토크나이즈는 embed 단계가 이 인덱스를 받아 수행한다.
#------------------------------------------------------------------


#------------------------------------------------------------------
# 슬라이딩 윈도우 경계 계산 (핵심)
#=> 길이 seq_len 인 토큰열을 (max_tokens 크기, overlap 만큼 겹침)으로 잘랐을 때의
#   [start, end) 구간 목록을 만든다. 벡터가 문맥을 놓치지 않게 조각을 겹친다.
#    1) step = max_tokens - overlap 만큼 창을 전진
#    2) 마지막 창이 끝에 닿으면 종료(중복 꼬리 생성 방지)
#
# -in: seq_len    = 전체 토큰 개수
# -in: max_tokens = 한 청크 최대 토큰 수(>0)
# -in: overlap    = 인접 청크 겹침 토큰 수(0 <= overlap < max_tokens)
#
# -out: spans = [(start, end), ...] 구간 리스트 (seq_len==0 이면 빈 리스트)
# -out: error = max_tokens<=0 또는 overlap>=max_tokens 면 ValueError
#------------------------------------------------------------------
def sliding_windows(seq_len, max_tokens, overlap):
    # 잘못된 설정은 무한루프/음수 스텝을 유발하므로 즉시 막는다.
    if max_tokens <= 0:
        raise ValueError("max_tokens 는 1 이상이어야 함")
    if overlap < 0 or overlap >= max_tokens:
        raise ValueError("overlap 은 0 이상 max_tokens 미만이어야 함")

    # 내용이 없으면 청크도 없다.
    if seq_len <= 0:
        return []

    # 전체가 한 청크에 들어가면 그대로 하나만 반환(불필요한 분할 방지).
    if seq_len <= max_tokens:
        return [(0, seq_len)]

    step = max_tokens - overlap  # 창이 매번 전진하는 칸 수
    spans = []
    start = 0
    while start < seq_len:
        end = min(start + max_tokens, seq_len)
        spans.append((start, end))
        # 이번 창이 끝에 닿았으면 더 만들 필요가 없다.
        if end >= seq_len:
            break
        start += step
    return spans


#------------------------------------------------------------------
# 토큰 id 리스트를 청크들로 분할
#=> 실제 토큰 id 배열을 위 sliding_windows 경계로 잘라 청크별 id 리스트를 만든다.
#
# -in: ids        = 토큰 id 정수 리스트
# -in: max_tokens = 한 청크 최대 토큰 수
# -in: overlap    = 겹침 토큰 수
#
# -out: chunks = [[id,...], ...] 청크별 토큰 id 리스트 (비었으면 [])
# -out: error = 설정 오류 시 sliding_windows 가 ValueError 전파
#------------------------------------------------------------------
def chunk_ids(ids, max_tokens, overlap):
    spans = sliding_windows(len(ids), max_tokens, overlap)
    return [ids[s:e] for (s, e) in spans]
