#------------------------------------------------------------------
# 모델 폴더 → .tar.xz 압축 (A안 외부 모델 배포용)
#=> resources/models/<local_dir> 의 실사용 파일(model.onnx, tokenizer.json 등)을
#   하나의 <local_dir>.tar.xz 로 묶는다. exe 옆에 이 파일 하나만 두면, 런타임이
#   최초 1회 캐시에 풀어 사용한다(resources.resolve_model_dir). 백업본은 제외.
#
#   사용:  python scripts/pack_model.py --model e5-small-ko
#          → dist-model/e5-small-ko.tar.xz 생성
#------------------------------------------------------------------

import argparse
import os
import sys
import tarfile

# 프로젝트 소스를 import 경로에 추가.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from csoclassify import config       # noqa: E402
from csoclassify import resources    # noqa: E402

# 압축에서 제외할 백업/대체본(용량만 키움).
_EXCLUDE_SUFFIX = (".fp32.onnx", ".fp16.onnx")


#------------------------------------------------------------------
# 모델 폴더를 tar.xz 로 묶기 (핵심)
#=> 폴더 안의 파일을 아카이브 "루트"에 담는다(arcname=파일명). 런타임 해제 코드가
#   루트에서 바로 model.onnx 를 찾도록 하기 위함이다.
#    1) 소스 모델 폴더 확인
#    2) dist-model/ 아래 <local_dir>.tar.xz 로 xz(preset6) 압축
#    3) 원본/압축 크기 비교 출력
#
# -in: model_key = config.MODELS 별칭(예: "e5-small-ko")
# -in: preset    = xz 압축 강도(0~9, 기본 6)
#
# -out: out = 생성된 .tar.xz 경로
# -out: error = 모델 폴더/파일 없으면 SystemExit
#------------------------------------------------------------------
def pack_model(model_key, preset=6):
    spec = config.get_model_spec(model_key)
    mdir = resources.model_dir(spec.local_dir)
    if not os.path.isfile(os.path.join(mdir, "model.onnx")):
        print(f"[pack] model.onnx 없음: {mdir} (먼저 export_onnx.py/quantize_onnx.py 실행)", file=sys.stderr)
        raise SystemExit(2)

    out_dir = os.path.join(resources._base_dir(), "dist-model")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{spec.local_dir}.tar.xz")

    # 담을 파일 목록(백업 제외)과 원본 총 크기.
    files = [fn for fn in os.listdir(mdir)
             if os.path.isfile(os.path.join(mdir, fn)) and not fn.endswith(_EXCLUDE_SUFFIX)]
    raw = sum(os.path.getsize(os.path.join(mdir, fn)) for fn in files)

    print(f"[pack] {mdir} → {out} (xz preset={preset}, 파일 {len(files)}개)")
    # xz 스트림으로 tar 를 감싸 한 파일로 만든다. arcname=fn 으로 루트에 평평하게 담음.
    with tarfile.open(out, "w:xz", preset=preset) as tf:
        for fn in files:
            tf.add(os.path.join(mdir, fn), arcname=fn)

    comp = os.path.getsize(out)
    print(f"[pack] 완료: {raw/1e6:.1f}MB → {comp/1e6:.1f}MB ({comp/raw*100:.0f}%)")
    return out


#------------------------------------------------------------------
# 스크립트 진입점
#=> --model 별칭을 pack_model 에 넘긴다.
#
# -in: 없음(argparse 로 --model/--preset 수집)
#
# -out: 없음
# -out: error = 하위 SystemExit 전파
#------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="모델 폴더 → .tar.xz 압축(A안 외부 배포용)")
    p.add_argument("--model", default=config.DEFAULT_MODEL, help=f"모델 별칭(기본 {config.DEFAULT_MODEL})")
    p.add_argument("--preset", type=int, default=6, help="xz 압축 강도 0~9(기본 6)")
    args = p.parse_args()
    pack_model(args.model, preset=args.preset)


if __name__ == "__main__":
    main()
