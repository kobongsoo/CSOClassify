#------------------------------------------------------------------
# HuggingFace 임베딩 모델 → ONNX 변환 (개발용, 런타임 제외)
#=> 기본 모델(dragonkue/multilingual-e5-small-ko 등)을 내려받아 ONNX 로 바꾸고,
#   tokenizer.json 과 함께 resources/models/<local_dir>/ 에 저장한다. 이 산출물을
#   exe 에 번들하면 런타임에는 onnxruntime+tokenizers 만으로 완전 오프라인 동작한다.
#
#   ※ 이 스크립트는 개발 PC에서 1회만 실행한다(인터넷 필요). torch/optimum/
#     transformers 는 여기서만 쓰고 최종 exe 에는 넣지 않는다.
#
#   사용:  python scripts/export_onnx.py --model e5-small-ko
#------------------------------------------------------------------

import argparse
import os
import sys

# 프로젝트 소스를 import 경로에 추가(설정/경로 상수 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from csoclassify import config       # noqa: E402
from csoclassify import resources    # noqa: E402


#------------------------------------------------------------------
# 모델 1개를 ONNX 로 변환·저장 (핵심)
#=> optimum 의 ORTModelForFeatureExtraction 으로 export 하고, 토크나이저를 함께 저장한다.
#    1) 별칭 → 스펙 조회로 hf_id/출력폴더 결정
#    2) optimum 으로 export=True 하여 model.onnx 생성·저장
#    3) AutoTokenizer 로 tokenizer.json 저장(fast 토크나이저 필요)
#
# -in: model_key = config.MODELS 의 별칭(예: "e5-small-ko")
#
# -out: out_dir = 산출물이 저장된 폴더 경로
# -out: error = optimum/transformers 미설치 시 안내 후 SystemExit
#------------------------------------------------------------------
def export_model(model_key):
    spec = config.get_model_spec(model_key)
    out_dir = resources.model_dir(spec.local_dir)
    os.makedirs(out_dir, exist_ok=True)

    # 무거운 개발 의존성은 여기서만 import — 없으면 설치법을 안내하고 종료.
    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
    except ImportError:
        print("[export] optimum/transformers 가 필요합니다:", file=sys.stderr)
        print('        pip install "optimum[onnxruntime]" transformers', file=sys.stderr)
        raise SystemExit(2)

    print(f"[export] {spec.hf_id} → {out_dir} (ONNX 변환 시작)")

    # (2) export=True 로 즉시 ONNX 변환 후 저장 → out_dir/model.onnx 생성.
    model = ORTModelForFeatureExtraction.from_pretrained(spec.hf_id, export=True)
    model.save_pretrained(out_dir)

    # (3) fast 토크나이저를 tokenizer.json 형태로 저장(런타임 tokenizers 라이브러리가 읽음).
    tok = AutoTokenizer.from_pretrained(spec.hf_id)
    tok.save_pretrained(out_dir)

    # onnxruntime 임베더는 tokenizer.json 을 직접 읽으므로 존재를 확인해 알려 준다.
    tok_json = os.path.join(out_dir, "tokenizer.json")
    onnx_file = os.path.join(out_dir, "model.onnx")
    print(f"[export] 완료: model.onnx={'OK' if os.path.isfile(onnx_file) else '없음'}, "
          f"tokenizer.json={'OK' if os.path.isfile(tok_json) else '없음'}")
    return out_dir


#------------------------------------------------------------------
# 스크립트 진입점
#=> --model 로 받은 별칭을 export_model 에 넘긴다.
#
# -in: 없음(argparse 로 --model 수집)
#
# -out: 없음
# -out: error = 없음(하위 호출의 SystemExit 전파)
#------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="임베딩 모델 → ONNX 변환(개발용)")
    p.add_argument("--model", default=config.DEFAULT_MODEL,
                   help=f"모델 별칭(기본 {config.DEFAULT_MODEL})")
    args = p.parse_args()
    export_model(args.model)


if __name__ == "__main__":
    main()
