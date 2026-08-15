#------------------------------------------------------------------
# 처리 파이프라인 (추출→정제→청킹→임베딩→결과조립)
#=> 파일 하나를 받아 벡터 결과 dict 를 만드는 전체 흐름을 담는다. 데몬 서버와
#   in-process 폴백이 똑같이 이 함수를 쓰도록 해, 두 경로의 동작을 일치시킨다.
#   단계별 소요시간은 넘겨받은 Timing 객체에 기록한다(설계서 §7-1).
#------------------------------------------------------------------

import threading

from .clean import clean_text
from .embed.base import EmbedError  # noqa: F401 (하위호환용 재노출)
from .logsetup import get_logger

log = get_logger(__name__)


#------------------------------------------------------------------
# 벡터 정밀도 적용
#=> 출력 벡터를 f32(그대로) 또는 f16(반정밀도로 캐스팅) 로 만든다. f16 은 파일
#   크기를 줄이려는 용도이며, numpy 로 실제 half 캐스팅 후 다시 float 로 되돌린다.
#
# -in: vec       = float 리스트(또는 리스트들의 리스트)
# -in: precision = "f32" | "f16"
#
# -out: 같은 구조의 리스트(정밀도 반영)
# -out: error = 없음(알 수 없는 값은 f32 취급)
#------------------------------------------------------------------
def apply_precision(vec, precision):
    if precision != "f16":
        return vec
    import numpy as np
    # 중첩 리스트(per_chunk)인지 단일 벡터인지에 따라 처리.
    arr = np.asarray(vec, dtype=np.float16).astype(np.float32)
    return arr.tolist()


#------------------------------------------------------------------
# 임베딩 + 시간기록
#=> 모델 로드(별도 계측)와 임베딩 추론을 수행하고 Timing 에 model_load/embed 를
#   기록한다. 반환은 결과 dict 의 핵심부(벡터/차원/청크수)만 만든다.
#    1) ensure_loaded 로 로드시간을 model_load 로 기록(이미 로드면 0)
#    2) measure("embed") 로 추론시간 기록
#    3) precision 적용 후 vector/vectors 키로 담아 반환
#
# -in: embedder = Embedder 구현체(모델)
# -in: text     = 정제된 문서 텍스트
# -in: opts     = 처리 옵션 dict(max_tokens/overlap/normalize/per_chunk/precision)
# -in: timing   = Timing 객체(여기에 단계시간 누적)
# -in: preloaded_load_ms = 병렬 예열로 이미 잰 로드시간(없으면 None)
#
# -out: (core, n_chunks) = {dim, chunks, vector|vectors} 부분 dict, 청크 수
# -out: error = 임베딩 실패 시 EmbedError 전파
#------------------------------------------------------------------
def embed_with_timing(embedder, text, opts, timing, preloaded_load_ms=None):
    # (1) 모델 로드시간: 병렬 예열로 이미 쟀으면 그 값, 아니면 지금 로드하며 측정.
    if preloaded_load_ms is not None:
        timing.set("model_load", preloaded_load_ms)
    else:
        timing.set("model_load", embedder.ensure_loaded())

    # (2) 실제 추론 시간 측정.
    with timing.measure("embed"):
        result, n = embedder.embed_document(
            text,
            opts["max_tokens"],
            opts["overlap"],
            normalize=opts["normalize"],
            per_chunk=opts["per_chunk"],
        )

    # (3) 정밀도 적용 후 형태에 맞는 키로 담는다.
    result = apply_precision(result, opts.get("precision", "f32"))
    core = {"dim": embedder.spec.dim, "chunks": n}
    if opts["per_chunk"]:
        core["vectors"] = result
    else:
        core["vector"] = result
    return core, n


#------------------------------------------------------------------
# 파일 1개 → 결과 dict (핵심)
#=> 추출→정제→임베딩 전 과정을 수행하고 결과 dict 를 만든다. 단일파일 in-process
#   경로에서는 parallel_preload=True 로 추출과 모델 로딩을 병렬화해 콜드스타트를 가린다.
#    1) (옵션) 백그라운드로 모델 예열 시작
#    2) extract → clean (각각 시간기록)
#    3) 예열 스레드 합류 → 임베딩
#    4) elapsed_ms/total 채워 결과 반환
#
# -in: path      = 입력 문서 경로
# -in: opts      = 처리 옵션 dict
# -in: extractor = TextExtractor 구현체
# -in: embedder  = Embedder 구현체
# -in: timing    = Timing 객체
# -in: parallel_preload = True 면 추출과 모델 로딩을 병렬 실행
#
# -out: result = {file, model, dim, chunks, vector|vectors, elapsed_ms}
# -out: error = 추출/임베딩 실패 시 각 예외 전파(ExtractError/EmbedError)
#------------------------------------------------------------------
def process_file(path, opts, extractor, embedder, timing, parallel_preload=False):
    # (1) 병렬 예열: 추출이 도는 동안 다른 스레드에서 모델을 미리 올린다.
    holder = {"ms": None, "err": None}
    preload_thread = None
    if parallel_preload:
        def _preload():
            try:
                holder["ms"] = embedder.ensure_loaded()
            except Exception as e:  # 로딩 실패는 본류에서 처리하도록 저장만.
                holder["err"] = e
        preload_thread = threading.Thread(target=_preload, daemon=True)
        preload_thread.start()

    # 어떤 파일을 어떤 옵션으로 처리하는지 먼저 남긴다.
    log.info("처리 시작 file=%s max_tokens=%s overlap=%s per_chunk=%s",
             path, opts.get("max_tokens"), opts.get("overlap"), opts.get("per_chunk"))

    # (2) 추출 + 정제.
    with timing.measure("extract"):
        raw = extractor.extract(path, save_dir=opts.get("save_dir"))
    with timing.measure("clean"):
        text = clean_text(raw, remove_page_markers=not opts.get("keep_page_markers", False))
    # 추출/정제 결과 규모를 남겨(원문 길이·정제 후 길이) 문제 문서를 찾기 쉽게 한다.
    log.info("추출/정제 file=%s raw_chars=%d clean_chars=%d", path, len(raw), len(text))

    # (3) 예열 스레드가 있으면 합류시키고, 로딩 에러가 있었다면 여기서 표면화.
    preloaded_ms = None
    if preload_thread is not None:
        preload_thread.join()
        if holder["err"] is not None:
            raise holder["err"]
        preloaded_ms = holder["ms"]

    core, n_chunks = embed_with_timing(embedder, text, opts, timing, preloaded_load_ms=preloaded_ms)

    # (4) 총시간 확정 후 결과 조립.
    timing.finalize_total()
    result = {
        "file": path,
        "model": embedder.spec.hf_id,
    }
    result.update(core)
    result["elapsed_ms"] = timing.as_dict()
    # 완료 요약: 청크 수와 단계별 소요시간을 한 줄로 남긴다(성능/이상 추적용).
    log.info("처리 완료 file=%s chunks=%d times_ms=%s", path, n_chunks, timing.as_dict())
    return result
