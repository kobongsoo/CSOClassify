#------------------------------------------------------------------
# ONNX 모델 동적 int8 양자화 (개발용, exe 경량화)
#=> export_onnx.py 가 만든 fp32 model.onnx(약 470MB)를 int8 로 동적 양자화해
#   1/4 수준(~120MB)으로 줄인다. CPU 추론 속도도 대개 좋아지고, 임베딩 품질
#   손실은 작다. 원본 fp32 는 model.fp32.onnx 로 백업해 언제든 되돌릴 수 있다.
#
#   사용:  python scripts/quantize_onnx.py --model e5-small-ko
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
# 모델 1개를 int8 로 양자화 (핵심)
#=> 현재 model.onnx(fp32)를 백업하고, 그 백업을 원본으로 삼아 동적 양자화 결과를
#   다시 model.onnx 로 쓴다(런타임은 항상 model.onnx 를 로드하므로 교체만 하면 됨).
#    1) 별칭 → 폴더 결정, model.onnx 존재 확인
#    2) 최초 1회면 model.onnx → model.fp32.onnx 로 백업(이미 있으면 그대로 사용)
#    3) quantize_dynamic(fp32백업 → model.onnx, weight=QInt8)
#    4) 크기 비교 출력
#
# -in: model_key = config.MODELS 별칭(예: "e5-small-ko")
#
# -out: (fp32_path, int8_path) = 백업 경로, 양자화 결과 경로
# -out: error = onnxruntime 미설치/모델없음 시 SystemExit
#------------------------------------------------------------------
def quantize_model(model_key):
    spec = config.get_model_spec(model_key)
    mdir = resources.model_dir(spec.local_dir)
    onnx_path = os.path.join(mdir, "model.onnx")
    fp32_path = os.path.join(mdir, "model.fp32.onnx")

    # (1) 대상 확인.
    if not os.path.isfile(onnx_path) and not os.path.isfile(fp32_path):
        print(f"[quantize] model.onnx 없음: {onnx_path} (먼저 export_onnx.py 실행)", file=sys.stderr)
        raise SystemExit(2)

    # 양자화 도구는 onnxruntime 에 포함. 없으면 안내 후 종료.
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
    except ImportError:
        print("[quantize] onnxruntime(quantization) 이 필요합니다: pip install onnxruntime", file=sys.stderr)
        raise SystemExit(2)

    # (2) fp32 백업 확보: 아직 백업이 없으면 현재 model.onnx 를 fp32 원본으로 보존.
    if not os.path.isfile(fp32_path):
        os.replace(onnx_path, fp32_path)
    src_size = os.path.getsize(fp32_path)

    print(f"[quantize] {fp32_path} → {onnx_path} (int8 동적 양자화)")

    # (3) 동적 양자화: 가중치를 int8 로. MatMul 가중치 축소가 핵심이라 CPU 에 적합.
    quantize_dynamic(
        model_input=fp32_path,
        model_output=onnx_path,
        weight_type=QuantType.QInt8,
    )

    # (4) 크기 비교로 효과를 눈으로 확인.
    dst_size = os.path.getsize(onnx_path)
    print(f"[quantize] 완료: {src_size/1e6:.1f}MB(fp32) → {dst_size/1e6:.1f}MB(int8) "
          f"= {dst_size/src_size*100:.0f}%")
    return fp32_path, onnx_path


#------------------------------------------------------------------
# 스크립트 진입점
#=> --model 별칭을 quantize_model 에 넘긴다.
#
# -in: 없음(argparse 로 --model 수집)
#
# -out: 없음
# -out: error = 하위 SystemExit 전파
#------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="ONNX 모델 int8 양자화(개발용)")
    p.add_argument("--model", default=config.DEFAULT_MODEL,
                   help=f"모델 별칭(기본 {config.DEFAULT_MODEL})")
    args = p.parse_args()
    quantize_model(args.model)


if __name__ == "__main__":
    main()
