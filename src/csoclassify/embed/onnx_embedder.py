#------------------------------------------------------------------
# ONNX + onnxruntime 임베더 (설계서 결정2/결정4)
#=> 작은 임베딩 모델을 ONNX 로 돌려 CPU 에서 벡터를 만든다. PyTorch 를 안 쓰므로
#   exe 가 가볍다. onnxruntime/tokenizers/numpy 는 무거운 의존성이라 import 를
#   ensure_loaded() 안으로 미뤄(지연 로딩), 이 모듈 자체는 가볍게 import 되게 한다.
#------------------------------------------------------------------

import os
import threading
import time

from .. import resources
from ..chunk import chunk_ids
from ..logsetup import get_logger
from .base import Embedder, EmbedError

log = get_logger(__name__)


class OnnxEmbedder(Embedder):
    #------------------------------------------------------------------
    # 생성자 — 설정만 보관(모델은 아직 로드 안 함)
    #=> 어떤 모델을, 어떤 청킹 기본값으로 쓸지 기억만 한다. 실제 무거운 로딩은
    #   ensure_loaded() 로 미뤄 시작을 빠르게 한다.
    #    1) 세션/토크나이저 자리는 None 으로 비워 둠
    #    2) 여러 스레드가 동시에 로드/추론해도 안전하도록 락 준비
    #
    # -in: spec        = config.ModelSpec (모델 폴더/차원/프리픽스 정보)
    # -in: num_threads = onnxruntime intra-op 스레드 수(None 이면 기본)
    #
    # -out: 없음
    # -out: error = 없음(생성 시점엔 파일 접근 안 함)
    #------------------------------------------------------------------
    def __init__(self, spec, num_threads=None):
        self.spec = spec
        self.num_threads = num_threads
        self._session = None       # onnxruntime InferenceSession
        self._tokenizer = None     # tokenizers.Tokenizer
        self._input_names = None   # 모델이 요구하는 입력 이름 집합
        self._load_lock = threading.Lock()   # 로딩 중복 방지
        self._infer_lock = threading.Lock()  # 추론 직렬화(세션 공유 안전)

    #------------------------------------------------------------------
    # 로드 여부 확인
    #=> 세션과 토크나이저가 둘 다 준비됐는지 알려 준다(데몬 헬스 응답에 사용).
    #
    # -in: 없음
    #
    # -out: bool = 준비됐으면 True
    # -out: error = 없음
    #------------------------------------------------------------------
    def is_ready(self):
        return self._session is not None and self._tokenizer is not None

    #------------------------------------------------------------------
    # 모델 적재 보장(지연 로딩) — 핵심
    #=> 처음 호출 때만 tokenizer.json 과 model.onnx 를 메모리에 올린다. 락으로
    #   감싸 두 스레드가 동시에 들어와도 실제 로딩은 한 번만 일어나게 한다.
    #    1) 이미 준비돼 있으면 0.0 반환(중복 로드 방지)
    #    2) 무거운 라이브러리를 이 시점에 import
    #    3) 토크나이저 → onnxruntime 세션 순으로 로드하고 걸린 시간 측정
    #
    # -in: 없음
    #
    # -out: load_ms = 실제 로딩 밀리초(이미 로드면 0.0)
    # -out: error = 파일 없음/라이브러리 없음/세션 생성 실패 시 EmbedError
    #------------------------------------------------------------------
    def ensure_loaded(self):
        # 빠른 경로: 이미 준비됐으면 락 없이 즉시 반환.
        if self.is_ready():
            return 0.0

        with self._load_lock:
            # 락 대기 중 다른 스레드가 이미 로드했을 수 있으니 재확인.
            if self.is_ready():
                return 0.0

            t0 = time.perf_counter()

            # A안: 캐시/exe옆/env/번들 순으로 모델 폴더를 찾고, 없으면 .tar.xz 를
            # 캐시에 1회 해제한 경로를 돌려준다(외부 모델 배포 지원).
            mdir = resources.resolve_model_dir(self.spec.local_dir)
            onnx_path = os.path.join(mdir, "model.onnx")
            tok_path = os.path.join(mdir, "tokenizer.json")

            # 파일이 없으면 원인을 분명히 알려 준다(모델 미번들/미배치 흔한 실수).
            if not os.path.isfile(onnx_path):
                raise EmbedError(
                    f"model.onnx 없음: {onnx_path} "
                    f"(export_onnx.py 로 생성하거나, 배포 시 exe 옆에 "
                    f"{self.spec.local_dir}/ 폴더 또는 {self.spec.local_dir}.tar.xz 배치)")
            if not os.path.isfile(tok_path):
                raise EmbedError(f"tokenizer.json 없음: {tok_path}")

            # (2) 무거운 의존성은 여기서 import — 미설치 시 친절한 에러로 바꾼다.
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as e:
                raise EmbedError(f"필수 라이브러리 미설치(onnxruntime/tokenizers): {e}")

            #--------------------------------------------------------
            # (3) 토크나이저 로드.
            #--------------------------------------------------------
            try:
                self._tokenizer = Tokenizer.from_file(tok_path)
            except Exception as e:
                raise EmbedError(f"tokenizer 로딩 실패: {e}")

            # onnxruntime 세션 옵션: CPU 스레드 수를 상황에 맞게 제한 가능.
            so = ort.SessionOptions()
            if self.num_threads:
                so.intra_op_num_threads = int(self.num_threads)
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            #--------------------------------------------------------
            # (4) 임베딩 모델 로드.
            # => ort.InferenceSession 호출함으로써 모델 로딩됨.
            # => CPUExecutionProvider 로 CPU로 사용 전제 임. GPU있어도 안씀.
            #
            # GPU로 바꾸려면 (참고)
            # => providers=["CUDAExecutionProvider", "CPUExecutionProvider"]   # CUDA 실패 시 CPU로 폴백
            # 윈도우 범용이면 ["DmlExecutionProvider", "CPUExecutionProvider"] (DirectML)
            # *GPU를 쓰려면 onnxruntime-gpu 패키지 + CUDA/cuDNN이 필요.
            #--------------------------------------------------------
            try:
                self._session = ort.InferenceSession(
                    onnx_path, sess_options=so, providers=["CPUExecutionProvider"]
                )
            except Exception as e:
                raise EmbedError(f"onnxruntime 세션 생성 실패: {e}")

            # 모델이 실제로 요구하는 입력 이름을 기억(불필요한 입력을 안 넣기 위함).
            self._input_names = {i.name for i in self._session.get_inputs()}

            load_ms = (time.perf_counter() - t0) * 1000.0
            # 모델을 어디서 얼마 만에 올렸는지 남긴다(콜드스타트 원인 추적).
            log.info("모델 로드 완료 dir=%s ms=%.0f inputs=%s", mdir, load_ms, sorted(self._input_names))
            return load_ms

    #------------------------------------------------------------------
    # 텍스트 → 청크 문자열 목록
    #=> 프리픽스를 붙이기 전에 본문을 토큰 단위로 잘라 여러 청크 문자열로 만든다.
    #    1) 특수토큰 없이 토크나이즈해 본문 토큰 id 확보
    #    2) (모델 상한, 프리픽스, 특수토큰 2개)을 감안한 유효 길이로 청킹
    #    3) 각 청크 id 를 다시 텍스트로 디코드(임베딩 단계에서 프리픽스+특수토큰 재부착)
    #
    # -in: text       = 정제된 문서 텍스트
    # -in: max_tokens = 사용자가 준 청크 최대 토큰
    # -in: overlap    = 겹침 토큰
    #
    # -out: chunks = 청크 텍스트 리스트(내용 없으면 [])
    # -out: error = 없음(토크나이저 미로드 상태로 부르면 AttributeError 이나, 내부에서만 호출)
    #------------------------------------------------------------------
    def _split_chunks(self, text, max_tokens, overlap):
        # 특수토큰(add_special_tokens=False) 없이 순수 본문 토큰만 얻는다.
        enc = self._tokenizer.encode(text, add_special_tokens=False)
        ids = enc.ids
        if not ids:
            return []

        # 프리픽스와 [CLS]/[SEP] 2개가 차지할 자리를 빼서 실제 본문 예산을 정한다.
        prefix = self.spec.passage_prefix
        prefix_len = len(self._tokenizer.encode(prefix, add_special_tokens=False).ids) if prefix else 0
        # 모델 상한을 넘지 않도록 사용자 값과 비교해 더 작은 쪽을 택한다.
        hard_cap = min(max_tokens, self.spec.max_tokens_model)
        effective = max(8, hard_cap - prefix_len - 2)  # 최소 8 토큰은 보장

        # overlap 이 유효길이 이상이면 무한루프가 나므로 안전하게 줄인다.
        eff_overlap = min(overlap, effective - 1)

        chunks = []
        for cid in chunk_ids(ids, effective, eff_overlap):
            # 청크 토큰을 텍스트로 되돌린다 → 다음 단계에서 프리픽스+특수토큰 재부착.
            chunks.append(self._tokenizer.decode(cid))
        return chunks

    #------------------------------------------------------------------
    # 청크 문자열 하나 → 벡터
    #=> 프리픽스를 붙여 정식 토크나이즈(특수토큰 포함)하고 모델을 돌린 뒤,
    #   attention mask 를 반영한 평균 풀링으로 문장 벡터를 만든다.
    #    1) 입력 텐서 구성(input_ids/attention_mask/필요시 token_type_ids)
    #    2) onnxruntime 추론 → last_hidden_state
    #    3) 마스크 가중 평균으로 (dim,) 벡터 산출
    #
    # -in: chunk_text = 청크 텍스트(프리픽스 미포함)
    #
    # -out: vec = numpy 1차원 배열(shape=(dim,))
    # -out: error = 추론 실패 시 예외 전파(상위에서 EmbedError 로 감쌈)
    #------------------------------------------------------------------
    def _embed_one(self, chunk_text):
        import numpy as np

        # e5 규약: 문서는 'passage: ' 프리픽스를 붙여야 성능이 제대로 난다.
        full = (self.spec.passage_prefix or "") + chunk_text
        enc = self._tokenizer.encode(full, add_special_tokens=True)

        # 모델 상한을 넘으면 안전하게 앞부분만 사용(청킹으로 대부분 방지되지만 이중 안전).
        ids = enc.ids[: self.spec.max_tokens_model]
        attn = enc.attention_mask[: self.spec.max_tokens_model]

        input_ids = np.array([ids], dtype=np.int64)
        attention = np.array([attn], dtype=np.int64)

        # 모델이 요구하는 입력만 골라 넣는다(모델마다 token_type_ids 유무가 다름).
        feeds = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        # 추론은 세션 공유 안전을 위해 직렬화(락)한다.
        with self._infer_lock:
            outputs = self._session.run(None, feeds)

        # 첫 출력이 last_hidden_state (1, seq, dim) 라고 가정하고 평균 풀링.
        last_hidden = outputs[0][0]                 # (seq, dim)
        mask = attention[0].astype(np.float32)      # (seq,)
        denom = max(mask.sum(), 1e-9)               # 0 나눗셈 방지
        vec = (last_hidden * mask[:, None]).sum(axis=0) / denom
        return vec.astype(np.float32)

    #------------------------------------------------------------------
    # 문서 텍스트 → 벡터 (핵심)
    #=> 텍스트를 청킹해 각 청크를 임베딩하고, 기본은 청크 평균으로 문서 1벡터를
    #   만든다. per_chunk 면 청크별 벡터를 그대로 돌려준다.
    #    1) 로드 보장 → 청킹
    #    2) 청크마다 _embed_one
    #    3) (문서벡터면) 평균 → (옵션) L2 정규화
    #
    # -in: text      = 정제된 문서 텍스트
    # -in: max_tokens= 청크 최대 토큰
    # -in: overlap   = 청크 겹침 토큰
    # -in: normalize = True 면 L2 정규화
    # -in: per_chunk = True 면 청크별 벡터 리스트 반환
    #
    # -out: (result, n_chunks) = 문서벡터 list[float] 또는 청크벡터 list[list[float]], 청크 수
    # -out: error = 빈 텍스트/추론 실패 시 EmbedError
    #------------------------------------------------------------------
    def embed_document(self, text, max_tokens, overlap, normalize=True, per_chunk=False):
        import numpy as np

        self.ensure_loaded()

        # 정제 후에도 비어 있으면 만들 벡터가 없다 → 명확히 실패.
        if not text or not text.strip():
            raise EmbedError("임베딩할 텍스트가 비어 있음")

        chunks = self._split_chunks(text, max_tokens, overlap)
        if not chunks:
            raise EmbedError("청킹 결과가 비어 있음")

        try:
            vecs = [self._embed_one(c) for c in chunks]
        except Exception as e:
            raise EmbedError(f"추론 실패: {e}")

        mat = np.vstack(vecs)  # (n_chunks, dim)

        if per_chunk:
            # 청크별로 각각 정규화해서 반환(RAG 인덱싱 등에 그대로 사용 가능).
            out = [self._l2(v) if normalize else v for v in mat]
            return [v.tolist() for v in out], len(chunks)

        # 문서벡터: 청크 평균 후 (옵션) 정규화.
        # => 1개 평균벡터를 만듬.(실제 출력 벡터임.)
        doc = mat.mean(axis=0)

        # L2 정규화 시킴.
        if normalize:
            doc = self._l2(doc)
        return doc.tolist(), len(chunks)

    #------------------------------------------------------------------
    # L2 정규화
    #=> 벡터를 길이 1 로 만든다(코사인 유사도 사용 시 표준 전처리).
    #
    # -in: v = numpy 1차원 배열
    #
    # -out: 정규화된 배열(0벡터면 원본 그대로)
    # -out: error = 없음
    #------------------------------------------------------------------
    def _l2(self, v):
        import numpy as np
        n = np.linalg.norm(v)
        # 길이가 0 이면 나눌 수 없으니 그대로 둔다.
        return v / n if n > 1e-9 else v
